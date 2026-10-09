"""Testes do coletor de freshness e volume.

O modulo sob teste importa pyspark, que nao esta disponivel fora do
Databricks. Ele e substituido por um stub antes da importacao: o que
importa testar aqui e a regra de avaliacao, nao a biblioteca.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

class _StructField:
    def __init__(self, name, data_type, nullable=True):
        self.name = name
        self.dataType = data_type
        self.nullable = nullable


class _StructType:
    def __init__(self, fields=None):
        self.fields = fields or []


_pyspark = types.ModuleType("pyspark")
_sql = types.ModuleType("pyspark.sql")
_sql.SparkSession = MagicMock()
_sql.Row = dict
_types = types.ModuleType("pyspark.sql.types")
_types.StructField = _StructField
_types.StructType = _StructType
_types.StringType = type("StringType", (), {})
_types.LongType = type("LongType", (), {})
_types.BooleanType = type("BooleanType", (), {})
_types.TimestampType = type("TimestampType", (), {})
_sql.types = _types
_pyspark.sql = _sql
sys.modules["pyspark"] = _pyspark
sys.modules["pyspark.sql"] = _sql
sys.modules["pyspark.sql.types"] = _types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import constants as c  # noqa: E402
import asset_observability as oa  # noqa: E402

AGORA = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def asset(**kwargs) -> oa.Asset:
    default = dict(
        full_name="cat.schema.tabela",
        domain="Logistics",
        criticality="HIGH",
        freshness_sla_minutes=24 * 60,
        volume=oa.VolumeRule(),
    )
    default.update(kwargs)
    return oa.Asset(**default)


def observacao(
    horas_atras: float = 1,
    tamanho: int = 1000,
    linhas: int | None = 1000,
    dia: int | None = None,
) -> oa.Observation:
    observado = AGORA if dia is None else AGORA.replace(day=dia)
    return oa.Observation(
        asset="cat.schema.tabela",
        updated_at=observado - timedelta(hours=horas_atras),
        size_bytes=tamanho,
        file_count=10,
        row_count=linhas,
        observed_at=observado,
    )


class TestFreshness(unittest.TestCase):
    def test_dentro_do_prazo_passa(self):
        evento = oa.evaluate_freshness(asset(), observacao(horas_atras=5))
        self.assertEqual(evento.result, "PASS")
        self.assertIsNone(evento.error_code)

    def test_fora_do_prazo_falha(self):
        evento = oa.evaluate_freshness(asset(), observacao(horas_atras=30))
        self.assertEqual(evento.result, "FAIL")
        self.assertEqual(evento.error_code, c.ERROR_CODE_STALE)
        self.assertIn("30.0h", evento.description)  # _humanize em horas

    def test_exatamente_no_limite_passa(self):
        evento = oa.evaluate_freshness(asset(), observacao(horas_atras=24))
        self.assertEqual(evento.result, "PASS")

    def test_severidade_vem_da_criticidade(self):
        alta = oa.evaluate_freshness(asset(criticality="CRITICAL"), observacao(30))
        baixa = oa.evaluate_freshness(asset(criticality="LOW"), observacao(30))
        self.assertEqual(alta.severity, "HIGH")
        self.assertEqual(baixa.severity, "LOW")

    def test_criticidade_desconhecida_usa_padrao(self):
        evento = oa.evaluate_freshness(asset(criticality="?"), observacao(30))
        self.assertEqual(evento.severity, c.DEFAULT_SEVERITY)


class TestVolume(unittest.TestCase):
    """A metrica agora e `row_count`, nao bytes."""

    def test_variacao_dentro_da_faixa_passa(self):
        evento = oa.evaluate_volume(
            asset(), observacao(linhas=1100), observacao(linhas=1000)
        )
        self.assertEqual(evento.result, "PASS")

    def test_queda_abaixo_do_minimo_percentual_falha(self):
        evento = oa.evaluate_volume(
            asset(), observacao(linhas=500), observacao(linhas=1000)
        )
        self.assertEqual(evento.result, "FAIL")
        self.assertEqual(evento.error_code, c.ERROR_CODE_VOLUME)

    def test_crescimento_acima_do_maximo_percentual_falha(self):
        # Reprocessamento que duplica registros e tao problematico quanto
        # uma carga que nao veio.
        evento = oa.evaluate_volume(
            asset(), observacao(linhas=2000), observacao(linhas=1000)
        )
        self.assertEqual(evento.result, "FAIL")

    def test_limites_assimetricos(self):
        """O ganho de sair da tag: um numero para cada lado.

        A tolerancia unica anterior comparava abs(variacao) e forcava o
        mesmo limite nos dois sentidos.
        """
        regra = oa.VolumeRule(variation_min_pct=-10, variation_max_pct=100)
        cresceu = oa.evaluate_volume(
            asset(volume=regra), observacao(linhas=1900), observacao(linhas=1000)
        )
        encolheu = oa.evaluate_volume(
            asset(volume=regra), observacao(linhas=850), observacao(linhas=1000)
        )
        self.assertEqual(cresceu.result, "PASS")
        self.assertEqual(encolheu.result, "FAIL")

    def test_minimo_absoluto_pega_o_que_o_percentual_nao_pega(self):
        """Tabela pequena e estavel: percentualmente OK, faticamente quebrada."""
        regra = oa.VolumeRule(row_count_min=1000)
        evento = oa.evaluate_volume(
            asset(volume=regra), observacao(linhas=10), observacao(linhas=10)
        )
        self.assertEqual(evento.result, "FAIL")
        self.assertIn("abaixo do mínimo", evento.description)

    def test_maximo_absoluto(self):
        regra = oa.VolumeRule(row_count_max=100)
        evento = oa.evaluate_volume(
            asset(volume=regra), observacao(linhas=150), observacao(linhas=140)
        )
        self.assertEqual(evento.result, "FAIL")
        self.assertIn("acima do máximo", evento.description)

    def test_percentual_e_absoluto_valem_por_AND(self):
        """O mais apertado vence; um limite nunca afrouxa o outro."""
        regra = oa.VolumeRule(
            variation_min_pct=-90, variation_max_pct=90, row_count_min=1000
        )
        evento = oa.evaluate_volume(
            asset(volume=regra), observacao(linhas=900), observacao(linhas=1000)
        )
        self.assertEqual(evento.result, "FAIL")

    def test_tabela_vazia_falha_mesmo_sem_referencia(self):
        evento = oa.evaluate_volume(asset(), observacao(linhas=0), None)
        self.assertEqual(evento.result, "FAIL")
        self.assertEqual(evento.error_code, c.ERROR_CODE_EMPTY)

    def test_primeira_execucao_passa(self):
        # Sem referencia nao ha variacao a calcular. Inventar um veredito
        # aqui geraria alerta falso no dia da implantacao.
        evento = oa.evaluate_volume(asset(), observacao(linhas=1000), None)
        self.assertEqual(evento.result, "PASS")

    def test_warm_up_sem_row_count_anterior_so_aplica_absolutos(self):
        """Historico gravado antes de row_count existir."""
        evento = oa.evaluate_volume(
            asset(), observacao(linhas=1000), observacao(linhas=None)
        )
        self.assertEqual(evento.result, "PASS")

    def test_sem_row_count_atual_nao_avalia_volume(self):
        """COUNT(*) falhou: freshness continua, volume fica de fora."""
        evento = oa.evaluate_volume(
            asset(), observacao(linhas=None), observacao(linhas=1000)
        )
        self.assertEqual(evento.result, "PASS")

    def test_metrica_bytes_ainda_e_possivel(self):
        regra = oa.VolumeRule(metric=c.METRIC_BYTES, row_count_min=None)
        evento = oa.evaluate_volume(
            asset(volume=regra),
            observacao(tamanho=500, linhas=1000),
            observacao(tamanho=1000, linhas=1000),
        )
        self.assertEqual(evento.result, "FAIL")


class TestExcecaoDeVolume(unittest.TestCase):
    """Expurgo mensal: afrouxa o limite, nunca desliga a regra."""

    def _regra(self):
        return oa.VolumeRule(
            variation_min_pct=-10,
            exceptions=(
                oa.VolumeException(
                    reason="Expurgo mensal",
                    days_of_month=(1, 2, 3),
                    variation_min_pct=-65,
                ),
            ),
        )

    def test_dentro_da_janela_a_queda_esperada_passa(self):
        evento = oa.evaluate_volume(
            asset(volume=self._regra()),
            observacao(linhas=500, dia=2),
            observacao(linhas=1000, dia=1),
        )
        self.assertEqual(evento.result, "PASS")

    def test_fora_da_janela_a_mesma_queda_falha(self):
        evento = oa.evaluate_volume(
            asset(volume=self._regra()),
            observacao(linhas=500, dia=15),
            observacao(linhas=1000, dia=14),
        )
        self.assertEqual(evento.result, "FAIL")

    def test_a_excecao_afrouxa_mas_nao_desliga(self):
        """O dia do expurgo e quando um DELETE errado passa despercebido.

        Se a janela desativasse a regra, ela viraria ponto cego mensal.
        Uma queda de 90% no dia 2, com -65% declarado, continua incidente.
        """
        evento = oa.evaluate_volume(
            asset(volume=self._regra()),
            observacao(linhas=100, dia=2),
            observacao(linhas=1000, dia=1),
        )
        self.assertEqual(evento.result, "FAIL")

    def test_limite_nao_declarado_na_excecao_continua_valendo(self):
        regra = oa.VolumeRule(
            variation_max_pct=20,
            exceptions=(
                oa.VolumeException(
                    reason="Expurgo", days_of_month=(1,), variation_min_pct=-90
                ),
            ),
        )
        evento = oa.evaluate_volume(
            asset(volume=regra),
            observacao(linhas=2000, dia=1),
            observacao(linhas=1000, dia=1),
        )
        self.assertEqual(evento.result, "FAIL")


class TestParseVolumeRule(unittest.TestCase):
    def test_bloco_ausente_vira_regra_padrao(self):
        self.assertEqual(oa.parse_volume_rule(None, "cat.sch.t"), oa.VolumeRule())

    def test_le_todos_os_campos(self):
        regra = oa.parse_volume_rule(
            {
                "metric": "row_count",
                "variation_min_pct": -10,
                "variation_max_pct": 40,
                "row_count_min": 50000,
                "row_count_max": 80000000,
                "exceptions": [
                    {
                        "reason": "Expurgo mensal",
                        "days_of_month": [1, 2, 3],
                        "variation_min_pct": -65,
                    }
                ],
            },
            "cat.sch.t",
        )
        self.assertEqual(regra.variation_min_pct, -10.0)
        self.assertEqual(regra.row_count_max, 80000000)
        self.assertEqual(regra.exceptions[0].days_of_month, (1, 2, 3))

    def test_bloco_invalido_cai_no_padrao_sem_derrubar(self):
        """Erro de digitacao no YAML nao pode tirar do ar a observacao toda."""
        regra = oa.parse_volume_rule({"metric": "gigabytes"}, "cat.sch.t")
        self.assertEqual(regra, oa.VolumeRule())


class TestParseFrequency(unittest.TestCase):
    def test_cada_unidade(self):
        casos = {
            "5min": 5, "1h": 60, "1d": 1440, "1w": 10080,
            "1m": 43200, "1y": 525600,
        }
        for valor, esperado in casos.items():
            with self.subTest(valor=valor):
                self.assertEqual(oa.parse_frequency(valor), esperado)

    def test_min_nao_e_lido_como_mes(self):
        """A ordem da alternancia no regex decide isto.

        Com `m` antes de `min`, "5min" casaria "5m" — 5 meses em vez de 5
        minutos, um erro de fator 8.640.
        """
        self.assertEqual(oa.parse_frequency("5min"), 5)
        self.assertEqual(oa.parse_frequency("5m"), 5 * 30 * 1440)  # 5 meses

    def test_aceita_maiuscula_e_espaco(self):
        self.assertEqual(oa.parse_frequency("  1D  "), 1440)

    def test_valor_desconhecido_levanta(self):
        """O ponto do bloco: falhar alto, nunca cair num default calado.

        O mapa de enum anterior devolvia 24h para qualquer coisa fora dele.
        Uma tabela declarando "DAILY" no formato antigo passaria a ser
        avaliada com prazo de 24h por coincidencia, e uma declarando "5min"
        tambem — 288 vezes a janela pretendida, sem erro e sem log.
        """
        for invalido in ["DAILY", "QUINZENAL", "1", "h", "1 d", "-1d", "1x"]:
            with self.subTest(valor=invalido):
                with self.assertRaises(ValueError):
                    oa.parse_frequency(invalido)


class TestSlaEmMinutos(unittest.TestCase):
    def test_sla_explicito_manda(self):
        tags = {c.TAG_REFRESH_FREQUENCY: "1d", c.TAG_FRESHNESS_SLA: "6h"}
        self.assertEqual(oa._sla_in_minutes(tags), 360)

    def test_sem_sla_usa_a_frequencia(self):
        self.assertEqual(oa._sla_in_minutes({c.TAG_REFRESH_FREQUENCY: "4h"}), 240)

    def test_frequencia_invalida_propaga_o_erro(self):
        with self.assertRaises(ValueError):
            oa._sla_in_minutes({c.TAG_REFRESH_FREQUENCY: "DAILY"})


class TestIdadeEmMinutos(unittest.TestCase):
    def test_calcula_minutos_desde_a_atualizacao(self):
        self.assertAlmostEqual(observacao(horas_atras=7.5).age_minutes, 450)


class _LinhaFalsa:
    """Linha do information_schema, acessada por atributo como no Spark."""

    def __init__(self, **campos):
        self.__dict__.update(campos)


def _spark_com(tags_de_schema, tags_de_tabela, tabelas_sem_tag=()):
    """SparkSession de mentira que responde as consultas de tag e de tabelas.

    Despacha pela presenca de "schema_tags" no SQL — e o suficiente para
    exercitar a heranca sem subir um catalogo de verdade.
    """
    spark = MagicMock()

    def sql(consulta):
        resultado = MagicMock()
        if "information_schema.tables" in consulta:
            resultado.collect.return_value = [
                _LinhaFalsa(table_catalog="cat", table_schema="sch",
                            table_name=tabela)
                for tabela in [*tags_de_tabela, *tabelas_sem_tag]
            ]
        elif "schema_tags" in consulta:
            resultado.collect.return_value = [
                _LinhaFalsa(tag_name=k, tag_value=v)
                for k, v in tags_de_schema.items()
            ]
        else:
            resultado.collect.return_value = [
                _LinhaFalsa(catalog_name="cat", schema_name="sch",
                            table_name=tabela, tag_name=k, tag_value=v)
                for tabela, tags in tags_de_tabela.items()
                for k, v in tags.items()
            ]
        return resultado

    spark.sql.side_effect = sql
    return spark


class TestHerancaDeTagDoSchema(unittest.TestCase):
    """vc_data_domain e aplicada no SCHEMA, nunca na tabela.

    Lendo so `table_tags`, o dominio nunca seria encontrado e todo ativo
    cairia em DEFAULT_DOMAIN. O alerta continuaria chegando — sempre no
    mesmo canal —, e o roteamento por dominio deixaria de existir sem
    ninguem perceber.
    """

    def test_tag_do_schema_chega_na_tabela(self):
        spark = _spark_com(
            {c.TAG_DATA_DOMAIN: "log"},
            {"t": {c.TAG_REFRESH_FREQUENCY: "1d"}},
        )
        assets = oa.read_assets(spark, "cat", "sch")
        self.assertEqual([a.domain for a in assets], ["log"])

    def test_tag_da_tabela_vence_a_do_schema(self):
        spark = _spark_com(
            {c.TAG_DATA_DOMAIN: "log"},
            {"t": {c.TAG_REFRESH_FREQUENCY: "1d", c.TAG_DATA_DOMAIN: "fin"}},
        )
        assets = oa.read_assets(spark, "cat", "sch")
        self.assertEqual([a.domain for a in assets], ["fin"])

    def test_sem_dominio_em_lugar_nenhum_cai_no_padrao(self):
        spark = _spark_com({}, {"t": {c.TAG_REFRESH_FREQUENCY: "1d"}})
        assets = oa.read_assets(spark, "cat", "sch")
        self.assertEqual([a.domain for a in assets], [c.DEFAULT_DOMAIN])


class TestExpectativaPorFrequencia(unittest.TestCase):
    """Toda frequencia valida gera expectativa, por menor que seja.

    O job ve a tabela so quando roda, mas, se nesse momento ela esta parada
    ha mais que o prazo, o FAIL e verdadeiro.
    """

    def _slas(self, assets):
        return {a.full_name: a.freshness_sla_minutes for a in assets}

    def test_frequencia_sub_diaria_tem_sla(self):
        spark = _spark_com(
            {},
            {
                "rapida": {c.TAG_REFRESH_FREQUENCY: "5min"},
                "horaria": {c.TAG_REFRESH_FREQUENCY: "1h"},
                "diaria": {c.TAG_REFRESH_FREQUENCY: "1d"},
            },
        )
        slas = self._slas(oa.read_assets(spark, "cat", "sch"))
        self.assertEqual(
            slas,
            {"cat.sch.rapida": 5 * c.MINUTES_PER_UNIT["min"],
             "cat.sch.horaria": c.MINUTES_PER_UNIT["h"],
             "cat.sch.diaria": c.MINUTES_PER_UNIT["d"]},
        )

    def test_tabela_horaria_atrasada_falha(self):
        horaria = asset(freshness_sla_minutes=oa.parse_frequency("1h"))
        evento = oa.evaluate_freshness(horaria, observacao(horas_atras=3))
        self.assertEqual(evento.result, "FAIL")
        self.assertEqual(evento.error_code, c.ERROR_CODE_STALE)

    def test_tabela_horaria_vazia_falha_por_volume(self):
        horaria = asset(freshness_sla_minutes=oa.parse_frequency("1h"))
        evento = oa.evaluate_volume(horaria, observacao(linhas=0), observacao(linhas=100))
        self.assertEqual(evento.error_code, c.ERROR_CODE_EMPTY)

    def test_sem_tag_de_frequencia_e_coletada_sem_sla(self):
        spark = _spark_com({}, {"t": {c.TAG_CRITICALITY: "HIGH"}})
        self.assertEqual(self._slas(oa.read_assets(spark, "cat", "sch")),
                         {"cat.sch.t": None})

    def test_tabela_sem_nenhuma_tag_e_coletada(self):
        spark = _spark_com({}, {}, tabelas_sem_tag=["nua"])
        self.assertEqual(self._slas(oa.read_assets(spark, "cat", "sch")),
                         {"cat.sch.nua": None})

    def test_frequencia_invalida_nao_derruba_os_outros(self):
        """Um valor mal preenchido nao pode parar a varredura inteira."""
        spark = _spark_com(
            {},
            {
                "quebrada": {c.TAG_REFRESH_FREQUENCY: "DAILY"},
                "boa": {c.TAG_REFRESH_FREQUENCY: "1d"},
            },
        )
        self.assertEqual(
            self._slas(oa.read_assets(spark, "cat", "sch")),
            {"cat.sch.quebrada": None, "cat.sch.boa": c.MINUTES_PER_UNIT["d"]},
        )


class TestDestinosPorAmbiente(unittest.TestCase):
    def test_dev_grava_em_dev_governance(self):
        d = oa.Destinations.for_environment("dev")
        self.assertEqual(
            d.observations,
            "dev_governance.corp_cleansed.tbl_dbr_it_asset_observability",
        )
        self.assertTrue(d.events.startswith("dev_governance."))
        self.assertTrue(d.commit_metrics.startswith("dev_governance."))

    def test_prd_grava_em_prd_governance(self):
        d = oa.Destinations.for_environment("prd")
        for tabela in (d.observations, d.events, d.commit_metrics):
            self.assertTrue(tabela.startswith("prd_governance."))


def _spark_de_schema(tabelas, detalhe_por_tabela=None, erro_por_tabela=None):
    """SparkSession falsa que responde a varredura de UM schema.

    `detalhe_por_tabela` e `erro_por_tabela` sao indexados pelo nome curto
    da tabela: o primeiro devolve a linha do DESCRIBE DETAIL, o segundo
    levanta a excecao informada, para simular tabela orfa ou falha de
    permissao.
    """
    detalhe_por_tabela = detalhe_por_tabela or {}
    erro_por_tabela = erro_por_tabela or {}
    spark = MagicMock()
    # O Spark real entrega TIMESTAMP sem tzinfo; o fuso vem desta conf.
    spark.conf.get.return_value = c.DEFAULT_SESSION_TIMEZONE

    def sql(consulta):
        r = MagicMock()
        if "information_schema.tables" in consulta:
            r.collect.return_value = [
                _LinhaFalsa(table_catalog="cat", table_schema="sch", table_name=t)
                for t in tabelas
            ]
        elif "DESCRIBE DETAIL" in consulta or "DESCRIBE HISTORY" in consulta:
            curta = consulta.strip().rsplit(".", 1)[-1].strip()
            if curta in erro_por_tabela:
                raise erro_por_tabela[curta]
            if "DESCRIBE HISTORY" in consulta:
                r.collect.return_value = []
            else:
                r.collect.return_value = [detalhe_por_tabela.get(
                    curta,
                    {"lastModified": AGORA, "sizeInBytes": 10, "numFiles": 1},
                )]
        elif "COUNT(*)" in consulta:
            r.collect.return_value = [{"n": 5}]
        else:
            r.collect.return_value = []
        return r

    spark.sql.side_effect = sql
    return spark


class TestProcessSoAvaliaQuemTemSla(unittest.TestCase):
    def test_sem_sla_coleta_mas_nao_gera_evento(self):
        spark = _spark_de_schema(["t"])
        obs, eventos, _, cobertura = oa.process(
            spark, "cat", "sch", AGORA, oa.Destinations.for_environment("dev")
        )
        self.assertEqual([o.asset for o in obs], ["cat.sch.t"])
        self.assertEqual(eventos, [])
        self.assertEqual(cobertura.com_expectativa, 0)
        self.assertEqual(cobertura.observados, 1)


class TestFusoDosTimestampsDoSpark(unittest.TestCase):
    """O PySpark entrega TIMESTAMP sem tzinfo; `now_utc` nasce aware.

    Subtrair os dois levanta TypeError. O fake antigo devolvia
    `lastModified` ja aware — mais bem-comportado que o Spark real — e por
    isso o defeito que derrubou dev_governance.corp_cleansed em 2026-10-07
    atravessou a suite inteira sem ser visto.
    """

    def test_describe_normaliza_lastmodified_naive(self):
        naive = (AGORA - timedelta(hours=30)).replace(tzinfo=None)
        spark = _spark_de_schema(
            ["t"],
            {"t": {"lastModified": naive, "sizeInBytes": 10, "numFiles": 1}},
        )

        obs = oa.describe(
            spark, asset(full_name="cat.sch.t"), AGORA, oa.session_timezone(spark)
        )

        self.assertIsNotNone(obs.updated_at.tzinfo)
        self.assertEqual(obs.age_minutes, 30 * 60)

    def test_as_utc_interpreta_naive_no_fuso_da_sessao(self):
        # Offset fixo em vez de nome de fuso: nao depende da base tzdata,
        # ausente numa instalacao Windows limpa.
        convertido = oa._as_utc(
            datetime(2026, 9, 4, 12, 0), timezone(timedelta(hours=-3))
        )
        self.assertEqual(
            convertido, datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
        )

    def test_as_utc_converte_valor_que_ja_vem_aware(self):
        aware = datetime(2026, 9, 4, 15, 0, tzinfo=timezone(timedelta(hours=-3)))
        self.assertEqual(
            oa._as_utc(aware, timezone.utc),
            datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc),
        )

    def test_as_utc_preserva_ausencia_de_valor(self):
        self.assertIsNone(oa._as_utc(None, timezone.utc))

    def test_sessao_em_utc_nao_depende_da_base_de_fusos(self):
        spark = MagicMock()
        spark.conf.get.return_value = c.DEFAULT_SESSION_TIMEZONE
        self.assertEqual(oa.session_timezone(spark), timezone.utc)

    def test_fuso_desconhecido_cai_no_padrao_com_aviso(self):
        spark = MagicMock()
        spark.conf.get.return_value = "Marte/Olympus_Mons"
        with self.assertLogs(oa.logger, level="WARNING") as registro:
            self.assertEqual(oa.session_timezone(spark), timezone.utc)
        self.assertIn(c.DEFAULT_SESSION_TIMEZONE, "\n".join(registro.output))

    def test_conf_ilegivel_cai_no_padrao_com_aviso(self):
        spark = MagicMock()
        spark.conf.get.side_effect = RuntimeError("sessao encerrada")
        with self.assertLogs(oa.logger, level="WARNING"):
            self.assertEqual(oa.session_timezone(spark), timezone.utc)

    def test_falha_de_avaliacao_de_um_ativo_nao_derruba_os_outros(self):
        """Regressao do dano real: um ativo levava o schema inteiro embora."""
        naive = (AGORA - timedelta(hours=30)).replace(tzinfo=None)
        spark = _spark_de_schema(
            ["a", "b"],
            {t: {"lastModified": naive, "sizeInBytes": 10, "numFiles": 1}
             for t in ("a", "b")},
        )

        # Normalizacao desativada de proposito: reproduz o TypeError
        # original dentro de evaluate_freshness, com os dois ativos tendo
        # expectativa declarada.
        with patch.object(oa, "_as_utc", lambda valor, origem: valor), \
             patch.object(
                 oa, "read_assets",
                 return_value=[asset(full_name="cat.sch.a"),
                               asset(full_name="cat.sch.b")],
             ), \
             self.assertLogs(oa.logger, level="ERROR"):
            obs, eventos, _, cobertura = oa.process(
                spark, "cat", "sch", AGORA,
                oa.Destinations.for_environment("dev"),
            )

        # As observacoes dos DOIS ativos sobrevivem a falha de avaliacao.
        self.assertEqual(sorted(o.asset for o in obs),
                         ["cat.sch.a", "cat.sch.b"])
        self.assertEqual(cobertura.observados, 2)
        # E o isolamento e por avaliador, nao por ativo: freshness quebrou,
        # volume seguiu e emitiu o evento dos dois ativos.
        self.assertEqual(
            [e.source for e in eventos],
            [c.SOURCE_VOLUME, c.SOURCE_VOLUME],
        )


class TestTabelaExternaOrfa(unittest.TestCase):
    """DELTA_PATH_DOES_NOT_EXIST: registro no catalogo, caminho inexistente."""

    def _erro_de_path(self, caminho):
        return Exception(
            "[" + c.ERRO_PATH_AUSENTE + "] " + caminho + " doesn't exist, or "
            "is not a Delta table.;\nDescribeDeltaDetailCommand\n"
            "+- ResolvedTable UnityCatalogV2Proxy ..."
        )

    def test_reconhece_o_codigo_de_erro(self):
        self.assertTrue(oa._path_ausente(self._erro_de_path("abfss://c@s/x")))

    def test_nao_confunde_com_tabela_ausente_do_bootstrap(self):
        bootstrap = Exception("[" + c.ERRO_TABELA_AUSENTE + "] nao existe")
        self.assertFalse(oa._path_ausente(bootstrap))
        self.assertTrue(oa._tabela_ausente(bootstrap))

    def test_extrai_o_caminho_da_mensagem(self):
        caminho = "abfss://cleansed@conta.dfs.core.windows.net/Industrial/AFR/t"
        self.assertEqual(
            oa._caminho_ausente(self._erro_de_path(caminho)), caminho
        )

    def test_sem_caminho_na_mensagem_devolve_vazio(self):
        self.assertEqual(oa._caminho_ausente(Exception("outro erro")), "")

    def test_orfa_entra_na_cobertura_e_nao_derruba_o_schema(self):
        spark = _spark_de_schema(
            ["boa", "orfa"],
            erro_por_tabela={"orfa": self._erro_de_path("abfss://c@s/orfa")},
        )

        with self.assertLogs(oa.logger, level="WARNING"):
            obs, _, _, cobertura = oa.process(
                spark, "cat", "sch", AGORA,
                oa.Destinations.for_environment("dev"),
            )

        # As DUAS linhas vao para o Delta: a boa e a tentativa da orfa.
        self.assertEqual(sorted(o.asset for o in obs),
                         ["cat.sch.boa", "cat.sch.orfa"])
        self.assertEqual(cobertura.orfaos, ("cat.sch.orfa",))
        self.assertEqual(cobertura.outras_falhas, ())
        self.assertEqual(cobertura.coletados, 2)
        # ...mas so UMA foi lida de verdade.
        self.assertEqual(cobertura.observados, 1)
        self.assertEqual(cobertura.sem_observacao, 1)

    def test_linha_de_tentativa_tem_metricas_zeradas_e_updated_at_nulo(self):
        spark = _spark_de_schema(
            ["orfa"],
            erro_por_tabela={"orfa": self._erro_de_path("abfss://c@s/orfa")},
        )

        with self.assertLogs(oa.logger, level="WARNING"):
            obs, eventos, _, _ = oa.process(
                spark, "cat", "sch", AGORA,
                oa.Destinations.for_environment("dev"),
            )

        linha = obs[0]
        self.assertEqual(linha.asset, "cat.sch.orfa")
        self.assertIsNone(linha.updated_at)
        self.assertEqual(linha.size_bytes, c.METRICA_NAO_LIDA)
        self.assertEqual(linha.file_count, c.METRICA_NAO_LIDA)
        self.assertEqual(linha.row_count, c.METRICA_NAO_LIDA)
        # observed_at preenchido: a tentativa aconteceu neste instante.
        self.assertEqual(linha.observed_at, AGORA)
        # E nao gera veredito sobre dado que ninguem leu.
        self.assertEqual(eventos, [])

    def test_tentativa_nao_e_avaliada_mesmo_com_expectativa_declarada(self):
        """Sem updated_at nao existe atraso a medir: avaliar daria stale eterno."""
        spark = _spark_de_schema(
            ["orfa"],
            erro_por_tabela={"orfa": self._erro_de_path("abfss://c@s/orfa")},
        )

        with patch.object(
            oa, "read_assets",
            return_value=[asset(full_name="cat.sch.orfa")],
        ), self.assertLogs(oa.logger, level="WARNING"):
            obs, eventos, _, cobertura = oa.process(
                spark, "cat", "sch", AGORA,
                oa.Destinations.for_environment("dev"),
            )

        self.assertEqual(cobertura.com_expectativa, 1)
        self.assertEqual(eventos, [])
        self.assertIsNone(obs[0].updated_at)

    def test_falha_que_nao_e_orfa_tambem_rende_linha_de_tentativa(self):
        spark = _spark_de_schema(
            ["sem_permissao"],
            erro_por_tabela={"sem_permissao": Exception("PERMISSION_DENIED")},
        )

        with self.assertLogs(oa.logger, level="ERROR"):
            obs, _, _, cobertura = oa.process(
                spark, "cat", "sch", AGORA,
                oa.Destinations.for_environment("dev"),
            )

        self.assertEqual([o.asset for o in obs], ["cat.sch.sem_permissao"])
        self.assertIsNone(obs[0].updated_at)
        self.assertEqual(cobertura.observados, 0)
        self.assertEqual(cobertura.outras_falhas, ("cat.sch.sem_permissao",))

    def test_referencia_de_volume_ignora_linha_de_tentativa(self):
        """A consulta de observacao anterior filtra as tentativas.

        Sem o filtro, o `previous` do dia de recuperacao viria com metrica
        zero e _percent_change() nao calcula variacao sobre zero: a
        avaliacao de variacao de volume ficaria desligada exatamente no dia
        em que a tabela volta a funcionar.
        """
        spark = _spark_de_schema(["t"])
        oa.read_previous_observations(
            spark, ["cat.sch.t"], oa.Destinations.for_environment("dev"),
            timezone.utc,
        )

        consultas = [ch.args[0] for ch in spark.sql.call_args_list]
        da_anterior = [q for q in consultas if "ROW_NUMBER()" in q]
        self.assertEqual(len(da_anterior), 1)
        self.assertIn("updated_at IS NOT NULL", da_anterior[0])

    def test_outra_falha_e_classificada_em_separado(self):
        spark = _spark_de_schema(
            ["boa", "sem_permissao"],
            erro_por_tabela={"sem_permissao": Exception("PERMISSION_DENIED")},
        )

        with self.assertLogs(oa.logger, level="ERROR"):
            _, _, _, cobertura = oa.process(
                spark, "cat", "sch", AGORA,
                oa.Destinations.for_environment("dev"),
            )

        self.assertEqual(cobertura.orfaos, ())
        self.assertEqual(cobertura.outras_falhas, ("cat.sch.sem_permissao",))
        # A boa foi lida; a sem permissao rendeu linha de tentativa.
        self.assertEqual(cobertura.observados, 1)


class TestResumoDaCobertura(unittest.TestCase):
    """main() soma a cobertura por schema e fecha o log com o resultado."""

    def test_soma_contagens_e_concatena_nomes(self):
        a = oa.Cobertura(coletados=10, com_expectativa=2, observados=8,
                         orfaos=("x",), outras_falhas=())
        b = oa.Cobertura(coletados=5, com_expectativa=0, observados=4,
                         orfaos=("y",), outras_falhas=("z",))
        total = a + b
        self.assertEqual(total.coletados, 15)
        self.assertEqual(total.com_expectativa, 2)
        self.assertEqual(total.observados, 12)
        self.assertEqual(total.orfaos, ("x", "y"))
        self.assertEqual(total.sem_observacao, 3)

    def test_schema_vazio_nao_soma_nada(self):
        self.assertEqual(oa.Cobertura() + oa.Cobertura(), oa.Cobertura())

    def test_avisa_quando_nenhum_ativo_tem_expectativa(self):
        cobertura = oa.Cobertura(
            coletados=2500, com_expectativa=0, observados=2482,
            orfaos=tuple("t%d" % i for i in range(18)),
        )
        with self.assertLogs(oa.logger, level="WARNING") as registro:
            oa._resumir_execucao(cobertura)
        self.assertIn(c.TAG_REFRESH_FREQUENCY, "\n".join(registro.output))

    def test_nao_avisa_quando_ha_expectativa(self):
        cobertura = oa.Cobertura(coletados=10, com_expectativa=3, observados=10)
        with self.assertLogs(oa.logger, level="INFO") as registro:
            oa._resumir_execucao(cobertura)
        self.assertNotIn("WARNING", "\n".join(registro.output))

    def test_schema_sem_perda_nao_gera_aviso(self):
        spark = _spark_de_schema(["t"])
        with self.assertLogs(oa.logger, level="INFO") as registro:
            oa.process(spark, "cat", "sch", AGORA,
                       oa.Destinations.for_environment("dev"))
        self.assertNotIn("sem observacao", "\n".join(registro.output))


class TestLeituraDoYamlDeAtivos(unittest.TestCase):
    """O arquivo que a documentacao dizia existir e o codigo nunca lia."""

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.caminho = Path(self.pasta.name) / "monitored-assets.yaml"

    def _escrever(self, texto):
        self.caminho.write_text(textwrap.dedent(texto), encoding="utf-8")
        return self.caminho

    def test_le_por_nome_completo(self):
        caminho = self._escrever("""
            assets:
              - name: cat.sch.t
                vc_criticality: HIGH
        """)
        overrides = oa.read_asset_overrides(caminho)
        self.assertEqual(list(overrides), ["cat.sch.t"])
        self.assertEqual(overrides["cat.sch.t"]["vc_criticality"], "HIGH")

    def test_arquivo_ausente_nao_e_erro(self):
        """Arquivo movido de lugar nao pode virar perda de configuracao."""
        self.assertEqual(
            oa.read_asset_overrides(Path(self.pasta.name) / "nao-existe.yaml"), {}
        )

    def test_arquivo_vazio_nao_e_erro(self):
        self.assertEqual(oa.read_asset_overrides(self._escrever("")), {})

    def test_entrada_sem_nome_e_ignorada_sem_derrubar_as_outras(self):
        caminho = self._escrever("""
            assets:
              - vc_criticality: HIGH
              - name: cat.sch.boa
                vc_criticality: LOW
        """)
        self.assertEqual(list(oa.read_asset_overrides(caminho)), ["cat.sch.boa"])


class TestPrecedenciaDoYaml(unittest.TestCase):
    """YAML por cima da tag, campo a campo."""

    def test_yaml_sobrescreve_a_tag(self):
        spark = _spark_com(
            {},
            {"t": {c.TAG_REFRESH_FREQUENCY: "1d", c.TAG_CRITICALITY: "LOW"}},
        )
        assets = oa.read_assets(
            spark, "cat", "sch",
            {"cat.sch.t": {c.TAG_CRITICALITY: "HIGH"}},
        )
        self.assertEqual(assets[0].criticality, "HIGH")

    def test_campo_ausente_no_yaml_preserva_o_da_tag(self):
        """Sobrescrita e por campo, nunca substituicao do ativo inteiro.

        Corrigir so a criticidade nao pode apagar o dominio e a frequencia
        que vieram da tag.
        """
        spark = _spark_com(
            {c.TAG_DATA_DOMAIN: "log"},
            {"t": {c.TAG_REFRESH_FREQUENCY: "1d"}},
        )
        assets = oa.read_assets(
            spark, "cat", "sch",
            {"cat.sch.t": {c.TAG_CRITICALITY: "HIGH"}},
        )
        self.assertEqual(assets[0].domain, "log")
        self.assertEqual(assets[0].freshness_sla_minutes, 1440)

    def test_ativo_so_no_yaml_entra_mesmo_sem_tag(self):
        """O caso de onboarding: catalogo ainda nao taggeado."""
        spark = _spark_com({}, {})
        assets = oa.read_assets(
            spark, "cat", "sch",
            {"cat.sch.nova": {c.TAG_REFRESH_FREQUENCY: "1d"}},
        )
        self.assertEqual([a.full_name for a in assets], ["cat.sch.nova"])

    def test_yaml_de_outro_schema_nao_vaza_para_esta_varredura(self):
        spark = _spark_com({}, {})
        assets = oa.read_assets(
            spark, "cat", "sch",
            {"outro.escopo.t": {c.TAG_REFRESH_FREQUENCY: "1d"}},
        )
        self.assertEqual(assets, [])


class TestMetricasDeCommit(unittest.TestCase):
    """Fase 1: coleta para calibrar regra depois, sem alertar nada."""

    def _spark_historico(self, commits):
        spark = MagicMock()

        def sql(consulta):
            resultado = MagicMock()
            if "DESCRIBE HISTORY" in consulta:
                resultado.collect.return_value = commits
            else:
                resultado.collect.return_value = []
            return resultado

        spark.sql.side_effect = sql
        return spark

    def _commit(self, versao, operacao="WRITE", metricas=None):
        return {
            "version": versao,
            "timestamp": AGORA,
            "operation": operacao,
            "operationMetrics": metricas or {},
        }

    def test_le_so_o_que_e_novo_e_em_ordem(self):
        spark = self._spark_historico(
            [self._commit(3), self._commit(1), self._commit(2)]
        )
        commits = oa.read_commits(spark, "cat.sch.t", watermark=1)
        self.assertEqual([m.delta_version for m in commits], [2, 3])

    def test_metrica_ausente_vira_none_e_nunca_zero(self):
        """"Nao sei" e "foram zero linhas" sao respostas diferentes.

        Um OPTIMIZE nao publica metrica de linha nenhuma; um zero inventado
        aqui viraria "esta carga trouxe zero linhas" na analise.
        """
        spark = self._spark_historico([self._commit(1, "OPTIMIZE")])
        metricas = oa.read_commits(spark, "cat.sch.t", -1)[0].metrics
        self.assertIsNone(metricas["num_output_rows"])
        self.assertIsNone(metricas["num_target_rows_inserted"])

    def test_merge_traz_os_campos_que_desambiguam_output_rows(self):
        """numTargetRowsCopied e o que torna numOutputRows interpretavel.

        Num MERGE, numOutputRows conta tambem as linhas inalteradas que
        foram reescritas junto: 100 inseridas podem virar 1.200.000 de
        saida.
        """
        spark = self._spark_historico([
            self._commit(1, "MERGE", {
                "numOutputRows": "1200000",
                "numTargetRowsInserted": "100",
                "numTargetRowsUpdated": "0",
                "numTargetRowsCopied": "1199900",
            })
        ])
        m = oa.read_commits(spark, "cat.sch.t", -1)[0].metrics
        self.assertEqual(m["num_output_rows"], 1200000)
        self.assertEqual(m["num_target_rows_inserted"], 100)
        self.assertEqual(m["num_target_rows_updated"], 0)
        self.assertEqual(m["num_target_rows_copied"], 1199900)

    def test_zero_declarado_e_preservado(self):
        spark = self._spark_historico(
            [self._commit(1, "WRITE", {"numOutputRows": "0"})]
        )
        self.assertEqual(
            oa.read_commits(spark, "cat.sch.t", -1)[0].metrics["num_output_rows"], 0
        )

    def test_acima_do_teto_grava_uma_linha_agregada(self):
        """Tabela de streaming taggeada por engano nao explode o log."""
        commits = [
            self._commit(v, "WRITE", {"numOutputRows": "10"})
            for v in range(1, c.COMMIT_BATCH_CAP + 20)
        ]
        resultado = oa.read_commits(self._spark_historico(commits), "cat.sch.t", -1)
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0].operation, c.OPERATION_AGGREGATED)
        self.assertEqual(resultado[0].commits_collapsed, len(commits))
        self.assertEqual(resultado[0].metrics["num_output_rows"], 10 * len(commits))

    def test_agregada_avanca_o_watermark_ate_a_versao_mais_recente(self):
        """Guardar so as primeiras N faria o job nunca alcancar a tabela."""
        commits = [self._commit(v) for v in range(1, c.COMMIT_BATCH_CAP + 20)]
        resultado = oa.read_commits(self._spark_historico(commits), "cat.sch.t", -1)
        self.assertEqual(resultado[0].delta_version, commits[-1]["version"])

    def test_buraco_no_historico_e_marcado(self):
        """Serie com furo silencioso produz regra calibrada errado."""
        spark = self._spark_historico([self._commit(50), self._commit(51)])
        commits = oa.read_commits(spark, "cat.sch.t", watermark=10)
        self.assertTrue(commits[0].has_gap)
        self.assertFalse(commits[1].has_gap)

    def test_sem_buraco_quando_a_sequencia_e_continua(self):
        spark = self._spark_historico([self._commit(11), self._commit(12)])
        commits = oa.read_commits(spark, "cat.sch.t", watermark=10)
        self.assertFalse(any(m.has_gap for m in commits))

    def test_primeira_execucao_nao_reporta_buraco(self):
        spark = self._spark_historico([self._commit(50)])
        self.assertFalse(oa.read_commits(spark, "cat.sch.t", -1)[0].has_gap)

    def test_falha_ao_ler_historico_nao_derruba_o_ativo(self):
        spark = MagicMock()
        spark.sql.side_effect = RuntimeError("sem permissao")
        self.assertEqual(oa.read_commits(spark, "cat.sch.t", -1), [])


class TestRetencaoDoLogDeCommits(unittest.TestCase):
    def _sql_da_limpeza(self):
        spark = MagicMock()
        spark.sql.return_value.collect.return_value = [{"num_deleted_rows": 3}]
        oa.purge_commit_metrics(spark, oa.Destinations.for_environment("dev"))
        return spark.sql.call_args[0][0]

    def test_usa_o_prazo_da_constante(self):
        self.assertIn(
            f"add_months(current_timestamp(), -{c.COMMIT_METRICS_RETENTION_MONTHS})",
            self._sql_da_limpeza(),
        )

    def test_preserva_o_commit_mais_recente_de_cada_ativo(self):
        """E dele que sai o watermark; apagado, o ativo voltaria a -1."""
        self.assertIn("m.delta_version < u.ultima", self._sql_da_limpeza())

    def test_so_toca_o_log_de_commits_do_ambiente(self):
        sql = self._sql_da_limpeza()
        self.assertIn(
            f"MERGE INTO {oa.Destinations.for_environment('dev').commit_metrics}", sql
        )


class TestNaoObservaAsPropriasTabelas(unittest.TestCase):
    def test_tabelas_de_destino_ficam_fora_da_varredura(self):
        destinos = oa.Destinations.for_environment("dev")
        catalogo, schema, propria = destinos.observations.split(".")
        spark = MagicMock()

        def sql(consulta):
            r = MagicMock()
            if "information_schema.tables" in consulta:
                r.collect.return_value = [
                    _LinhaFalsa(table_catalog=catalogo, table_schema=schema, table_name=t)
                    for t in (propria, "tabela_de_negocio")
                ]
            else:
                r.collect.return_value = []
            return r

        spark.sql.side_effect = sql
        assets = oa.read_assets(spark, catalogo, schema, excluded=destinos.all)
        self.assertEqual(
            [a.full_name for a in assets], [f"{catalogo}.{schema}.tabela_de_negocio"]
        )


class TestIdDoEvento(unittest.TestCase):
    def test_duas_execucoes_no_mesmo_dia_tem_ids_diferentes(self):
        """O PASS da segunda execucao nao pode ser descartado como duplicado."""
        manha = oa.event_id(c.SOURCE_FRESHNESS, "cat.sch.t", AGORA)
        tarde = oa.event_id(c.SOURCE_FRESHNESS, "cat.sch.t", AGORA + timedelta(hours=6))
        self.assertNotEqual(manha, tarde)

    def test_e_deterministico_para_reenvio(self):
        self.assertEqual(
            oa.event_id(c.SOURCE_VOLUME, "cat.sch.t", AGORA),
            oa.event_id(c.SOURCE_VOLUME, "cat.sch.t", AGORA),
        )

    def test_fontes_diferentes_tem_ids_diferentes(self):
        self.assertNotEqual(
            oa.event_id(c.SOURCE_FRESHNESS, "cat.sch.t", AGORA),
            oa.event_id(c.SOURCE_VOLUME, "cat.sch.t", AGORA),
        )

    def test_so_usa_caracteres_aceitos_pelo_publicador(self):
        self.assertRegex(
            oa.event_id(c.SOURCE_FRESHNESS, "cat.sch.t", AGORA), r"^[A-Za-z0-9_-]+$"
        )


class TestLinhasNaOrdemDoSchema(unittest.TestCase):
    def test_ordem_vem_do_schema_e_nao_do_registro(self):
        registro = {campo.name: campo.name for campo in reversed(oa.OBSERVATIONS_SCHEMA.fields)}
        linha = oa._rows_in_schema_order(oa.OBSERVATIONS_SCHEMA, [registro])[0]
        self.assertEqual(linha, tuple(campo.name for campo in oa.OBSERVATIONS_SCHEMA.fields))

    def test_campo_ausente_falha_em_vez_de_virar_nulo(self):
        with self.assertRaises(KeyError):
            oa._rows_in_schema_order(oa.OBSERVATIONS_SCHEMA, [{"asset": "x"}])


if __name__ == "__main__":
    unittest.main()
