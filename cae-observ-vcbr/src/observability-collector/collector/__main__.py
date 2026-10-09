"""Ponto de entrada do Container Apps Job — `python -m collector` (Dockerfile).

Um ciclo completo, na ordem do documento (§3, §4, §9, §12):

  1. Databricks — os três passos de `databricks.py`, por workspace ativo.
  2. ADF — sinal primário via Log Analytics (`adf.py`, atividades), mais
     pipeline run e trigger run (`adf_runs.py`). A disponibilidade de nós do
     Self-Hosted IR (`integration_runtime.py`) vem da API de métricas do
     Azure Monitor e por isso roda fora do bloco que depende do Log
     Analytics.
  3. Rede de apoio — reconciliação contra `DatabricksJobs` (`reconciliation.py`).
  4. Ausência de execução e job travado (`expectations.py`, §4.4) — roda
     depois de 1-3, para já ter as observações deste ciclo gravadas antes
     de decidir o que está ausente.
  5. Notificação — consome a outbox e envia ao Teams (`notification.py`).
     Roda depois de (4) de propósito: um incidente aberto agora pelo
     avaliador já sai notificado neste mesmo ciclo, não no próximo.
  6. Heartbeat — grava o sinal de vida do ciclo (`heartbeat.py`), sempre por
     último e sempre executado, sucesso ou falha parcial.

Um workspace ou uma origem falhando não pode impedir os demais: cada bloco
tem seu próprio try/except, e o que falhou entra na contagem de erros que o
heartbeat relata — silêncio sobre falha parcial é exatamente o que este
projeto existe para eliminar (§12).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# Os módulos deste pacote usam import flat entre si (`import constants as c`),
# o mesmo estilo de natural_key.py — para isso funcionar tanto sob
# `python -m collector` (este arquivo) quanto nos testes (que fazem o mesmo
# insert diretamente), o diretório do pacote precisa estar em sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure.identity import DefaultAzureCredential  # noqa: E402

import adf  # noqa: E402
import adf_runs  # noqa: E402
import constants as c  # noqa: E402
import databricks  # noqa: E402
import expectations  # noqa: E402
import heartbeat  # noqa: E402
import integration_runtime  # noqa: E402
import notification  # noqa: E402
import reconciliation  # noqa: E402

logger = logging.getLogger("collector")


def _credential() -> DefaultAzureCredential:
    # client_id explícito: com mais de uma identidade atribuída ao host, a
    # resolução automática é ambígua e falha em runtime, não no deploy (§6).
    return DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    started_at = time.monotonic()
    credential = _credential()
    errors = 0

    active_count = databricks_events = adf_events = gaps_filled = 0
    notifications_sent = notifications_failed = 0
    absence_incidents = stuck_incidents = 0

    with databricks.connect_sql(credential) as conn:
        workspaces = databricks.list_workspaces(conn)
        if not workspaces:
            logger.warning("tbl_cfg_workspaces sem nenhum workspace ativo")

        for workspace in workspaces:
            try:
                result = databricks.run_cycle(conn, credential, workspace)
            except Exception:
                logger.exception(
                    "workspace %s: ciclo Databricks falhou", workspace.workspace_id
                )
                errors += 1
            else:
                active_count += result.active_count
                databricks_events += result.events_observed

        log_analytics_workspace_id = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID")
        if log_analytics_workspace_id:
            try:
                adf_events = adf.run_cycle(conn, credential, log_analytics_workspace_id)
            except Exception:
                logger.exception("ciclo ADF falhou")
                errors += 1

            # Pipeline e trigger contam em `adf_events`: o heartbeat tem um
            # indicador por fonte, não por tipo de sinal do ADF.
            for label, cycle in (
                ("pipeline run", adf_runs.run_pipeline_cycle),
                ("trigger run", adf_runs.run_trigger_cycle),
            ):
                try:
                    adf_events += cycle(conn, credential, log_analytics_workspace_id)
                except Exception:
                    logger.exception("ciclo ADF de %s falhou", label)
                    errors += 1

            try:
                gaps_filled = reconciliation.run_cycle(
                    conn, credential, log_analytics_workspace_id
                )
            except Exception:
                logger.exception("ciclo de reconciliação (rede de apoio) falhou")
                errors += 1
        else:
            logger.warning(
                "LOG_ANALYTICS_WORKSPACE_ID não configurado — sinal ADF e "
                "rede de apoio do Databricks não rodam neste ciclo"
            )

        # Fora do bloco acima de propósito: a disponibilidade de nós do
        # Self-Hosted IR vem da API de métricas do Azure Monitor, não do Log
        # Analytics, então não depende de LOG_ANALYTICS_WORKSPACE_ID.
        try:
            adf_events += integration_runtime.run_cycle(conn, credential)
        except Exception:
            logger.exception("ciclo ADF de Integration Runtime falhou")
            errors += 1

        try:
            absence_incidents, stuck_incidents = expectations.run_cycle(conn)
        except Exception:
            # Ausência/job travado é detecção adicional sobre o que os
            # blocos acima já gravaram — uma falha aqui não pode impedir a
            # notificação e o heartbeat do que já foi observado no ciclo.
            logger.exception("ciclo de ausência/job travado (§4.4) falhou")
            errors += 1

        key_vault_uri = os.environ.get("KEY_VAULT_URI")
        if key_vault_uri:
            try:
                notifications_sent, notifications_failed = notification.run_cycle(
                    conn, credential, key_vault_uri
                )
            except Exception:
                logger.exception("ciclo de notificação falhou")
                errors += 1
        else:
            logger.warning("KEY_VAULT_URI não configurado — notificação não roda")

        depth = notification.outbox_depth(conn)

        heartbeat.record(
            credential,
            heartbeat.CycleStats(
                status=c.HEARTBEAT_STATUS_OK if errors == 0
                else c.HEARTBEAT_STATUS_ERROR,
                duration_seconds=round(time.monotonic() - started_at, 3),
                workspaces_processed=len(workspaces),
                active_executions=active_count,
                databricks_events=databricks_events,
                adf_events=adf_events,
                reconciliation_gaps_filled=gaps_filled,
                notifications_sent=notifications_sent,
                notifications_failed=notifications_failed,
                outbox_depth=depth,
                errors_count=errors,
                absence_incidents=absence_incidents,
                stuck_incidents=stuck_incidents,
            ),
        )

    if errors:
        # Encerra com falha para o Container Apps Job registrar a execução
        # como não bem-sucedida — o heartbeat já cobre "rodou mas não fez
        # nada"; isto cobre o alerta de métrica primário do §12, que olha
        # para o status de saída do job.
        sys.exit(1)


if __name__ == "__main__":
    main()
