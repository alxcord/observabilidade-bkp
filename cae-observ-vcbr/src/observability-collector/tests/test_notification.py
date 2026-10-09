"""Testes de notification.py (documento §9) — resolução de segredo do Key
Vault e o payload mínimo do Teams.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


_stub("httpx", Client=MagicMock())
_stub("pyodbc", connect=MagicMock(), Connection=object)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import notification as notif  # noqa: E402


def _notification(**overrides) -> notif.PendingNotification:
    base = dict(
        outbox_id=1, type="OPEN", attempt_count=0, problem_id="p1",
        source="DATABRICKS", domain="Logistics", severity="HIGH",
        natural_key="ws1:123", error_code="FAILED",
        error_description="deu ruim", occurrence_count=1,
        opened_at=datetime(2024, 6, 1, tzinfo=timezone.utc), closed_at=None,
        channel="teams", destination="kv://obs-webhook-ops",
    )
    base.update(overrides)
    return notif.PendingNotification(**base)


class TestResolveSecret(unittest.TestCase):
    def test_destination_without_kv_prefix_is_used_as_is(self):
        # Cobre um ambiente de teste apontando direto para um webhook, sem
        # exigir Key Vault só para isso.
        credential = MagicMock()
        url = notif.resolve_secret(
            credential, "https://vault.example", "https://webhook.example/x"
        )
        self.assertEqual(url, "https://webhook.example/x")
        credential.get_token.assert_not_called()

    def test_kv_destination_calls_key_vault(self):
        credential = MagicMock()
        credential.get_token.return_value.token = "tok"

        with unittest.mock.patch("notification.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value.json.return_value = {
                "value": "https://outlook.webhook/real"
            }
            url = notif.resolve_secret(
                credential, "https://kv-obs.vault.azure.net", "kv://obs-webhook-ops"
            )

        self.assertEqual(url, "https://outlook.webhook/real")
        called_url = mock_client.get.call_args.args[0]
        self.assertIn("obs-webhook-ops", called_url)


class TestBuildTeamsPayload(unittest.TestCase):
    def test_open_notification_uses_severity_color(self):
        payload = notif.build_teams_payload(_notification(severity="HIGH"))
        self.assertEqual(payload["themeColor"], notif.c.TEAMS_COLOR_HIGH)
        self.assertIn("ABERTO", payload["title"])

    def test_close_notification_uses_resolved_color_regardless_of_severity(self):
        payload = notif.build_teams_payload(
            _notification(type="CLOSE", severity="HIGH")
        )
        self.assertEqual(payload["themeColor"], notif.c.TEAMS_COLOR_RESOLVED)
        self.assertIn("RESOLVIDO", payload["title"])

    def test_facts_include_error_code_when_present(self):
        payload = notif.build_teams_payload(_notification(error_code="FAILED"))
        names = [f["name"] for f in payload["sections"][0]["facts"]]
        self.assertIn("Código", names)

    def test_facts_omit_error_code_when_absent(self):
        payload = notif.build_teams_payload(_notification(error_code=None))
        names = [f["name"] for f in payload["sections"][0]["facts"]]
        self.assertNotIn("Código", names)


class TestRunCycleChannelHandling(unittest.TestCase):
    def test_unsupported_channel_is_skipped_and_left_pending(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.description = [
            (f,) for f in notif.PendingNotification.__dataclass_fields__
        ]
        conn.cursor.return_value.execute.return_value.fetchall.return_value = [
            tuple(getattr(_notification(channel="email"), f)
                  for f in notif.PendingNotification.__dataclass_fields__)
        ]
        sent, failed = notif.run_cycle(conn, MagicMock(), "https://kv.example")
        self.assertEqual((sent, failed), (0, 0))
        conn.cursor.return_value.execute.assert_any_call(
            "SELECT * FROM vw_pending_notifications"
        )


if __name__ == "__main__":
    unittest.main()
