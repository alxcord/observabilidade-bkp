"""Testes da replicação do hub para Delta.

pyodbc, httpx e azure-identity são substituídos por stubs: o que importa
testar é o cálculo do watermark, a serialização em CSV, o nome
determinístico do arquivo de staging e a montagem do COPY INTO — não a
biblioteca de terceiros nem um workspace Databricks real.
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


_stub("pyodbc", connect=MagicMock(), Connection=object)
_stub("azure")
_stub("azure.identity", DefaultAzureCredential=MagicMock())
_stub("httpx", Client=MagicMock())

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import constants as c  # noqa: E402
import hub_replication as hr  # noqa: E402


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class TestRowsToCsv(unittest.TestCase):
    def test_header_matches_declared_columns(self):
        content = hr.rows_to_csv([], ("a", "b", "c"))
        self.assertEqual(content.decode("utf-8").splitlines()[0], "a,b,c")

    def test_timestamps_are_serialized_as_iso(self):
        row = (1, _at("2026-09-01T12:00:00+00:00"))
        content = hr.rows_to_csv([row], ("id", "observed_at"))
        linha = content.decode("utf-8").splitlines()[1]
        self.assertIn("2026-09-01T12:00:00+00:00", linha)


class TestStagingFilename(unittest.TestCase):
    """O nome precisa ser determinístico — ver a nota no topo do módulo
    sobre por que um nome aleatório quebraria o retry após falha parcial."""

    def test_same_table_and_watermark_produce_the_same_name(self):
        table = c.SOURCE_TABLES[0]
        watermark = _at("2026-09-01T00:00:00+00:00")
        self.assertEqual(
            hr.staging_filename(table, watermark),
            hr.staging_filename(table, watermark),
        )

    def test_different_watermarks_produce_different_names(self):
        table = c.SOURCE_TABLES[0]
        a = hr.staging_filename(table, _at("2026-09-01T00:00:00+00:00"))
        b = hr.staging_filename(table, _at("2026-09-02T00:00:00+00:00"))
        self.assertNotEqual(a, b)

    def test_different_tables_never_collide(self):
        watermark = _at("2026-09-01T00:00:00+00:00")
        names = {hr.staging_filename(t, watermark) for t in c.SOURCE_TABLES}
        self.assertEqual(len(names), len(c.SOURCE_TABLES))

    def test_name_has_no_characters_unsafe_for_a_volume_path(self):
        name = hr.staging_filename(c.SOURCE_TABLES[0], _at("2026-09-01T00:00:00+00:00"))
        self.assertNotIn(":", name)
        self.assertTrue(name.endswith(".csv"))


class TestBuildCopyInto(unittest.TestCase):
    def test_targets_the_declared_schema_and_table(self):
        table = c.SOURCE_TABLES[0]
        stmt = hr.build_copy_into(c.RAW_SCHEMA, table, "tbl_executions_x.csv")
        self.assertIn(f"{c.RAW_SCHEMA}.{table.delta_table}", stmt)

    def test_restricts_to_the_single_uploaded_file(self):
        # Nunca varre o volume inteiro — evitaria recarregar algo que uma
        # limpeza externa ainda não removeu.
        stmt = hr.build_copy_into(c.RAW_SCHEMA, c.SOURCE_TABLES[0], "arquivo.csv")
        self.assertIn("FILES = ('arquivo.csv')", stmt)

    def test_matches_columns_by_header_name(self):
        stmt = hr.build_copy_into(c.RAW_SCHEMA, c.SOURCE_TABLES[0], "x.csv")
        self.assertIn("'header' = 'true'", stmt)


class TestComputeNewWatermark(unittest.TestCase):
    """O ponto que protege a purga de apagar dado não confirmado em Delta."""

    def _result(self, table, max_timestamp):
        return hr.TableResult(table=table, rows_read=1, max_timestamp=max_timestamp)

    def test_advances_to_the_minimum_of_the_maximums(self):
        previous = _at("2026-09-01T00:00:00+00:00")
        results = [
            self._result(c.SOURCE_TABLES[0], _at("2026-09-03T00:00:00+00:00")),
            self._result(c.SOURCE_TABLES[1], _at("2026-09-02T00:00:00+00:00")),
        ]
        self.assertEqual(
            hr.compute_new_watermark(results, previous), _at("2026-09-02T00:00:00+00:00")
        )

    def test_a_table_with_nothing_new_does_not_block_the_others(self):
        previous = _at("2026-09-01T00:00:00+00:00")
        results = [
            self._result(c.SOURCE_TABLES[0], _at("2026-09-05T00:00:00+00:00")),
            self._result(c.SOURCE_TABLES[1], None),
        ]
        self.assertEqual(
            hr.compute_new_watermark(results, previous), _at("2026-09-01T00:00:00+00:00")
        )

    def test_no_permanently_empty_table_is_replicated(self):
        """Guarda o motivo pelo qual `quality_results` saiu de SOURCE_TABLES.

        Uma tabela que nunca recebe linha participaria do mínimo sempre pelo
        watermark anterior, fixando-o para sempre — e a purga do hub, que
        exige `< replicated_watermark`, nunca apagaria nada. Este teste não
        detecta isso sozinho; ele fixa a lista esperada, para que incluir uma
        tabela nova seja uma decisão consciente e não um acidente.
        """
        self.assertEqual(
            [t.hub_table for t in c.SOURCE_TABLES], ["tbl_executions", "tbl_problems"]
        )

    def test_never_regresses(self):
        previous = _at("2026-09-10T00:00:00+00:00")
        results = [self._result(c.SOURCE_TABLES[0], _at("2026-09-01T00:00:00+00:00"))]
        self.assertEqual(hr.compute_new_watermark(results, previous), previous)

    def test_nothing_new_anywhere_keeps_the_previous_watermark(self):
        previous = _at("2026-09-01T00:00:00+00:00")
        results = [self._result(t, None) for t in c.SOURCE_TABLES]
        self.assertEqual(hr.compute_new_watermark(results, previous), previous)


class TestReadWatermark(unittest.TestCase):
    def test_missing_row_falls_back_to_epoch(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchone.return_value = None
        watermark = hr.read_watermark(conn)
        self.assertEqual(watermark, datetime.fromisoformat(c.EPOCH_WATERMARK))

    def test_existing_row_is_returned_as_is(self):
        conn = MagicMock()
        stored = _at("2026-08-15T00:00:00+00:00")
        conn.cursor.return_value.execute.return_value.fetchone.return_value = (stored,)
        self.assertEqual(hr.read_watermark(conn), stored)


class TestWriteWatermark(unittest.TestCase):
    def test_commits_after_updating(self):
        conn = MagicMock()
        hr.write_watermark(conn, _at("2026-09-04T00:00:00+00:00"))
        conn.commit.assert_called_once()
        update_sql = conn.cursor.return_value.execute.call_args[0][0]
        self.assertIn("UPDATE tbl_ctl_replication", update_sql)


class TestReadIncremental(unittest.TestCase):
    def test_filters_by_the_watermark_column_as_a_parameter(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchall.return_value = []
        table = c.SOURCE_TABLES[0]
        watermark = _at("2026-09-01T00:00:00+00:00")
        hr.read_incremental(conn, table, watermark)

        args = conn.cursor.return_value.execute.call_args[0]
        sql, params = args[0], args[1:]
        self.assertIn(f"{table.timestamp_column} > ?", sql)
        self.assertIn(watermark, params)
        # nunca por interpolação — o valor não aparece dentro do texto do SQL
        self.assertNotIn(watermark.isoformat(), sql)

    def test_applies_the_extra_filter_when_declared(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.return_value.fetchall.return_value = []
        table = next(t for t in c.SOURCE_TABLES if t.hub_table == "tbl_problems")
        hr.read_incremental(conn, table, _at("2026-09-01T00:00:00+00:00"))
        sql = conn.cursor.return_value.execute.call_args[0][0]
        self.assertIn("incident_status = 'RESOLVED'", sql)


if __name__ == "__main__":
    unittest.main()
