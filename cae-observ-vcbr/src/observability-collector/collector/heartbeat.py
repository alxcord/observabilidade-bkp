"""Grava o sinal de vida ao fim de cada ciclo bem-sucedido (documento §12).

Existe porque, neste desenho, "o coletor não fala" é indistinguível de
"está tudo bem" até o painel operacional ficar velho — e a solução inteira
existe para acabar com falha silenciosa. Cobre o que um alerta de métrica
sobre o Container Apps Job **não** vê: o processo rodou até o fim, terminou
aparentemente bem, mas não fez trabalho nenhum — zero workspaces lidos,
watermark parado, toda chamada de API falhando.

Vai para a tabela custom `Observabilidade_Heartbeat_CL`, pela **Logs
Ingestion API** (Data Collection Endpoint + Data Collection Rule) — não a
Data Collector API clássica, que exige chave compartilhada e violaria a
exigência de "sem segredo de longa duração" do §6.

  PRÉ-REQUISITO FORA DESTE REPOSITÓRIO: a DCE e a DCR, com a tabela destino
  e o mapeamento de coluna já criados, e a Managed Identity do coletor com
  a role `Monitoring Metrics Publisher` atribuída na DCR — nenhum dos dois
  é recurso do Bicep deste projeto (mesma fronteira que já existe para os
  workspaces Databricks de `tbl_cfg_workspaces`, e para o SQL Warehouse
  serverless de `hub-replication`, §11). Sem eles configurados via
  `LOG_ANALYTICS_INGESTION_ENDPOINT`/`LOG_ANALYTICS_DCR_IMMUTABLE_ID`, este
  módulo loga um aviso e não grava nada — o ciclo inteiro não pode falhar
  por causa do sinal que só existe para relatar se o ciclo falhou.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx
from azure.identity import DefaultAzureCredential

import constants as c

logger = logging.getLogger("collector.heartbeat")


@dataclass(frozen=True)
class CycleStats:
    """Os indicadores mínimos listados no §12 do documento: último sucesso
    (implícito no TimeGenerated do registro), workspaces processados,
    execuções ativas, eventos por fonte, gaps preenchidos pela rede de
    apoio, notificações e profundidade da outbox."""

    status: str  # OK | ERROR
    duration_seconds: float
    workspaces_processed: int
    active_executions: int
    databricks_events: int
    adf_events: int
    reconciliation_gaps_filled: int
    notifications_sent: int
    notifications_failed: int
    outbox_depth: int
    errors_count: int
    #: Abertos pelo avaliador de ausência/job travado (§4.4, expectations.py).
    #: Default 0 para não quebrar quem já constrói CycleStats sem eles.
    absence_incidents: int = 0
    stuck_incidents: int = 0


def _pascal_case(snake_case: str) -> str:
    """CycleStats usa snake_case, como o resto do módulo; a tabela custom do
    Log Analytics segue PascalCase, como toda coluna do Azure Monitor."""
    return "".join(part.capitalize() for part in snake_case.split("_"))


def record(credential: DefaultAzureCredential, stats: CycleStats) -> None:
    endpoint = os.environ.get("LOG_ANALYTICS_INGESTION_ENDPOINT")
    dcr_immutable_id = os.environ.get("LOG_ANALYTICS_DCR_IMMUTABLE_ID")
    if not endpoint or not dcr_immutable_id:
        logger.warning(
            "LOG_ANALYTICS_INGESTION_ENDPOINT/LOG_ANALYTICS_DCR_IMMUTABLE_ID "
            "não configurados — heartbeat não gravado neste ciclo"
        )
        return

    record_payload = {
        "TimeGenerated": datetime.now(timezone.utc).isoformat(),
        **{_pascal_case(field): value for field, value in asdict(stats).items()},
    }

    try:
        token = credential.get_token(c.HEARTBEAT_MONITOR_SCOPE).token
        url = (
            f"{endpoint.rstrip('/')}/dataCollectionRules/{dcr_immutable_id}"
            f"/streams/{c.HEARTBEAT_STREAM_NAME}"
        )
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                params={"api-version": "2023-01-01"},
                json=[record_payload],
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
    except Exception:
        # O heartbeat que falha não pode derrubar o ciclo que ele só
        # relata — mas precisa aparecer em log com destaque, porque é
        # exatamente o canal que cobre "o coletor não fez trabalho nenhum".
        logger.exception("falha ao gravar heartbeat")
    else:
        logger.info("heartbeat gravado: status=%s", stats.status)
