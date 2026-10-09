"""Consome `vw_pending_notifications` e envia ao Teams (documento §9).

Padrão outbox: a intenção de notificar já foi gravada, na mesma transação
da mudança de estado, por `resolution.resolve_event` (`event_resolution.resolve_event`, em Python).
Este módulo só consome o que está pendente e marca `sent_at` **depois** da
resposta 2xx — nunca antes. Inverter a ordem (marcar e só então disparar)
faz uma queda no meio perder o alerta; disparar e só então marcar faz uma
queda no meio duplicá-lo. `sent_at IS NULL` é o que garante que um reenvio
no ciclo seguinte cobre exatamente o que falhou, nem mais nem menos.

`tbl_cfg_alert_routing.destination` guarda uma **referência** ao segredo no
Key Vault (`kv://<nome>`), nunca a URL do webhook (§4.2 do documento) — por
isso a resolução de segredo mora aqui, não em `tbl_cfg_alert_routing`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c

logger = logging.getLogger("collector.notification")


@dataclass(frozen=True)
class PendingNotification:
    outbox_id: int
    type: str  # OPEN | CLOSE — tipo da NOTIFICAÇÃO, não incident_status
    attempt_count: int
    problem_id: str
    source: str
    domain: str
    severity: str
    natural_key: str
    error_code: str | None
    error_description: str | None
    occurrence_count: int
    opened_at: datetime
    closed_at: datetime | None
    channel: str
    destination: str


def fetch_pending(conn: pyodbc.Connection) -> list[PendingNotification]:
    rows = conn.cursor().execute("SELECT * FROM vw_pending_notifications")
    columns = [d[0] for d in rows.description]
    return [PendingNotification(**dict(zip(columns, row))) for row in rows.fetchall()]


def outbox_depth(conn: pyodbc.Connection) -> int:
    """Quantas notificações seguem pendentes — indicador citado no §12 do
    documento ("profundidade da outbox"), reportado pelo heartbeat."""
    row = conn.cursor().execute(
        "SELECT COUNT(*) AS n FROM tbl_notification_outbox WHERE sent_at IS NULL"
    ).fetchone()
    return row.n if row else 0


# ---------------------------------------------------------------------------
# Key Vault — resolve `kv://<nome>` para o valor do segredo (a URL do webhook)
# ---------------------------------------------------------------------------

def resolve_secret(
    credential: DefaultAzureCredential, key_vault_uri: str, destination: str
) -> str:
    """`destination` fora do formato `kv://<nome>` é tratado como a própria
    URL — cobre um ambiente de teste apontando direto para um webhook, sem
    exigir um segredo no Key Vault só para isso."""
    if not destination.startswith(c.KEY_VAULT_DESTINATION_PREFIX):
        return destination

    secret_name = destination[len(c.KEY_VAULT_DESTINATION_PREFIX):]
    token = credential.get_token(c.KEY_VAULT_SCOPE).token
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{key_vault_uri.rstrip('/')}/secrets/{secret_name}",
            params={"api-version": c.KEY_VAULT_API_VERSION},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()["value"]


# ---------------------------------------------------------------------------
# Teams — MessageCard, aceito por qualquer conector de Incoming Webhook sem
# configuração extra (Adaptive Card exigiria registrar um novo tipo de
# conector no Teams, fora do escopo deste ciclo).
# ---------------------------------------------------------------------------

def _color_for(notification: PendingNotification) -> str:
    if notification.type == c.NOTIFICATION_TYPE_CLOSE:
        return c.TEAMS_COLOR_RESOLVED
    return {
        "HIGH": c.TEAMS_COLOR_HIGH,
        "MEDIUM": c.TEAMS_COLOR_MEDIUM,
        "LOW": c.TEAMS_COLOR_LOW,
    }.get(notification.severity, c.TEAMS_COLOR_MEDIUM)


def build_teams_payload(notification: PendingNotification) -> dict:
    verb = "ABERTO" if notification.type == c.NOTIFICATION_TYPE_OPEN else "RESOLVIDO"
    title = f"[{verb}] {notification.source} — {notification.domain} — {notification.natural_key}"

    facts = [
        {"name": "Severidade", "value": notification.severity},
        {"name": "Domínio", "value": notification.domain},
        {"name": "Origem", "value": notification.source},
        {"name": "Ocorrências", "value": str(notification.occurrence_count)},
        {"name": "Aberto em", "value": notification.opened_at.isoformat()},
    ]
    if notification.error_code:
        facts.append({"name": "Código", "value": notification.error_code})
    if notification.closed_at:
        facts.append({"name": "Fechado em", "value": notification.closed_at.isoformat()})

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": _color_for(notification),
        "summary": title,
        "title": title,
        "text": notification.error_description or "",
        "sections": [{"facts": facts}],
    }


def send_teams(webhook_url: str, notification: PendingNotification) -> None:
    with httpx.Client(timeout=30) as client:
        resp = client.post(webhook_url, json=build_teams_payload(notification))
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Persistência do resultado do envio
# ---------------------------------------------------------------------------

def mark_sent(conn: pyodbc.Connection, outbox_id: int) -> None:
    conn.cursor().execute(
        "UPDATE tbl_notification_outbox SET sent_at = SYSDATETIMEOFFSET() "
        "WHERE outbox_id = ?",
        outbox_id,
    )
    conn.commit()


def mark_failed(conn: pyodbc.Connection, outbox_id: int, error: str) -> None:
    conn.cursor().execute(
        "UPDATE tbl_notification_outbox "
        "   SET attempt_count = attempt_count + 1, last_error = ? "
        " WHERE outbox_id = ?",
        error[:500], outbox_id,
    )
    conn.commit()


def run_cycle(
    conn: pyodbc.Connection, credential: DefaultAzureCredential, key_vault_uri: str
) -> tuple[int, int]:
    """Devolve (enviados, falhados). `channel` diferente de 'teams' fica
    pendente e loga um aviso — só Teams está implementado; email é rota
    válida em tbl_cfg_alert_routing (documento §4.2) mas não tem envio."""
    pending = fetch_pending(conn)
    secrets_cache: dict[str, str] = {}
    sent = failed = 0

    for notification in pending:
        if notification.channel != "teams":
            logger.warning(
                "outbox_id=%s: canal '%s' não implementado; mantido pendente",
                notification.outbox_id, notification.channel,
            )
            continue

        try:
            if notification.destination not in secrets_cache:
                secrets_cache[notification.destination] = resolve_secret(
                    credential, key_vault_uri, notification.destination
                )
            webhook_url = secrets_cache[notification.destination]
            send_teams(webhook_url, notification)
        except Exception as exc:
            logger.exception("outbox_id=%s: falha ao notificar", notification.outbox_id)
            mark_failed(conn, notification.outbox_id, str(exc))
            failed += 1
        else:
            mark_sent(conn, notification.outbox_id)
            sent += 1

    logger.info("notificação: %d enviadas, %d falharam", sent, failed)
    return sent, failed
