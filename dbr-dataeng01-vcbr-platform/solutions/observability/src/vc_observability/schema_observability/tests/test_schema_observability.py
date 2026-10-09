"""Testes do coletor de mudanca de schema.

Mesmo stub de pyspark de `asset_observability/tests/test_asset_observability.py`
— o que importa testar aqui e a classificacao e a decisao de alertar, nao
a biblioteca.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

_pyspark = types.ModuleType("pyspark")
_sql = types.ModuleType("pyspark.sql")
_sql.SparkSession = MagicMock()
_sql.Row = dict
_pyspark.sql = _sql
sys.modules["pyspark"] = _pyspark
sys.modules["pyspark.sql"] = _sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import constants as c  # noqa: E402
import schema_observability as so  # noqa: E402

AGORA = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def coluna(nome: str, tipo: str, nullable: bool = True, posicao: int = 1) -> so.ColumnSnapshot:
    return so.ColumnSnapshot(
        table="cat.schema.tabela", column=nome, ordinal_position=posicao,
        data_type=tipo, is_nullable=nullable, observed_at=AGORA,
    )


def asset(consumers: tuple[str, ...] = ()) -> so.MonitoredAsset:
    return so.MonitoredAsset(
        full_name="cat.schema.tabela", domain="Logistics",
        criticality="HIGH", consumers=consumers,
    )


class TestClassifyTypeChange(unittest.TestCase):
    def test_alargamento_seguro_e_controlada(self):
        self.assertEqual(so.classify_type_change("int", "bigint"), c.CLASS_CONTROLADA)

    def test_estreitamento_e_incompativel(self):
        self.assertEqual(so.classify_type_change("bigint", "int"), c.CLASS_INCOMPATIVEL)

    def test_troca_de_tipo_nao_relacionado_e_incompativel(self):
        self.assertEqual(so.classify_type_change("string", "int"), c.CLASS_INCOMPATIVEL)

    def test_mudanca_de_precisao_do_mesmo_tipo_base_e_incompativel(self):
        # decimal(10,2) -> decimal(5,2): mesmo tipo base, mas sem saber a
        # direcao o padrao seguro e nao presumir alargamento.
        self.assertEqual(
            so.classify_type_change("decimal(10,2)", "decimal(5,2)"),
            c.CLASS_INCOMPATIVEL,
        )


class TestCompareColumns(unittest.TestCase):
    def test_coluna_nova_e_compativel(self):
        anterior = {"id": coluna("id", "int")}
        atual = {"id": coluna("id", "int"), "novo": coluna("novo", "string")}
        mudancas = so.compare_columns("cat.schema.tabela", anterior, atual, AGORA)

        self.assertEqual(len(mudancas), 1)
        self.assertEqual(mudancas[0].change_type, c.CHANGE_COLUMN_ADDED)
        self.assertEqual(mudancas[0].classification, c.CLASS_COMPATIVEL)

    def test_coluna_removida_e_incompativel(self):
        anterior = {"id": coluna("id", "int"), "antiga": coluna("antiga", "string")}
        atual = {"id": coluna("id", "int")}
        mudancas = so.compare_columns("cat.schema.tabela", anterior, atual, AGORA)

        self.assertEqual(len(mudancas), 1)
        self.assertEqual(mudancas[0].change_type, c.CHANGE_COLUMN_DROPPED)
        self.assertEqual(mudancas[0].classification, c.CLASS_INCOMPATIVEL)

    def test_tipo_alterado_usa_classify_type_change(self):
        anterior = {"id": coluna("id", "int")}
        atual = {"id": coluna("id", "bigint")}
        mudancas = so.compare_columns("cat.schema.tabela", anterior, atual, AGORA)

        self.assertEqual(len(mudancas), 1)
        self.assertEqual(mudancas[0].change_type, c.CHANGE_TYPE_CHANGED)
        self.assertEqual(mudancas[0].classification, c.CLASS_CONTROLADA)

    def test_coluna_fica_not_null_e_controlada(self):
        anterior = {"id": coluna("id", "int", nullable=True)}
        atual = {"id": coluna("id", "int", nullable=False)}
        mudancas = so.compare_columns("cat.schema.tabela", anterior, atual, AGORA)

        self.assertEqual(len(mudancas), 1)
        self.assertEqual(mudancas[0].change_type, c.CHANGE_NULLABILITY_TIGHTENED)
        self.assertEqual(mudancas[0].classification, c.CLASS_CONTROLADA)

    def test_coluna_deixa_de_ser_not_null_e_compativel(self):
        anterior = {"id": coluna("id", "int", nullable=False)}
        atual = {"id": coluna("id", "int", nullable=True)}
        mudancas = so.compare_columns("cat.schema.tabela", anterior, atual, AGORA)

        self.assertEqual(len(mudancas), 1)
        self.assertEqual(mudancas[0].change_type, c.CHANGE_NULLABILITY_RELAXED)
        self.assertEqual(mudancas[0].classification, c.CLASS_COMPATIVEL)

    def test_nada_mudou_nao_gera_evento(self):
        anterior = {"id": coluna("id", "int")}
        atual = {"id": coluna("id", "int")}
        self.assertEqual(so.compare_columns("t", anterior, atual, AGORA), [])

    def test_multiplas_mudancas_na_mesma_coluna(self):
        # Tipo e nullability podem mudar juntos no mesmo ALTER TABLE — as
        # duas mudancas sao registradas, nao so uma.
        anterior = {"id": coluna("id", "int", nullable=True)}
        atual = {"id": coluna("id", "bigint", nullable=False)}
        mudancas = so.compare_columns("t", anterior, atual, AGORA)

        tipos = {m.change_type for m in mudancas}
        self.assertEqual(tipos, {c.CHANGE_TYPE_CHANGED, c.CHANGE_NULLABILITY_TIGHTENED})


class TestTableDropped(unittest.TestCase):
    def test_tabela_removida_e_critica(self):
        mudanca = so.table_dropped_change("cat.schema.tabela", AGORA)
        self.assertEqual(mudanca.change_type, c.CHANGE_TABLE_DROPPED)
        self.assertEqual(mudanca.classification, c.CLASS_CRITICA)


class TestToEvent(unittest.TestCase):
    def test_mudanca_incompativel_com_consumidor_alerta(self):
        mudanca = so._with_asset(
            so.SchemaChange(
                table="cat.schema.tabela", domain="", severity="", column="antiga",
                change_type=c.CHANGE_COLUMN_DROPPED, classification=c.CLASS_INCOMPATIVEL,
                previous_value="string", current_value=None, detected_at=AGORA,
            ),
            asset(consumers=("Power BI — Painel",)),
        )
        evento = so.to_event(mudanca, asset(consumers=("Power BI — Painel",)))

        self.assertIsNotNone(evento)
        self.assertEqual(evento.result, "FAIL")
        self.assertEqual(evento.source, c.SOURCE_SCHEMA)
        self.assertEqual(evento.error_code, c.ERROR_CODE_INCOMPATIBLE_CHANGE)

    def test_mudanca_incompativel_sem_consumidor_nao_alerta(self):
        mudanca = so.SchemaChange(
            table="cat.schema.tabela", domain="Logistics", severity="HIGH",
            column="antiga", change_type=c.CHANGE_COLUMN_DROPPED,
            classification=c.CLASS_INCOMPATIVEL,
            previous_value="string", current_value=None, detected_at=AGORA,
        )
        self.assertIsNone(so.to_event(mudanca, asset(consumers=())))

    def test_mudanca_compativel_nunca_alerta_mesmo_com_consumidor(self):
        mudanca = so.SchemaChange(
            table="cat.schema.tabela", domain="Logistics", severity="HIGH",
            column="novo", change_type=c.CHANGE_COLUMN_ADDED,
            classification=c.CLASS_COMPATIVEL,
            previous_value=None, current_value="string", detected_at=AGORA,
        )
        self.assertIsNone(so.to_event(mudanca, asset(consumers=("Power BI",))))

    def test_mudanca_controlada_nunca_alerta(self):
        mudanca = so.SchemaChange(
            table="cat.schema.tabela", domain="Logistics", severity="HIGH",
            column="id", change_type=c.CHANGE_TYPE_CHANGED,
            classification=c.CLASS_CONTROLADA,
            previous_value="int", current_value="bigint", detected_at=AGORA,
        )
        self.assertIsNone(so.to_event(mudanca, asset(consumers=("Power BI",))))

    def test_evento_nunca_e_pass(self):
        # to_event so tem o caminho FAIL — nao existe branch de PASS neste
        # modulo (ver limitacao no docstring de schema_observability.py).
        mudanca = so.SchemaChange(
            table="t", domain="Logistics", severity="HIGH", column=None,
            change_type=c.CHANGE_TABLE_DROPPED, classification=c.CLASS_CRITICA,
            previous_value="t", current_value=None, detected_at=AGORA,
        )
        evento = so.to_event(mudanca, asset(consumers=("Power BI",)))
        self.assertEqual(evento.result, "FAIL")


class TestReadMonitoredAssets(unittest.TestCase):
    def test_precedencia_do_yaml_sobre_a_tag(self):
        tags_by_table = {"cat.schema.t": {c.TAG_DATA_DOMAIN: "Finance"}}
        overrides = {
            "cat.schema.t": {"domain": "Logistics", "consumers": ["Power BI"]}
        }
        self.assertEqual(
            so._field("cat.schema.t", tags_by_table, overrides, c.TAG_DATA_DOMAIN, "domain"),
            "Logistics",
        )

    def test_sem_override_usa_a_tag(self):
        tags_by_table = {"cat.schema.t": {c.TAG_DATA_DOMAIN: "Finance"}}
        self.assertEqual(
            so._field("cat.schema.t", tags_by_table, {}, c.TAG_DATA_DOMAIN, "domain"),
            "Finance",
        )


if __name__ == "__main__":
    unittest.main()
