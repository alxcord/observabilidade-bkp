"""Resolve cada evento observado (documento §4.3) e guarda as tabelas de
controle que `databricks.py` e `adf.py` compartilham.

A decisão de abrir, incrementar ou fechar incidente mora em
`event_resolution.py` — **não** em stored procedure — porque o coletor e o
publicador (`src/hub-publisher/`) precisam da mesma lógica, e duplicá-la
aqui seria abrir a porta para os dois divergirem em silêncio. Este módulo só reexporta `resolve_event` (para
`databricks.py`/`adf.py` continuarem chamando `resolution.resolve_event`
sem saber onde o código mora) e guarda o que é exclusivo do coletor:
`tbl_executions` (log append-only de observações) e os watermarks de
`tbl_ctl_watermark`.

`event_resolution.py` vive em `src/shared/`, fora deste projeto — o
Dockerfile copia o arquivo para dentro de `collector/` no build (ver
comentário no topo do Dockerfile), o mesmo mecanismo que `sync-expectations`
já usa para `config/executions/manual-expectations.yaml`. Rodando fora do
container (testes, dev local), o import abaixo cai no `except` e aponta
para `src/shared/` diretamente, já que o repositório inteiro está
disponível nesses dois casos.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pyodbc

import constants as c

try:
    from event_resolution import resolve_event
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
    from event_resolution import resolve_event

__all__ = ["resolve_event", "log_execution", "read_watermark", "advance_watermark"]

logger = logging.getLogger("collector.resolution")


# ---------------------------------------------------------------------------
# tbl_executions — log append-only
# ---------------------------------------------------------------------------

# INSERT condicional, não INSERT simples: a sobreposição deliberada da janela
# de recuperação (§3.1c) faz o mesmo run aparecer em mais de uma varredura
# entre ciclos, e a mesma execução pode ser vista tanto pelo passo (b) quanto
# pelo (c) dentro do mesmo ciclo. A PK (source, scope, run_id, observed_at) é
# estável entre essas repetições porque observed_at vem do started_at do run,
# não do instante da coleta — então "já existe" é a resposta certa, não erro.
_INSERT_EXECUTION_IF_NEW = """
INSERT INTO tbl_executions (source, scope, natural_key, run_id, run_name,
                             status, result_state, capture_source, observed_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
 WHERE NOT EXISTS (
     SELECT 1 FROM tbl_executions
      WHERE source = ? AND scope = ? AND run_id = ? AND observed_at = ?
 );
"""


def log_execution(
    conn: pyodbc.Connection,
    *,
    source: str,
    scope: str,
    natural_key: str,
    run_id: str,
    run_name: str | None,
    status: str,
    result_state: str | None,
    capture_source: str,
    observed_at: datetime,
) -> bool:
    """Registra a observação em tbl_executions. Devolve False se a mesma
    observação já tinha sido gravada — sinal para o chamador não repetir a
    chamada a `resolve_event` (que não tem outra defesa de idempotência
    para eventos sem `source_event_id`, o caso de tudo que vem do Databricks
    e do ADF direto — só o que atravessa `hub_publisher` carrega hash)."""
    cursor = conn.cursor()
    cursor.execute(
        _INSERT_EXECUTION_IF_NEW,
        source, scope, natural_key, run_id, run_name, status, result_state,
        capture_source, observed_at,
        source, scope, run_id, observed_at,
    )
    is_new = cursor.rowcount > 0
    conn.commit()
    return is_new


# ---------------------------------------------------------------------------
# tbl_ctl_watermark
# ---------------------------------------------------------------------------

def read_watermark(conn: pyodbc.Connection, key: str) -> datetime:
    row = conn.cursor().execute(
        "SELECT watermark FROM tbl_ctl_watermark WHERE source = ?", key
    ).fetchone()
    if row is None:
        logger.info("sem watermark para '%s'; usando época", key)
        return datetime.fromisoformat(c.EPOCH_WATERMARK)
    return row.watermark


_UPSERT_WATERMARK = """
MERGE tbl_ctl_watermark AS target
USING (SELECT ? AS source, ? AS watermark) AS src
   ON target.source = src.source
WHEN MATCHED THEN
  UPDATE SET watermark = src.watermark, updated_at = SYSDATETIMEOFFSET()
WHEN NOT MATCHED THEN
  INSERT (source, watermark) VALUES (src.source, src.watermark);
"""


def advance_watermark(conn: pyodbc.Connection, key: str, watermark: datetime) -> None:
    conn.cursor().execute(_UPSERT_WATERMARK, key, watermark)
    conn.commit()
