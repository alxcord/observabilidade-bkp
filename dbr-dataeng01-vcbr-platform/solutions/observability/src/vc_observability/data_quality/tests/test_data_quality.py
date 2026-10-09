"""Testes do motor de Data Quality.

pyspark e substituido por um stub: o que importa testar e a traducao da
regra em SQL e a classificacao do resultado, nao a biblioteca.
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
_types = types.ModuleType("pyspark.sql.types")
for _nome in ("BooleanType", "DoubleType", "LongType", "StringType",
              "StructField", "StructType", "TimestampType"):
    setattr(_types, _nome, MagicMock())
_pyspark.sql = _sql
_sql.types = _types
sys.modules["pyspark"] = _pyspark
sys.modules["pyspark.sql"] = _sql
sys.modules["pyspark.sql.types"] = _types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import constants as c  # noqa: E402
import data_quality as dq  # noqa: E402

AGORA = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
YAML_REAL = (
    Path(__file__).resolve().parents[4]
    / "config"
    / "quality-rules"
    / "logistics.yaml"
)


def rule(**kwargs) -> dq.Rule:
    padrao = dict(
        id="teste",
        version="1",
        rule_type=c.TYPE_NOT_NULL,
        asset="cat.sch.tbl",
        columns=["col"],
        blocking=True,
    )
    padrao.update(kwargs)
    return dq.Rule(**padrao)


def result(invalid: int, evaluated: int = 100, **kwargs) -> dq.Result:
    padrao = dict(
        rule=rule(**kwargs.pop("rule_kwargs", {})),
        evaluation_status=c.STATUS_SUCCESS,
        quality_outcome=c.OUTCOME_PASS,
        evaluated_row_count=evaluated,
        invalid_row_count=invalid,
        executed_at=AGORA,
    )
    padrao.update(kwargs)
    return dq.Result(**padrao)


class TestCargaDoYaml(unittest.TestCase):
    """A carga precisa funcionar contra o YAML que está no repositório."""

    def test_carrega_o_arquivo_de_exemplo(self):
        rules = dq.load_rules(str(YAML_REAL))
        self.assertEqual(len(rules), 6)
        self.assertEqual({r.rule_type for r in rules} - c.SUPPORTED_TYPES, set())

    def test_toda_regra_do_exemplo_gera_sql(self):
        for r in dq.load_rules(str(YAML_REAL)):
            self.assertIn("SELECT", dq.build_query(r), r.id)

    def test_tipo_desconhecido_interrompe_a_carga(self):
        # Regra que nao roda em silencio da falsa sensacao de cobertura.
        file = Path(self.enterContext(_tmp())) / "r.yaml"
        file.write_text(
            "rules:\n  - id: x\n    type: inexistente\n    asset: a.b.c\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as error:
            dq.load_rules(str(file))
        self.assertIn("inexistente", str(error.exception))


def _tmp():
    import tempfile

    return tempfile.TemporaryDirectory()


class TestMontarConsulta(unittest.TestCase):
    def test_nao_nulo_cobre_todas_as_colunas(self):
        sql = dq.build_query(rule(columns=["a", "b"]))
        self.assertIn("a IS NULL OR b IS NULL", sql)

    def test_dominio_lista_os_valores(self):
        sql = dq.build_query(
            rule(rule_type=c.TYPE_DOMAIN, parameters={"values": ["X", "Y"]})
        )
        self.assertIn("NOT IN ('X', 'Y')", sql)

    def test_intervalo_com_os_dois_limites(self):
        sql = dq.build_query(
            rule(rule_type=c.TYPE_RANGE, parameters={"min": 0, "max": 10})
        )
        self.assertIn("col < 0", sql)
        self.assertIn("col > 10", sql)

    def test_intervalo_sem_limite_e_erro(self):
        with self.assertRaises(ValueError):
            dq.build_query(rule(rule_type=c.TYPE_RANGE, parameters={}))

    def test_unico_conta_as_repetidas_alem_da_primeira(self):
        sql = dq.build_query(rule(rule_type=c.TYPE_UNIQUE))
        self.assertIn("SUM(repetidas - 1)", sql)

    def test_referencia_usa_left_join(self):
        sql = dq.build_query(
            rule(
                rule_type=c.TYPE_REFERENCE,
                parameters={
                    "reference_table": "cat.sch.ref",
                    "reference_column": "id",
                },
            )
        )
        self.assertIn("LEFT JOIN cat.sch.ref", sql)


class TestClassificacao(unittest.TestCase):
    def test_sem_invalidas_passa(self):
        self.assertEqual(dq._classify(result(0)), c.OUTCOME_PASS)

    def test_dentro_do_limite_passa(self):
        r = result(1, rule_kwargs={"threshold_pct": 2.0})
        self.assertEqual(dq._classify(r), c.OUTCOME_PASS)

    def test_bloqueante_acima_do_limite_falha(self):
        r = result(10, rule_kwargs={"blocking": True, "threshold_pct": 0})
        self.assertEqual(dq._classify(r), c.OUTCOME_FAIL)

    def test_nao_bloqueante_acima_do_limite_alerta(self):
        # WARN merece atencao, mas nao e falha formal e nao abre incidente.
        r = result(10, rule_kwargs={"blocking": False, "threshold_pct": 0})
        self.assertEqual(dq._classify(r), c.OUTCOME_WARN)


class TestExecucao(unittest.TestCase):
    def test_falha_tecnica_nao_e_dado_reprovado(self):
        # ERROR com UNKNOWN, nunca FAIL: tabela inacessivel nao e dado ruim,
        # e contar assim inverteria o diagnostico de quem investiga.
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("tabela nao encontrada")

        r = dq.execute(spark, rule(), AGORA)

        self.assertEqual(r.evaluation_status, c.STATUS_ERROR)
        self.assertEqual(r.quality_outcome, c.OUTCOME_UNKNOWN)
        self.assertIn("nao encontrada", r.error)

    def test_execucao_com_sucesso_classifica(self):
        spark = MagicMock()
        spark.sql.return_value.collect.return_value = [
            {"avaliadas": 100, "invalidas": 5}
        ]

        r = dq.execute(spark, rule(blocking=True, threshold_pct=0), AGORA)

        self.assertEqual(r.evaluation_status, c.STATUS_SUCCESS)
        self.assertEqual(r.quality_outcome, c.OUTCOME_FAIL)
        self.assertEqual(r.invalid_pct, 5.0)


class TestIdempotencia(unittest.TestCase):
    def test_mesmo_resultado_gera_mesmo_id(self):
        self.assertEqual(result(0).result_id, result(0).result_id)

    def test_versao_da_regra_muda_o_id(self):
        a = result(0, rule_kwargs={"version": "1"})
        b = result(0, rule_kwargs={"version": "2"})
        self.assertNotEqual(a.result_id, b.result_id)


class TestEventosParaOHub(unittest.TestCase):
    def test_so_bloqueantes_viram_evento(self):
        spark = MagicMock()
        dq.write_events(
            spark,
            [
                result(10, rule_kwargs={"blocking": False}),
                result(0, rule_kwargs={"blocking": False}),
            ],
        )
        spark.createDataFrame.assert_not_called()

    def test_pass_de_bloqueante_tambem_vira_evento(self):
        # E o que fecha o incidente aberto por um FAIL anterior. Enviar so as
        # falhas faria o incidente abrir e nunca fechar.
        spark = MagicMock()
        dq.write_events(
            spark, [result(0, rule_kwargs={"blocking": True})]
        )
        spark.createDataFrame.assert_called_once()
        rows = spark.createDataFrame.call_args[0][0]
        # Nomes de campo em inglês: são as colunas físicas da tabela de
        # staging (ver a nota em gravar_eventos), por isso o teste lê por
        # elas mesmo o resto do módulo permanecendo em português.
        self.assertEqual(rows[0]["result"], c.OUTCOME_PASS)
        self.assertIsNone(rows[0]["error_code"])


if __name__ == "__main__":
    unittest.main()
