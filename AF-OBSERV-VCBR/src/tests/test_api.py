"""Testes da API.

pyodbc, azure-identity e azure-functions são substituídos por stubs: o que
importa testar é a autorização, a validação e o SQL gerado — não as
bibliotecas nem o banco.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

for name in ("pyodbc", "azure", "azure.identity", "azure.functions"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)

sys.modules["pyodbc"].Connection = object
sys.modules["pyodbc"].IntegrityError = type("IntegrityError", (Exception,), {})
sys.modules["pyodbc"].connect = MagicMock()
sys.modules["azure.identity"].DefaultAzureCredential = MagicMock()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth  # noqa: E402
import constants as c  # noqa: E402
import resources  # noqa: E402
import repository as repo  # noqa: E402


class TestAuthorization(unittest.TestCase):
    def setUp(self):
        import os
        os.environ[c.VAR_TOKEN_READ] = "read-token"
        os.environ[c.VAR_TOKEN_WRITE] = "write-token"

    def test_correct_token_passes(self):
        auth.require("Bearer read-token", c.SCOPE_READ)

    def test_read_token_does_not_serve_write(self):
        # Escopos separados: um token de leitura vazado expõe a lista de
        # incidentes; um de escrita permite injetar incidente falso.
        with self.assertRaises(auth.Unauthorized):
            auth.require("Bearer read-token", c.SCOPE_WRITE)

    def test_missing_header_fails(self):
        with self.assertRaises(auth.Unauthorized):
            auth.require(None, c.SCOPE_READ)

    def test_invalid_format_fails(self):
        for header in ("read-token", "Basic read-token", "Bearer"):
            with self.assertRaises(auth.Unauthorized):
                auth.require(header, c.SCOPE_READ)

    def test_unconfigured_token_fails_closed(self):
        # Erro de deploy não pode virar API aberta.
        import os
        os.environ.pop(c.VAR_TOKEN_WRITE)
        with self.assertRaises(auth.Unauthorized):
            auth.require("Bearer qualquer", c.SCOPE_WRITE)
        os.environ[c.VAR_TOKEN_WRITE] = "write-token"

    def test_unresolved_key_vault_reference_fails_closed(self):
        """Referência não resolvida não vale como token.

        Se o App Setting fica com o texto da referência em vez do segredo,
        esse texto seria um token previsível — qualquer um que conheça o
        formato o escreve.
        """
        import os
        reference = f"{c.KEY_VAULT_REFERENCE_PREFIX}(SecretUri=https://kv/x)"
        os.environ[c.VAR_TOKEN_READ] = reference
        try:
            with self.assertRaises(auth.Unauthorized):
                auth.require(f"Bearer {reference}", c.SCOPE_READ)
        finally:
            os.environ[c.VAR_TOKEN_READ] = "read-token"


class TestResourceValidation(unittest.TestCase):
    def setUp(self):
        self.resource = resources.RESOURCES["workspaces"]

    def test_valid_body_passes(self):
        body = {
            "workspace_id": "ws1",
            "workspace_name": "Analytics",
            "workspace_url": "https://x.azuredatabricks.net",
        }
        self.assertEqual(self.resource.validate(body, creating=True), [])

    def test_unknown_field_is_rejected(self):
        # Um typo em nome de coluna passaria como "não atualizou nada", e o
        # cliente não teria como saber.
        body = {"workspace_id": "ws1", "workspace_nome": "typo"}
        self.assertTrue(self.resource.validate(body, creating=False))

    def test_read_only_field_is_rejected(self):
        body = {"workspace_id": "ws1", "synced_at": "2026-01-01"}
        errors = self.resource.validate(body, creating=False)
        self.assertIn("somente leitura", "; ".join(errors))

    def test_missing_required_field_on_create(self):
        errors = self.resource.validate({"workspace_id": "ws1"}, creating=True)
        self.assertIn("obrigatórios", "; ".join(errors))

    def test_required_field_not_enforced_on_update(self):
        # Atualização é parcial: o que não veio não é tocado.
        self.assertEqual(
            self.resource.validate({"active": False}, creating=False), []
        )


class TestFixedColumns(unittest.TestCase):
    """Colunas que a API define sozinha, e o cliente não escolhe."""

    def setUp(self):
        self.resource = resources.RESOURCES["expectations"]
        self.body = {
            "source": "CONTROLM",
            "natural_key": "job-x",
            "schedule_kind": "DAILY",
            "written_by": "equipe-dados",
        }

    def test_create_without_the_fixed_column_passes(self):
        # A coluna é obrigatória no banco; quem cadastra não precisa saber.
        self.assertEqual(self.resource.validate(self.body, creating=True), [])

    def test_client_cannot_choose_the_value(self):
        # Com AUTO ou YAML a linha passaria a ser gerenciada pelo
        # sincronizador, que a sobrescreveria sem avisar quem cadastrou.
        errors = self.resource.validate(
            {**self.body, "config_source": "AUTO"}, creating=True
        )
        self.assertIn("config_source", "; ".join(errors))

    def test_client_cannot_change_it_on_update(self):
        errors = self.resource.validate(
            {"config_source": c.EXPECTATION_CONFIG_SOURCE}, creating=False
        )
        self.assertTrue(errors)

    def test_create_writes_the_value_from_the_policy(self):
        with unittest.mock.patch.object(repo.db, "execute") as execute, \
             unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = [{"source": "CONTROLM"}]
            repo.create(MagicMock(), self.resource, self.body)

        sql, params = execute.call_args[0][1], execute.call_args[0][2:]
        self.assertIn("config_source", sql)
        self.assertIn(c.EXPECTATION_CONFIG_SOURCE, params)


class TestIntegrationRuntimes(unittest.TestCase):
    """Cadastro de Self-Hosted IR: o que o coletor compara vem daqui."""

    def setUp(self):
        self.resource = resources.RESOURCES["integration-runtimes"]
        self.body = {
            "data_factory": "adf-analytics-vcbr",
            "integration_runtime": "shir-onprem-01",
            "expected_nodes": c.MIN_EXPECTED_NODES + 1,
        }

    def test_valid_body_passes(self):
        self.assertEqual(self.resource.validate(self.body, creating=True), [])

    def test_minimum_expected_nodes_is_accepted(self):
        body = {**self.body, "expected_nodes": c.MIN_EXPECTED_NODES}
        self.assertEqual(self.resource.validate(body, creating=True), [])

    def test_below_the_minimum_is_rejected(self):
        body = {**self.body, "expected_nodes": c.MIN_EXPECTED_NODES - 1}
        errors = self.resource.validate(body, creating=True)
        self.assertIn("expected_nodes", "; ".join(errors))

    def test_non_integer_node_count_is_rejected(self):
        # "2" iria para uma coluna INT, e True viraria 1 silenciosamente.
        for value in ("2", 2.5, True, None):
            body = {**self.body, "expected_nodes": value}
            self.assertTrue(
                self.resource.validate(body, creating=True),
                f"aceitou expected_nodes={value!r}",
            )

    def test_blank_names_are_rejected(self):
        for column in ("data_factory", "integration_runtime"):
            body = {**self.body, column: "   "}
            errors = self.resource.validate(body, creating=True)
            self.assertIn(column, "; ".join(errors))

    def test_error_message_names_the_column_and_the_limit(self):
        # Quem opera a esteira lê esta mensagem na resposta da API.
        body = {**self.body, "expected_nodes": c.MIN_EXPECTED_NODES - 1}
        message = "; ".join(self.resource.validate(body, creating=True))
        self.assertIn("expected_nodes", message)
        self.assertIn(str(c.MIN_EXPECTED_NODES), message)

    def test_sql_parametrizes_the_composite_key(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = [{"data_factory": "adf"}]
            repo.get(
                MagicMock(),
                self.resource,
                {
                    "data_factory": "adf",
                    "integration_runtime": "'; DROP TABLE--",
                },
            )
        sql = query.call_args[0][1]
        for column in self.resource.key:
            self.assertIn(f"{column} = ?", sql)
        self.assertNotIn("DROP TABLE", sql)


class TestGeneratedSql(unittest.TestCase):
    """O SQL precisa parametrizar todo valor, sempre."""

    def setUp(self):
        self.conn = MagicMock()
        self.resource = resources.RESOURCES["alert-routes"]

    def _sql_from(self, mock):
        return mock.call_args[0][1]

    def test_list_parametrizes_the_filter(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = []
            repo.list_rows(self.conn, self.resource, {"domain": "'; DROP TABLE--"})

        sql = self._sql_from(query)
        self.assertIn("domain = ?", sql)
        self.assertNotIn("DROP TABLE", sql)

    def test_list_ignores_unknown_column_filter(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = []
            repo.list_rows(self.conn, self.resource, {"fake_column": "x"})
        self.assertNotIn("fake_column", self._sql_from(query))

    def test_composite_key_generates_every_condition(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = [{"domain": "Logistics"}]
            repo.get(
                self.conn,
                self.resource,
                {"domain": "Logistics", "severity": "HIGH", "channel": "teams"},
            )
        sql = self._sql_from(query)
        for column in self.resource.key:
            self.assertIn(f"{column} = ?", sql)

    def test_get_missing_row_raises(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = []
            with self.assertRaises(repo.NotFound):
                repo.get(self.conn, self.resource, {
                    "domain": "X", "severity": "HIGH", "channel": "teams"})

    def test_update_does_not_touch_the_key(self):
        with unittest.mock.patch.object(repo.db, "execute") as execute, \
             unittest.mock.patch.object(repo.db, "query") as query:
            execute.return_value = 1
            query.return_value = [{"domain": "Logistics"}]
            repo.update(
                self.conn,
                self.resource,
                {"domain": "Logistics", "severity": "HIGH", "channel": "teams"},
                {"domain": "Outro", "destination": "kv://novo"},
            )
        sql = self._sql_from(execute)
        self.assertIn("destination = ?", sql)
        self.assertNotIn("SET domain", sql)

    def test_delete_missing_row_raises(self):
        with unittest.mock.patch.object(repo.db, "execute") as execute:
            execute.return_value = 0
            with self.assertRaises(repo.NotFound):
                repo.delete(self.conn, self.resource, {
                    "domain": "X", "severity": "HIGH", "channel": "teams"})


class TestExposedResources(unittest.TestCase):
    def test_only_configuration_tables(self):
        # tbl_problems, tbl_executions e tbl_technical_events sao estado
        # quem escreve neles e a procedure. Um PUT ali permitiria alterar
        # incidente por fora da maquina de estado.
        tables = {r.table for r in resources.RESOURCES.values()}
        self.assertTrue(all(t.startswith("tbl_cfg_") for t in tables), tables)

    def test_every_key_is_among_the_columns(self):
        for resource in resources.RESOURCES.values():
            self.assertTrue(
                set(resource.key) <= set(resource.columns), resource.name
            )

    def test_every_key_is_required_on_create(self):
        for resource in resources.RESOURCES.values():
            self.assertTrue(
                set(resource.key) <= set(resource.required), resource.name
            )


class TestProblemQueries(unittest.TestCase):
    """As duas leituras de incidente usam views diferentes, nunca a tabela."""

    def setUp(self):
        self.conn = MagicMock()

    def _sql_from(self, mock):
        return mock.call_args[0][1]

    def test_open_problems_reads_the_open_view(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = []
            repo.open_problems(self.conn)
        self.assertIn("vw_open_problems", self._sql_from(query))

    def test_recently_closed_reads_the_closed_view(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = []
            repo.recently_closed_problems(self.conn)
        self.assertIn("vw_recently_closed_problems", self._sql_from(query))

    def test_recently_closed_parametrizes_the_domain_filter(self):
        with unittest.mock.patch.object(repo.db, "query") as query:
            query.return_value = []
            repo.recently_closed_problems(self.conn, domain="'; DROP TABLE--")
        sql = self._sql_from(query)
        self.assertIn("domain = ?", sql)
        self.assertNotIn("DROP TABLE", sql)


if __name__ == "__main__":
    unittest.main()
