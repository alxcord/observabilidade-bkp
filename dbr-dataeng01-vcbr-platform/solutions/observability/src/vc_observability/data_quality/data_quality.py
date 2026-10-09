"""Executa regras de Data Quality e grava os resultados.

As regras sao declaradas em YAML, versionado em git — uma regra e
configuracao, e mudanca de regra precisa passar por revisao. Os resultados
vao para tabela, porque sao dado.

Fluxo:

    YAML de regras  -->  executa contra o ativo  -->  tbl_dbr_it_data_quality_results
                                                 -->  tbl_dbr_it_observability_events

Cada regra vira uma consulta que devolve duas contagens: quantas linhas
foram avaliadas e quantas estao invalidas. O veredito sai da comparacao
com o limite declarado.

Uso:

    python data_quality.py --regras ../../../config/quality-rules/logistics.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml
from pyspark.sql import Row, SparkSession
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

import constants as c

logger = logging.getLogger("data-quality")


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """Uma regra de qualidade, como declarada no YAML."""

    id: str
    version: str
    rule_type: str
    asset: str
    description: str = ""
    columns: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    severity: str = c.DEFAULT_SEVERITY
    domain: str = c.DEFAULT_DOMAIN
    blocking: bool = False
    threshold_pct: float = c.DEFAULT_THRESHOLD_PCT


@dataclass(frozen=True)
class Result:
    """O que aconteceu ao executar uma regra."""

    rule: Rule
    evaluation_status: str
    quality_outcome: str
    evaluated_row_count: int
    invalid_row_count: int
    executed_at: datetime
    error: str | None = None

    @property
    def invalid_pct(self) -> float:
        if not self.evaluated_row_count:
            return 0.0
        return self.invalid_row_count / self.evaluated_row_count * 100

    @property
    def result_id(self) -> str:
        """Chave deterministica: reenviar o mesmo resultado nao duplica.

        A versao da regra entra na chave porque um resultado produzido por
        outra versao e outro resultado — sem isso, alterar a regra tornaria
        o historico ininterpretavel.
        """
        identity = "||".join(
            [
                self.rule.asset,
                self.rule.id,
                self.rule.version,
                self.executed_at.isoformat(),
            ]
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Carga das regras
# ---------------------------------------------------------------------------


def load_rules(path: str) -> list[Rule]:
    """Le o YAML de regras.

    Uma regra com tipo desconhecido interrompe a carga em vez de ser
    ignorada: regra que nao roda em silencio da falsa sensacao de cobertura.
    """
    with open(path, encoding="utf-8") as file:
        content = yaml.safe_load(file) or {}

    rules = []
    for item in content.get("rules", []):
        rule_type = item["type"]
        if rule_type not in c.SUPPORTED_TYPES:
            raise ValueError(
                f"regra '{item['id']}': tipo '{rule_type}' nao suportado. "
                f"Suportados: {', '.join(sorted(c.SUPPORTED_TYPES))}"
            )
        rules.append(
            Rule(
                id=item["id"],
                version=str(item.get("version", "1")),
                rule_type=rule_type,
                asset=item["asset"],
                description=item.get("description", ""),
                columns=item.get("columns", []),
                parameters=item.get("parameters", {}),
                severity=item.get("severity", c.DEFAULT_SEVERITY),
                domain=item.get("domain", c.DEFAULT_DOMAIN),
                blocking=item.get("blocking", False),
                threshold_pct=float(item.get("threshold_pct", c.DEFAULT_THRESHOLD_PCT)),
            )
        )

    logger.info("%d regras carregadas de %s", len(rules), path)
    return rules


# ---------------------------------------------------------------------------
# Traducao da regra em SQL
#
# Cada funcao devolve a condicao que identifica uma linha INVALIDA. O
# esqueleto da consulta e o mesmo para todos os tipos, o que mantem a
# contagem consistente e evita um SQL diferente por regra.
# ---------------------------------------------------------------------------


def _not_null_condition(rule: Rule) -> str:
    return " OR ".join(f"{column} IS NULL" for column in rule.columns)


def _domain_condition(rule: Rule) -> str:
    values = ", ".join(f"'{v}'" for v in rule.parameters["values"])
    column = rule.columns[0]
    return f"{column} IS NOT NULL AND {column} NOT IN ({values})"


def _range_condition(rule: Rule) -> str:
    column = rule.columns[0]
    min_value = rule.parameters.get("min")
    max_value = rule.parameters.get("max")

    bounds = []
    if min_value is not None:
        bounds.append(f"{column} < {min_value}")
    if max_value is not None:
        bounds.append(f"{column} > {max_value}")
    if not bounds:
        raise ValueError(f"regra '{rule.id}': informe min e/ou max")

    return " OR ".join(bounds)


def _expression_condition(rule: Rule) -> str:
    """Escape hatch: a expressao declara o que e invalido."""
    return rule.parameters["invalid_condition"]


CONDITION_BY_TYPE = {
    c.TYPE_NOT_NULL: _not_null_condition,
    c.TYPE_DOMAIN: _domain_condition,
    c.TYPE_RANGE: _range_condition,
    c.TYPE_EXPRESSION: _expression_condition,
}


def build_query(rule: Rule) -> str:
    """Devolve o SQL que conta linhas avaliadas e invalidas.

    Unicidade e integridade referencial nao cabem no esqueleto de condicao
    por linha — a primeira precisa agrupar, a segunda precisa juntar com
    outra tabela — entao cada uma tem sua consulta.
    """
    if rule.rule_type == c.TYPE_UNIQUE:
        columns = ", ".join(rule.columns)
        return f"""
            SELECT
                (SELECT COUNT(*) FROM {rule.asset}) AS avaliadas,
                COALESCE(SUM(repetidas - 1), 0)      AS invalidas
            FROM (
                SELECT COUNT(*) AS repetidas
                  FROM {rule.asset}
                 GROUP BY {columns}
                HAVING COUNT(*) > 1
            )
        """

    if rule.rule_type == c.TYPE_REFERENCE:
        column = rule.columns[0]
        reference_table = rule.parameters["reference_table"]
        reference_column = rule.parameters["reference_column"]
        return f"""
            SELECT
                COUNT(*)                                        AS avaliadas,
                SUM(CASE WHEN r.{reference_column} IS NULL AND o.{column} IS NOT NULL
                         THEN 1 ELSE 0 END)                     AS invalidas
              FROM {rule.asset} o
              LEFT JOIN {reference_table} r
                ON o.{column} = r.{reference_column}
        """

    condition = CONDITION_BY_TYPE[rule.rule_type](rule)
    return f"""
        SELECT
            COUNT(*)                                      AS avaliadas,
            SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)   AS invalidas
          FROM {rule.asset}
    """


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------


def execute(
    spark: SparkSession, rule: Rule, now_utc: datetime
) -> Result:
    """Roda uma regra e devolve o resultado.

    Falha tecnica vira status ERROR com qualidade UNKNOWN — nunca FAIL. Uma
    tabela inacessivel nao e um dado reprovado, e contar assim inverteria o
    diagnostico de quem for investigar.
    """
    try:
        row = spark.sql(build_query(rule)).collect()[0]
        evaluated = row["avaliadas"] or 0
        invalid = row["invalidas"] or 0
    except Exception as error:
        logger.exception("regra %s falhou ao executar", rule.id)
        return Result(
            rule=rule,
            evaluation_status=c.STATUS_ERROR,
            quality_outcome=c.OUTCOME_UNKNOWN,
            evaluated_row_count=0,
            invalid_row_count=0,
            executed_at=now_utc,
            error=str(error)[:500],
        )

    partial = Result(
        rule=rule,
        evaluation_status=c.STATUS_SUCCESS,
        quality_outcome=c.OUTCOME_PASS,
        evaluated_row_count=evaluated,
        invalid_row_count=invalid,
        executed_at=now_utc,
    )

    return Result(
        rule=rule,
        evaluation_status=c.STATUS_SUCCESS,
        quality_outcome=_classify(partial),
        evaluated_row_count=evaluated,
        invalid_row_count=invalid,
        executed_at=now_utc,
    )


def _classify(result: Result) -> str:
    """PASS, WARN ou FAIL, comparando o percentual invalido com o limite.

    Regra nao bloqueante que estoura o limite vira WARN, nao FAIL: ela
    merece atencao, mas nao e falha formal e nao deve abrir incidente.
    """
    if result.invalid_row_count == 0:
        return c.OUTCOME_PASS
    if result.invalid_pct <= result.rule.threshold_pct:
        return c.OUTCOME_PASS
    if result.rule.blocking:
        return c.OUTCOME_FAIL
    return c.OUTCOME_WARN


# ---------------------------------------------------------------------------
# Escrita
#
# Esquema explicito em cada gravacao, pelo mesmo motivo de asset_observability:
# sem ele, um lote com colunas nulas em todas as linhas (`error` quando nenhuma
# regra falhou, `sent_at` sempre) nao tem tipo inferivel e o write aborta com
# CANNOT_DETERMINE_TYPE. A ordem dos campos segue a ordem dos Row abaixo: o
# Spark associa Row a StructType por posicao, nao por nome.
# ---------------------------------------------------------------------------

RESULTS_SCHEMA = StructType(
    [
        StructField("result_id", StringType(), nullable=False),
        StructField("rule_id", StringType(), nullable=False),
        StructField("rule_version", StringType(), nullable=False),
        StructField("asset", StringType(), nullable=False),
        StructField("rule_type", StringType(), nullable=False),
        StructField("severity", StringType(), nullable=True),
        StructField("blocking", BooleanType(), nullable=True),
        StructField("evaluation_status", StringType(), nullable=False),
        StructField("quality_outcome", StringType(), nullable=False),
        StructField("evaluated_row_count", LongType(), nullable=True),
        StructField("invalid_row_count", LongType(), nullable=True),
        StructField("invalid_pct", DoubleType(), nullable=True),
        StructField("error", StringType(), nullable=True),
        StructField("executed_at", TimestampType(), nullable=False),
    ]
)

EVENTS_SCHEMA = StructType(
    [
        StructField("source", StringType(), nullable=False),
        StructField("result", StringType(), nullable=False),
        StructField("natural_key", StringType(), nullable=False),
        StructField("domain", StringType(), nullable=True),
        StructField("severity", StringType(), nullable=True),
        StructField("error_code", StringType(), nullable=True),
        StructField("description", StringType(), nullable=True),
        StructField("source_event_id", StringType(), nullable=False),
        StructField("observed_at", TimestampType(), nullable=False),
        StructField("sent_at", TimestampType(), nullable=True),
    ]
)


def ensure_results_table(spark: SparkSession) -> None:
    """Cria a tabela de resultados por DDL, se ainda nao existir.

    Criar por DDL, e nao pelo primeiro saveAsTable, fixa tipos, comentarios e
    o grao (result_id) antes da primeira carga. A tabela de eventos e criada
    pelo bootstrap de asset_observability, que e o dono da staging.
    """
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {c.RESULTS_TABLE} (
          result_id STRING NOT NULL
            COMMENT 'SHA-256 de ativo, regra, versao e instante da execucao.',
          rule_id STRING NOT NULL COMMENT 'Id estavel da regra no YAML.',
          rule_version STRING NOT NULL COMMENT 'Versao da regra; muda quando o criterio muda.',
          asset STRING NOT NULL COMMENT 'Nome completo do ativo avaliado (catalogo.schema.tabela).',
          rule_type STRING NOT NULL COMMENT 'Tipo da regra: not_null, unique, domain, range, reference ou expression.',
          severity STRING COMMENT 'Severidade declarada na regra (HIGH, MEDIUM ou LOW).',
          blocking BOOLEAN COMMENT 'TRUE quando a regra pode abrir incidente.',
          evaluation_status STRING NOT NULL COMMENT 'SUCCESS ou ERROR: a regra conseguiu rodar?',
          quality_outcome STRING NOT NULL COMMENT 'PASS, WARN, FAIL ou UNKNOWN: o dado esta bom?',
          evaluated_row_count BIGINT COMMENT 'Linhas avaliadas pela regra.',
          invalid_row_count BIGINT COMMENT 'Linhas invalidas encontradas.',
          invalid_pct DOUBLE COMMENT 'Percentual de linhas invalidas sobre as avaliadas.',
          error STRING COMMENT 'Mensagem tecnica quando evaluation_status = ERROR; NULL caso contrario.',
          executed_at TIMESTAMP NOT NULL COMMENT 'Instante UTC da execucao da regra.',
          CONSTRAINT pk_data_quality_results PRIMARY KEY (result_id)
        )
        USING DELTA
        COMMENT 'Historico completo dos resultados de Data Quality. Uma linha por regra por execucao.'
        """
    )


def write_results(
    spark: SparkSession, results: list[Result]
) -> None:
    """Grava o historico completo, aprovados e reprovados."""
    if not results:
        return

    rows = [
        Row(
            result_id=r.result_id,
            rule_id=r.rule.id,
            rule_version=r.rule.version,
            asset=r.rule.asset,
            rule_type=r.rule.rule_type,
            severity=r.rule.severity,
            blocking=r.rule.blocking,
            evaluation_status=r.evaluation_status,
            quality_outcome=r.quality_outcome,
            evaluated_row_count=r.evaluated_row_count,
            invalid_row_count=r.invalid_row_count,
            invalid_pct=r.invalid_pct,
            error=r.error,
            executed_at=r.executed_at,
        )
        for r in results
    ]
    spark.createDataFrame(rows, schema=RESULTS_SCHEMA).write.mode("append").saveAsTable(
        c.RESULTS_TABLE
    )
    logger.info("%d resultados gravados", len(rows))


def write_events(
    spark: SparkSession, results: list[Result]
) -> None:
    """Enfileira para o hub apenas o que pode virar incidente.

    So regras bloqueantes entram: WARN e informativo e nao deve alertar.
    PASS de regra bloqueante tambem entra, porque e o que fecha o incidente
    aberto por um FAIL anterior — enviar so as falhas faria o incidente
    abrir e nunca fechar.
    """
    candidates = [r for r in results if r.rule.blocking]
    if not candidates:
        return

    # Nomes de campo em inglês: viram, sem tradução, as colunas físicas da
    # tabela de staging (Delta), lida por hub_publisher.py e escrita
    # também por asset_observability.py — os três precisam concordar.
    rows = [
        Row(
            source=c.SOURCE_DATA_QUALITY,
            result=(
                c.OUTCOME_PASS
                if r.quality_outcome == c.OUTCOME_PASS
                else c.OUTCOME_FAIL
            ),
            natural_key=f"{r.rule.id}@{r.rule.asset}",
            domain=r.rule.domain,
            severity=r.rule.severity,
            error_code=_error_code(r),
            description=_description(r),
            source_event_id=r.result_id,
            observed_at=r.executed_at,
            sent_at=None,
        )
        for r in candidates
    ]
    spark.createDataFrame(rows, schema=EVENTS_SCHEMA).write.mode("append").saveAsTable(
        c.STAGING_EVENTS_TABLE
    )
    logger.info("%d eventos enfileirados", len(rows))


def _error_code(result: Result) -> str | None:
    if result.evaluation_status == c.STATUS_ERROR:
        return c.ERROR_CODE_RULE_NOT_EVALUATED
    if result.quality_outcome == c.OUTCOME_PASS:
        return None
    return c.ERROR_CODE_RULE_FAILED


def _description(result: Result) -> str | None:
    if result.evaluation_status == c.STATUS_ERROR:
        return f"A regra nao pode ser avaliada: {result.error}"
    if result.quality_outcome == c.OUTCOME_PASS:
        return None
    return (
        f"{result.invalid_row_count} de {result.evaluated_row_count} linhas "
        f"invalidas ({result.invalid_pct:.2f}%), acima do limite de "
        f"{result.rule.threshold_pct:.2f}%."
    )


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regras", required=True, action="append", dest="rules")
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()
    now_utc = datetime.now(timezone.utc)
    ensure_results_table(spark)

    for path in args.rules:
        results = [
            execute(spark, rule, now_utc) for rule in load_rules(path)
        ]
        write_results(spark, results)
        write_events(spark, results)


if __name__ == "__main__":
    main()
