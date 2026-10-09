"""Testes do inicializador de schema.

pyodbc e azure-identity são stubs. O que se testa: a
classificação de cada comando do DDL embutido, a leitura da estrutura esperada
de cada tabela, a decisão criar/conferir/pular e o SQL de permissões.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _PyodbcError(Exception):
    pass


_stub("pyodbc", connect=MagicMock(), Connection=object, Error=_PyodbcError)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import schema_init as si  # noqa: E402
from schema_definition import SCHEMA_BATCHES  # noqa: E402

BATCHES = SCHEMA_BATCHES
CREATES = {si.classify(b)[1]: b for b in BATCHES if si.classify(b)[0] == "table"}

def _db_columns(table: str) -> dict[str, str]:
    """A estrutura 'correta' no banco simulado: exatamente a do arquivo."""
    return dict(si.expected_columns(CREATES[table]))


class FakeConn:
    """Banco simulado: tabelas (nome -> {coluna: tipo}) e índices existentes."""

    def __init__(self, tables=None, indexes=None, fail_on=None):
        self.tables = {k: dict(v) for k, v in (tables or {}).items()}
        self.indexes = set(indexes or ())
        self.fail_on = fail_on
        self.executed: list[str] = []
        self.commits = self.rollbacks = 0
        self._result = None

    def cursor(self):
        return self

    def execute(self, sql, *params):
        if "OBJECT_ID(?, 'U')" in sql:
            exists = params[0].lower() in self.tables
            self._result = [types.SimpleNamespace(id=1 if exists else None)]
        elif "FROM sys.indexes" in sql:
            self._result = [(1,)] if params[0].lower() in self.indexes else []
        elif "INFORMATION_SCHEMA.COLUMNS" in sql:
            self._result = [self._column_row(n, t) for n, t in self.tables[params[0].lower()].items()]
        elif "FROM sys.database_principals" in sql:
            self._result = []
        else:
            if self.fail_on and self.fail_on in sql:
                raise _PyodbcError("falha simulada")
            self.executed.append(sql)
            self._result = []
            kind = si.classify(sql)
            if kind[0] == "table":
                self.tables[kind[1]] = si.expected_columns(sql)
            elif kind[0] == "index":
                self.indexes.add(kind[1])
        return self

    @staticmethod
    def _column_row(name, type_):
        base, _, param = type_.partition("(")
        param = param.rstrip(")")
        row = types.SimpleNamespace(COLUMN_NAME=name, DATA_TYPE=base,
                                    CHARACTER_MAXIMUM_LENGTH=None, DATETIME_PRECISION=None)
        if base in si._TEXT_TYPES:
            row.CHARACTER_MAXIMUM_LENGTH = -1 if param == "max" else int(param)
        if base in si._PRECISION_TYPES:
            row.DATETIME_PRECISION = int(param)
        return row

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ParsingTest(unittest.TestCase):
    def test_todo_lote_do_ddl_e_reconhecido(self):
        kinds = [si.classify(b)[0] for b in BATCHES]
        self.assertEqual(kinds.count("table"), len(CREATES))
        self.assertEqual(kinds.count("view"), 3)
        self.assertGreaterEqual(kinds.count("index"), 6)
        self.assertEqual(kinds.count("insert"), 3)
        for batch in BATCHES:
            self.assertNotRegex(batch, r"(?im)^\s*GO\s*$")

    def test_toda_tabela_que_a_api_edita_existe_no_ddl(self):
        import constants as c
        for table in c.API_CRUD:
            self.assertIn(table, CREATES)

    def test_comando_desconhecido_falha(self):
        with self.assertRaises(ValueError):
            si.classify("DROP TABLE tbl_problems")

    def test_colunas_esperadas(self):
        cols = si.expected_columns(CREATES["tbl_cfg_workspaces"])
        self.assertEqual(cols["workspace_id"], "varchar(50)")
        self.assertEqual(cols["workspace_url"], "nvarchar(300)")
        self.assertEqual(cols["active"], "bit")
        self.assertEqual(cols["synced_at"], "datetimeoffset(3)")

    def test_constraints_nao_viram_coluna_e_tipos_especiais(self):
        cols = si.expected_columns(CREATES["tbl_cfg_execution_expectations"])
        self.assertNotIn("constraint", cols)
        self.assertEqual(cols["time_of_day"], "time(0)")
        self.assertEqual(cols["schedule_kind"], "varchar(10)")
        outbox = si.expected_columns(CREATES["tbl_notification_outbox"])
        self.assertEqual(outbox["outbox_id"], "bigint")
        problems = si.expected_columns(CREATES["tbl_problems"])
        self.assertEqual(problems["problem_id"], "uniqueidentifier")

    def test_view_de_fechados_sem_aritmetica_invalida(self):
        text = "\n".join(BATCHES)
        self.assertNotIn("AS DATETIMEOFFSET(3)) - 1", text)
        self.assertIn("DATEADD(day, -1", text)


class RunSchemaTest(unittest.TestCase):
    def test_banco_vazio_cria_tudo_e_semeia(self):
        conn = FakeConn()
        stats = si.run_schema(conn, dry_run=False)
        self.assertEqual(stats["tabelas_criadas"], len(CREATES))
        self.assertEqual(stats["dados_iniciais"], 3)
        self.assertEqual(stats["views"], 3)
        self.assertEqual(conn.commits, 1)
        creates = [i for i, s in enumerate(conn.executed) if s.startswith("CREATE TABLE")]
        view = next(i for i, s in enumerate(conn.executed) if "VIEW" in s)
        self.assertLess(max(creates), view)

    def test_segunda_execucao_nao_recria_nada_e_reaplica_so_as_views(self):
        conn = FakeConn()
        si.run_schema(conn, dry_run=False)
        conn.executed.clear()
        stats = si.run_schema(conn, dry_run=False)
        self.assertEqual(stats["tabelas_criadas"], 0)
        self.assertEqual(stats["tabelas_conferidas"], len(CREATES))
        self.assertEqual(stats["dados_iniciais"], 0)
        self.assertEqual(stats["indices_criados"], 0)
        self.assertTrue(all("VIEW" in s for s in conn.executed))
        self.assertEqual(len(conn.executed), 3)

    def test_cria_so_a_tabela_que_falta_e_so_semeia_a_nova(self):
        existing = {t: _db_columns(t) for t in CREATES if t != "tbl_ctl_replication"}
        conn = FakeConn(tables=existing)
        # índices existentes: todos os das tabelas que já existem
        conn.indexes = {si.classify(b)[1] for b in BATCHES if si.classify(b)[0] == "index"}
        stats = si.run_schema(conn, dry_run=False)
        self.assertEqual(stats["tabelas_criadas"], 1)
        self.assertEqual(stats["dados_iniciais"], 1)
        inserts = [s for s in conn.executed if s.startswith("INSERT")]
        self.assertEqual(len(inserts), 1)
        self.assertIn("tbl_ctl_replication", inserts[0])

    def test_coluna_ausente_ou_tipo_errado_reprova_e_nao_grava(self):
        existing = {t: _db_columns(t) for t in CREATES}
        del existing["tbl_cfg_workspaces"]["region"]
        existing["tbl_problems"]["severity"] = "varchar(5)"
        conn = FakeConn(tables=existing)
        with self.assertRaises(si.SchemaMismatch) as ctx:
            si.run_schema(conn, dry_run=False)
        message = str(ctx.exception)
        self.assertIn("tbl_cfg_workspaces.region: coluna ausente", message)
        self.assertIn("tbl_problems.severity: tipo varchar(5), esperado varchar(20)", message)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_coluna_extra_no_banco_nao_e_problema(self):
        existing = {t: _db_columns(t) for t in CREATES}
        existing["tbl_problems"]["coluna_nova"] = "int"
        conn = FakeConn(tables=existing)
        conn.indexes = {si.classify(b)[1] for b in BATCHES if si.classify(b)[0] == "index"}
        si.run_schema(conn, dry_run=False)

    def test_falha_no_meio_desfaz_tudo(self):
        conn = FakeConn(fail_on="CREATE TABLE tbl_problems")
        with self.assertRaises(RuntimeError) as ctx:
            si.run_schema(conn, dry_run=False)
        self.assertIn("falha simulada", str(ctx.exception))
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_dry_run_nao_executa_nem_grava(self):
        conn = FakeConn()
        stats = si.run_schema(conn, dry_run=True)
        self.assertEqual(stats["tabelas_criadas"], len(CREATES))
        self.assertEqual(conn.executed, [])
        self.assertEqual(conn.commits, 0)


class GrantTest(unittest.TestCase):
    def test_quote_name(self):
        self.assertEqual(si.quote_name("Data Lake - Sustentacao"), "[Data Lake - Sustentacao]")
        for bad in ("x]; DROP TABLE a;--", "a'b", ""):
            with self.assertRaises(ValueError):
                si.quote_name(bad)

    def test_nomes_por_ambiente(self):
        dev = si.grant_statements("dev", [])
        prod = si.grant_statements("prod", [])
        self.assertTrue(any("[mi-observ-coletor-dev-vcbr]" in s for s in dev))
        self.assertTrue(any("[mi-observ-coletor-vcbr]" in s for s in prod))
        self.assertFalse(any("-dev-" in s for s in prod))

    def test_api_nao_toca_tabelas_de_incidente(self):
        statements = " ".join(si.grant_statements("prod", []))
        self.assertNotIn("tbl_problems", statements)
        self.assertNotIn("tbl_technical_events", statements)
        self.assertIn("GRANT SELECT ON dbo.vw_open_problems TO [mi-observ-api-vcbr]", statements)
        self.assertIn("GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.tbl_cfg_workspaces", statements)

    def test_grupos_recebem_ddladmin(self):
        self.assertIn("ALTER ROLE db_ddladmin ADD MEMBER [Grupo A]",
                      si.grant_statements("prod", ["Grupo A"]))

    def test_cria_so_o_que_falta(self):
        class Conn(FakeConn):
            def execute(self, sql, *params):
                if "sys.database_principals" in sql:
                    self._result = [(1,)] if params[0] == "mi-observ-api-vcbr" else []
                    return self
                self.executed.append(sql)
                self._result = []
                return self
        conn = Conn()
        si.grant_users(conn, "prod", [])
        creates = [s for s in conn.executed if s.startswith("CREATE USER")]
        self.assertEqual(len(creates), 1)
        self.assertIn("[mi-observ-coletor-vcbr]", creates[0])
        self.assertEqual(conn.commits, 1)


if __name__ == "__main__":
    unittest.main()
