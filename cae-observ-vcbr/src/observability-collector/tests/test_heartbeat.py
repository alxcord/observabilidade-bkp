"""Testes de heartbeat.py (documento §12).

O caso que mais importa aqui não é o envio — é o que acontece quando a DCE
e a DCR ainda não existem: o ciclo inteiro não pode falhar por causa do
sinal que só existe para relatar se o ciclo falhou.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


_stub("httpx", Client=MagicMock())
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import heartbeat  # noqa: E402


def _stats(**overrides) -> heartbeat.CycleStats:
    base = dict(
        status="OK", duration_seconds=1.2, workspaces_processed=2,
        active_executions=3, databricks_events=4, adf_events=5,
        reconciliation_gaps_filled=0, notifications_sent=1,
        notifications_failed=0, outbox_depth=0, errors_count=0,
    )
    base.update(overrides)
    return heartbeat.CycleStats(**base)


class TestPascalCase(unittest.TestCase):
    def test_converts_snake_to_pascal(self):
        self.assertEqual(heartbeat._pascal_case("errors_count"), "ErrorsCount")

    def test_single_word(self):
        self.assertEqual(heartbeat._pascal_case("status"), "Status")


class TestRecord(unittest.TestCase):
    def test_missing_endpoint_skips_without_raising(self):
        with patch.dict("os.environ", {}, clear=True):
            heartbeat.record(MagicMock(), _stats())  # não deve levantar

    def test_missing_dcr_id_skips_without_raising(self):
        with patch.dict(
            "os.environ",
            {"LOG_ANALYTICS_INGESTION_ENDPOINT": "https://dce.example"},
            clear=True,
        ):
            heartbeat.record(MagicMock(), _stats())

    def test_configured_endpoint_posts_the_record(self):
        env = {
            "LOG_ANALYTICS_INGESTION_ENDPOINT": "https://dce.example",
            "LOG_ANALYTICS_DCR_IMMUTABLE_ID": "dcr-123",
        }
        credential = MagicMock()
        credential.get_token.return_value.token = "tok"

        with patch.dict("os.environ", env, clear=True), \
             patch("heartbeat.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            heartbeat.record(credential, _stats(status="ERROR", errors_count=2))

        posted_body = mock_client.post.call_args.kwargs["json"]
        self.assertEqual(posted_body[0]["Status"], "ERROR")
        self.assertEqual(posted_body[0]["ErrorsCount"], 2)

    def test_send_failure_is_swallowed(self):
        env = {
            "LOG_ANALYTICS_INGESTION_ENDPOINT": "https://dce.example",
            "LOG_ANALYTICS_DCR_IMMUTABLE_ID": "dcr-123",
        }
        with patch.dict("os.environ", env, clear=True), \
             patch("heartbeat.httpx.Client", side_effect=RuntimeError("boom")):
            heartbeat.record(MagicMock(), _stats())  # não deve levantar


if __name__ == "__main__":
    unittest.main()
