"""Testes de event_resolution.py — a máquina de estado que decide abrir,
incrementar ou fechar um incidente (documento §4.3).

Esta é a suíte que mais importa no repositório para este pilar: a lógica
que antes vivia numa stored procedure (testada só por leitura cuidadosa do
SQL) agora é Python de verdade, e por isso precisa de testes de verdade.

Não há banco real aqui: `FakeProblemsDB`/`FakeConnection`/`FakeCursor`
simulam só o suficiente das tabelas (`tbl_ref_source`, `tbl_problems`,
`tbl_technical_events`, `tbl_cfg_domain_scope`, `tbl_notification_outbox`)
para exercitar cada branch, inclusive a corrida do índice único — que aqui
é forçada deliberadamente (`_blind_once`), já que não dá para reproduzir
concorrência de verdade sem um SQL Server.
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


_IntegrityError = type("IntegrityError", (Exception,), {})
_stub("pyodbc", connect=MagicMock(), Connection=object, Cursor=object,
      IntegrityError=_IntegrityError)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import event_resolution as ev  # noqa: E402

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeProblemsDB:
    """Só o suficiente das tabelas do hub para exercitar resolve_event()."""

    def __init__(self):
        self.active_sources = {"DATABRICKS", "ADF", "DATA_QUALITY"}
        self.domain_scope: dict[tuple[str, str], str] = {}
        self.problems: dict[str, dict] = {}
        self.technical_events: list[dict] = []
        self.outbox: list[tuple[str, str]] = []
        self._next_id = 1
        # Força a próxima leitura de "existe OPEN?" a não enxergar nada,
        # mesmo que exista — simula a corrida que o índice único cobre
        # quando UPDLOCK/HOLDLOCK, por algum motivo, não bastasse sozinho.
        self._blind_once = False

    def is_active_source(self, source: str) -> bool:
        return source in self.active_sources

    def find_technical_event_by_idempotency_key(self, source, source_event_id):
        for event in self.technical_events:
            if event["source"] == source and event["source_event_id"] == source_event_id:
                return event["problem_id"]
        return None

    def find_domain(self, source, scope):
        return self.domain_scope.get((source, scope))

    def find_open_by_correlation(self, correlation_key):
        if self._blind_once:
            self._blind_once = False
            return None
        for problem_id, problem in self.problems.items():
            if (problem["correlation_key"] == correlation_key
                    and problem["incident_status"] == "OPEN"):
                return problem_id
        return None

    def insert_problem(self, *, source, correlation_key, natural_key, key_rule,
                        workspace_id, domain, severity, error_code,
                        error_description, run_id, observed_at):
        # Simula o índice único filtrado ux_problems_open
        # (schema_definition.py do hub-schema-init): dois ciclos não podem deixar dois OPEN
        # com a mesma correlation_key.
        if self.find_open_by_correlation(correlation_key) is not None:
            raise _IntegrityError(
                "[23000] [Microsoft][ODBC Driver 18 for SQL Server]"
                "Violation of UNIQUE KEY constraint (2627)."
            )
        problem_id = f"problem-{self._next_id}"
        self._next_id += 1
        self.problems[problem_id] = dict(
            source=source, correlation_key=correlation_key,
            natural_key=natural_key, key_rule=key_rule, workspace_id=workspace_id,
            domain=domain, incident_status="OPEN", severity=severity,
            error_code=error_code, error_description=error_description,
            opening_run_id=run_id, opened_at=observed_at, closing_run_id=None,
            closed_at=None, occurrence_count=1,
        )
        return problem_id

    def increment_with_severity(self, severity, error_code, error_description, problem_id):
        problem = self.problems[problem_id]
        problem["occurrence_count"] += 1
        if severity == "HIGH":
            problem["severity"] = "HIGH"
        if error_code is not None:
            problem["error_code"] = error_code
        if error_description is not None:
            problem["error_description"] = error_description

    def increment_only(self, problem_id):
        self.problems[problem_id]["occurrence_count"] += 1

    def resolve_problem(self, closed_at, closing_run_id, problem_id):
        problem = self.problems[problem_id]
        problem["incident_status"] = "RESOLVED"
        problem["closed_at"] = closed_at
        problem["closing_run_id"] = closing_run_id


class FakeCursor:
    def __init__(self, db: FakeProblemsDB):
        self.db = db
        self._fetchone_value = None

    def execute(self, sql: str, *params):
        if "SELECT 1 FROM tbl_ref_source" in sql:
            self._fetchone_value = (1,) if self.db.is_active_source(params[0]) else None

        elif "SELECT problem_id FROM tbl_technical_events" in sql:
            pid = self.db.find_technical_event_by_idempotency_key(*params)
            self._fetchone_value = _Row(problem_id=pid) if pid else None

        elif "SELECT TOP 1 domain FROM tbl_cfg_domain_scope" in sql:
            domain = self.db.find_domain(*params)
            self._fetchone_value = _Row(domain=domain) if domain else None

        elif "SELECT problem_id FROM tbl_problems" in sql:
            # Cobre as duas variantes: com WITH (UPDLOCK, HOLDLOCK) dentro
            # de _apply(), e sem, dentro de _retry_after_lost_race().
            pid = self.db.find_open_by_correlation(params[0])
            self._fetchone_value = _Row(problem_id=pid) if pid else None

        elif "INSERT INTO tbl_problems" in sql:
            (source, correlation_key, natural_key, key_rule, workspace_id,
             domain, severity, error_code, error_description, run_id,
             observed_at) = params
            problem_id = self.db.insert_problem(
                source=source, correlation_key=correlation_key,
                natural_key=natural_key, key_rule=key_rule,
                workspace_id=workspace_id, domain=domain, severity=severity,
                error_code=error_code, error_description=error_description,
                run_id=run_id, observed_at=observed_at,
            )
            self._fetchone_value = _Row(problem_id=problem_id)

        elif "UPDATE tbl_problems" in sql and "severity = CASE" in sql:
            self.db.increment_with_severity(*params)

        elif "UPDATE tbl_problems" in sql and "incident_status = 'RESOLVED'" in sql:
            self.db.resolve_problem(*params)

        elif "UPDATE tbl_problems" in sql:
            self.db.increment_only(*params)

        elif "INSERT INTO tbl_notification_outbox" in sql:
            outbox_type = "OPEN" if "'OPEN'" in sql else "CLOSE"
            self.db.outbox.append((params[0], outbox_type))

        elif "INSERT INTO tbl_technical_events" in sql:
            if "'RECUPERADO'" in sql:
                (problem_id, source, error_description, run_id, natural_key,
                 source_event_id, correlation_ref, observed_at) = params
                error_code = "RECUPERADO"
            else:
                (problem_id, source, error_code, error_description, run_id,
                 natural_key, source_event_id, correlation_ref,
                 observed_at) = params
            self.db.technical_events.append(dict(
                problem_id=problem_id, source=source, error_code=error_code,
                error_description=error_description, run_id=run_id,
                natural_key=natural_key, source_event_id=source_event_id,
                correlation_ref=correlation_ref, observed_at=observed_at,
            ))

        else:
            raise AssertionError(f"SQL não reconhecido pelo fake:\n{sql}")

        return self

    def fetchone(self):
        return self._fetchone_value


class FakeConnection:
    def __init__(self, db: FakeProblemsDB):
        self.db = db
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.db)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class TestNewFailureOpensProblem(unittest.TestCase):
    def test_opens_and_returns_aberto(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)

        problem_id, action = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            observed_at=NOW,
        )

        self.assertEqual(action, ev.ACTION_ABERTO)
        self.assertEqual(db.problems[problem_id]["incident_status"], "OPEN")
        self.assertEqual(db.problems[problem_id]["occurrence_count"], 1)
        self.assertEqual(conn.commits, 1)

    def test_writes_outbox_open_in_the_same_call(self):
        # Outbox (§9): a intenção de notificar é gravada na mesma transação
        # que abre o incidente, nunca depois.
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            observed_at=NOW,
        )
        self.assertIn((problem_id, "OPEN"), db.outbox)

    def test_writes_technical_event(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            run_id="42", observed_at=NOW,
        )
        self.assertEqual(len(db.technical_events), 1)
        self.assertEqual(db.technical_events[0]["problem_id"], problem_id)
        self.assertEqual(db.technical_events[0]["run_id"], "42")


class TestCorrelationKey(unittest.TestCase):
    """A correlação sai SEMPRE da natural_key, que é estável entre execuções
    do mesmo processo. É essa estabilidade que permite ao sucesso seguinte
    fechar o incidente."""

    def test_adf_source_uses_the_natural_key(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="ADF", result="FAIL", natural_key="DF-X/pipeline",
            observed_at=NOW,
        )
        self.assertEqual(db.problems[problem_id]["correlation_key"], "adf:df-x/pipeline")

    def test_other_source_uses_source_prefixed_natural_key(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATA_QUALITY", result="FAIL", natural_key="asset.rule",
            observed_at=NOW,
        )
        self.assertEqual(
            db.problems[problem_id]["correlation_key"], "data_quality:asset.rule"
        )

    def test_execution_id_does_not_change_the_correlation(self):
        """O identificador da execução (ActivityRunId do ADF) é novo a cada
        execução: se entrasse na correlação, o sucesso seguinte cairia noutra
        chave e o incidente nunca fecharia sozinho."""
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        first, _ = ev.resolve_event(
            conn, source="ADF", result="FAIL", natural_key="DF-X/pipeline/copy",
            correlation_ref="abc-123", observed_at=NOW,
        )
        self.assertEqual(
            db.problems[first]["correlation_key"], "adf:df-x/pipeline/copy")

        # Execução seguinte: outro ActivityRunId, mesmo processo, sucesso.
        resolved, action = ev.resolve_event(
            conn, source="ADF", result="PASS", natural_key="DF-X/pipeline/copy",
            correlation_ref="def-456", observed_at=NOW,
        )
        self.assertEqual(action, ev.ACTION_RESOLVIDO)
        self.assertEqual(resolved, first)

    def test_execution_id_is_recorded_on_the_child_event(self):
        """É por `correlation_ref` que se liga, depois, o evento do ADF e o do
        run Databricks que a mesma atividade disparou."""
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        ev.resolve_event(
            conn, source="ADF", result="FAIL", natural_key="DF-X/pipeline/copy",
            correlation_ref="abc-123", observed_at=NOW,
        )
        self.assertEqual(db.technical_events[0]["correlation_ref"], "abc-123")

    def test_adf_and_databricks_of_the_same_execution_are_two_incidents(self):
        """Consequência aceita da correlação estável: cada lado tem o seu
        incidente, e os dois fecham sozinhos. O que liga os dois é o
        `correlation_ref` gravado nos eventos filhos, não a correlação."""
        db = FakeProblemsDB()
        conn = FakeConnection(db)

        dbx_id, dbx_action = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="adf_pipeline_x",
            correlation_ref="abc-123", observed_at=NOW,
        )
        adf_id, adf_action = ev.resolve_event(
            conn, source="ADF", result="FAIL", natural_key="DF-X/pipeline_x/copy",
            correlation_ref="abc-123", observed_at=NOW,
        )

        self.assertEqual(dbx_action, ev.ACTION_ABERTO)
        self.assertEqual(adf_action, ev.ACTION_ABERTO)
        self.assertNotEqual(dbx_id, adf_id)
        self.assertEqual(
            {e["correlation_ref"] for e in db.technical_events}, {"abc-123"})


class TestRecurrence(unittest.TestCase):
    def test_second_failure_increments_and_does_not_reopen(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        first_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            observed_at=NOW,
        )
        second_id, action = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            observed_at=NOW,
        )
        self.assertEqual(action, ev.ACTION_INCREMENTADO)
        self.assertEqual(first_id, second_id)
        self.assertEqual(db.problems[first_id]["occurrence_count"], 2)
        # Recorrência não notifica de novo — só a abertura entra na outbox.
        self.assertEqual(len(db.outbox), 1)

    def test_severity_only_escalates_never_downgrades(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            severity="LOW", observed_at=NOW,
        )
        ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            severity="MEDIUM", observed_at=NOW,
        )
        # MEDIUM não é HIGH, então a severidade não muda — fica em LOW.
        self.assertEqual(db.problems[problem_id]["severity"], "LOW")

        ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            severity="HIGH", observed_at=NOW,
        )
        self.assertEqual(db.problems[problem_id]["severity"], "HIGH")

        ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            severity="LOW", observed_at=NOW,
        )
        # Uma vez HIGH, uma falha LOW subsequente não derruba — mascarar
        # agravamento é o defeito que essa regra evita.
        self.assertEqual(db.problems[problem_id]["severity"], "HIGH")

    def test_error_code_updates_via_coalesce_semantics(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            error_code="TIMEOUT", observed_at=NOW,
        )
        # error_code=None não apaga o anterior — COALESCE(NULL, atual).
        ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            error_code=None, observed_at=NOW,
        )
        self.assertEqual(db.problems[problem_id]["error_code"], "TIMEOUT")

        ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            error_code="FAILED", observed_at=NOW,
        )
        self.assertEqual(db.problems[problem_id]["error_code"], "FAILED")


class TestSuccessClosesOrIsIgnored(unittest.TestCase):
    def test_success_with_open_incident_resolves(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            observed_at=NOW,
        )
        resolved_id, action = ev.resolve_event(
            conn, source="DATABRICKS", result="PASS", natural_key="ws1:123",
            observed_at=NOW,
        )
        self.assertEqual(action, ev.ACTION_RESOLVIDO)
        self.assertEqual(resolved_id, problem_id)
        self.assertEqual(db.problems[problem_id]["incident_status"], "RESOLVED")
        self.assertIn((problem_id, "CLOSE"), db.outbox)

    def test_success_without_open_incident_is_ignored_and_writes_nothing(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, action = ev.resolve_event(
            conn, source="DATABRICKS", result="PASS", natural_key="ws1:999",
            observed_at=NOW,
        )
        self.assertEqual(action, ev.ACTION_IGNORADO)
        self.assertIsNone(problem_id)
        self.assertEqual(db.problems, {})
        self.assertEqual(db.outbox, [])
        self.assertEqual(db.technical_events, [])


class TestIdempotency(unittest.TestCase):
    """§5.6: reenvio após timeout não pode duplicar evento — necessário
    porque o push do hub_publisher é at-least-once."""

    def test_same_source_event_id_is_not_reprocessed(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        first_id, first_action = ev.resolve_event(
            conn, source="DATA_QUALITY", result="FAIL", natural_key="asset.rule",
            source_event_id="hash-abc", observed_at=NOW,
        )
        second_id, second_action = ev.resolve_event(
            conn, source="DATA_QUALITY", result="FAIL", natural_key="asset.rule",
            source_event_id="hash-abc", observed_at=NOW,
        )
        self.assertEqual(first_action, ev.ACTION_ABERTO)
        self.assertEqual(second_action, ev.ACTION_DUPLICADO)
        self.assertEqual(first_id, second_id)
        # Reenvio não gera segunda ocorrência.
        self.assertEqual(db.problems[first_id]["occurrence_count"], 1)


class TestDomainResolution(unittest.TestCase):
    def test_match_found_by_scope(self):
        db = FakeProblemsDB()
        db.domain_scope[("DATABRICKS", "ws1")] = "Logistics"
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            scope="ws1", observed_at=NOW,
        )
        self.assertEqual(db.problems[problem_id]["domain"], "Logistics")

    def test_falls_back_to_workspace_id_when_scope_is_none(self):
        db = FakeProblemsDB()
        db.domain_scope[("DATABRICKS", "ws1")] = "Logistics"
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            workspace_id="ws1", observed_at=NOW,
        )
        self.assertEqual(db.problems[problem_id]["domain"], "Logistics")

    def test_no_match_falls_back_to_default(self):
        # Alerta sem domínio é alerta atrasado, nunca perdido (§4).
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        problem_id, _ = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            scope="ws-desconhecido", observed_at=NOW,
        )
        self.assertEqual(db.problems[problem_id]["domain"], "DEFAULT")


class TestValidation(unittest.TestCase):
    def test_unknown_source_raises(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        with self.assertRaises(ValueError):
            ev.resolve_event(
                conn, source="INEXISTENTE", result="FAIL", natural_key="x",
                observed_at=NOW,
            )

    def test_invalid_result_raises(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)
        with self.assertRaises(ValueError):
            ev.resolve_event(
                conn, source="DATABRICKS", result="MAYBE", natural_key="x",
                observed_at=NOW,
            )


class TestLostRace(unittest.TestCase):
    """A violação do índice único é tratada como corrida perdida, não como
    erro (§4.3) — é este bloco que substitui de fato o check-then-act."""

    def test_falls_back_to_increment_when_insert_collides(self):
        db = FakeProblemsDB()
        conn = FakeConnection(db)

        # "Outro ciclo" já abriu o incidente.
        winner_id = db.insert_problem(
            source="DATABRICKS", correlation_key="databricks:ws1:123",
            natural_key="ws1:123", key_rule="JOB_ID", workspace_id="ws1",
            domain="DEFAULT", severity="MEDIUM", error_code=None,
            error_description=None, run_id="1", observed_at=NOW,
        )

        # A nossa leitura, por timing, não viu o vencedor — só assim o
        # código chega a tentar o INSERT que colide.
        db._blind_once = True

        problem_id, action = ev.resolve_event(
            conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
            observed_at=NOW,
        )

        self.assertEqual(action, ev.ACTION_INCREMENTADO)
        self.assertEqual(problem_id, winner_id)
        self.assertEqual(db.problems[winner_id]["occurrence_count"], 2)
        self.assertEqual(conn.rollbacks, 1)
        # A transação de retry também precisa ser confirmada.
        self.assertGreaterEqual(conn.commits, 1)

    def test_non_duplicate_integrity_error_propagates(self):
        # Só 2601/2627 são tratados como corrida; qualquer outra violação
        # de integridade (FK, CHECK) precisa continuar subindo como erro.
        db = FakeProblemsDB()
        conn = FakeConnection(db)

        original_insert = db.insert_problem

        def _boom(**kwargs):
            raise _IntegrityError("FOREIGN KEY constraint failed")

        db.insert_problem = _boom
        with self.assertRaises(_IntegrityError):
            ev.resolve_event(
                conn, source="DATABRICKS", result="FAIL", natural_key="ws1:123",
                observed_at=NOW,
            )
        db.insert_problem = original_insert


if __name__ == "__main__":
    unittest.main()
