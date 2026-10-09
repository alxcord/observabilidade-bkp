"""Coleta mudancas de estrutura (schema) dos ativos monitorados.

Roda como job Databricks, uma vez por dia, no mesmo grupo de cadencia de
freshness/volume/lineage (Cen5-Hub-Operacional-SQL_v2.0.md, §5.6). Para
cada tabela monitorada:

1. le o snapshot atual de colunas em `{catalog}.information_schema.columns`
   — metadado de catalogo, sincrono, sem a latencia que descartou o
   caminho de alerta baseado nas System Tables (ver README do Cenario 3);
2. compara com o snapshot da execucao anterior, gravado por este mesmo job;
3. classifica cada mudanca em compativel, controlada, incompativel ou
   critica (Cenario 3, doc de referencia, §15.3);
4. grava toda mudanca na tabela de log (`tbl_dbr_it_schema_changes`) e,
   quando a mudanca e incompativel ou critica E o ativo tem consumidor
   conhecido, enfileira um evento para o hub.

Este e o processo que faltava ligar: a copia periodica do metadado de
schema para uma tabela de log, com historico e classificacao, que o
Cenario 3 ja desenhava e a Observabilidade so descrevia em duas linhas (§5.4)
sem nenhum job por tras.

O job nunca fala com o banco do hub — mesmo padrao de asset_observability
e data_quality: so escreve em Delta, quem envia e o publicador separado.

Limitacao conhecida, deliberada por ora: um evento de schema e um FAIL
pontual, nao um estado continuo. Nao existe "voltou ao normal" para uma
coluna removida — a tabela nao reganha a coluna sozinha. Por isso este
job NUNCA envia PASS para o pilar SCHEMA, e o incidente aberto por uma
mudanca incompativel ou critica fica OPEN ate resolucao manual, hoje sem
rota na API (ver Escopo §21.13 — acknowledgment via ITSM e Fase 2). E o
mesmo modo de falha ja aceito e documentado em Cen5-Hub-Operacional-SQL
para a renomeacao de pipeline ADF (§4.2): incidente que so fecha por
intervencao, com o runbook como mitigacao — nao um efeito colateral
escondido.

Uso:

    python schema_observability.py --catalog prd_logistics --schema corp_curated
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pyspark.sql import Row, SparkSession

import constants as c

logger = logging.getLogger("schema-observability")


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitoredAsset:
    """Uma tabela monitorada para fins de schema — dominio, criticidade e
    a lista de consumidores conhecidos que decide se uma mudanca ruim
    alerta ou so fica registrada no log."""

    full_name: str
    domain: str
    criticality: str
    consumers: tuple[str, ...] = ()

    @property
    def severity(self) -> str:
        return c.SEVERITY_BY_CRITICALITY.get(
            self.criticality.upper(), c.DEFAULT_SEVERITY
        )

    @property
    def has_known_consumers(self) -> bool:
        return len(self.consumers) > 0


@dataclass(frozen=True)
class ColumnSnapshot:
    """Uma coluna, no estado observado numa execucao."""

    table: str
    column: str
    ordinal_position: int
    data_type: str
    is_nullable: bool
    observed_at: datetime


@dataclass(frozen=True)
class SchemaChange:
    """Uma mudanca detectada entre duas execucoes, ja classificada."""

    table: str
    domain: str
    severity: str
    column: str | None
    change_type: str
    classification: str
    previous_value: str | None
    current_value: str | None
    detected_at: datetime


@dataclass(frozen=True)
class Event:
    """Resultado no formato que o hub espera — mesmo contrato de
    asset_observability.Event. So FAIL: ver limitacao no docstring do
    modulo."""

    source: str
    result: str
    asset: str
    domain: str
    severity: str
    error_code: str | None
    description: str | None


# ---------------------------------------------------------------------------
# Leitura dos ativos monitorados
# ---------------------------------------------------------------------------


def read_tags_by_table(
    spark: SparkSession, catalog: str, schema: str
) -> dict[str, dict[str, str]]:
    """Tags efetivas de cada tabela do schema, com heranca schema -> tabela.

    Mesma consulta e mesma logica de heranca de `asset_observability.py` —
    duplicada de proposito, nao importada: cada job Databricks desta pasta
    e autocontido (docs/codigo/jobs-databricks.md), e as duas copias sao pequenas
    o bastante para nao valer o acoplamento entre projetos.
    """
    tags_do_schema = {
        row.tag_name: row.tag_value
        for row in spark.sql(
            f"""
            SELECT tag_name, tag_value
              FROM {catalog}.information_schema.schema_tags
             WHERE schema_name = '{schema}'
            """
        ).collect()
    }

    tags_by_table: dict[str, dict[str, str]] = {}
    for row in spark.sql(
        f"""
        SELECT catalog_name, schema_name, table_name, tag_name, tag_value
          FROM {catalog}.information_schema.table_tags
         WHERE schema_name = '{schema}'
        """
    ).collect():
        name = f"{row.catalog_name}.{row.schema_name}.{row.table_name}"
        if name not in tags_by_table:
            tags_by_table[name] = dict(tags_do_schema)
        tags_by_table[name][row.tag_name] = row.tag_value

    return tags_by_table


def read_asset_overrides(path: str | Path) -> dict[str, dict]:
    """Le o mesmo YAML de ativos monitorados do asset_observability.

    Arquivo ausente ou vazio nao e erro e nao apaga nada — mesma protecao
    aplicada la.
    """
    caminho = Path(path)
    if not caminho.exists():
        logger.info("%s nao existe; nenhuma sobrescrita de ativo", caminho)
        return {}

    with caminho.open(encoding="utf-8") as arquivo:
        conteudo = yaml.safe_load(arquivo) or {}

    overrides = {}
    for item in conteudo.get("assets", []):
        nome = item.get("name")
        if not nome:
            logger.warning("%s: entrada sem `name`, ignorada", caminho)
            continue
        overrides[nome] = item

    logger.info("%s: %d ativos com sobrescrita", caminho, len(overrides))
    return overrides


def read_monitored_assets(
    spark: SparkSession,
    catalog: str,
    schema: str,
    overrides: dict[str, dict] | None = None,
) -> list[MonitoredAsset]:
    """Tabelas do schema em escopo de monitoracao de estrutura.

    Ao contrario de `asset_observability.read_assets`, NAO exige
    `vc_refresh_frequency`: uma tabela sem promessa de atualizacao ainda
    precisa ter a estrutura vigiada. O escopo aqui e "tem `vc_data_domain`
    ou `vc_criticality` na tag, ou esta declarada no YAML" — o mesmo
    universo de ativos que a governanca ja tratou como identificados, so
    sem a exigencia adicional de frequencia.
    """
    tags_by_table = read_tags_by_table(spark, catalog, schema)
    overrides = overrides or {}

    prefixo = f"{catalog}.{schema}."
    nomes = set(tags_by_table) | {
        nome for nome in overrides if nome.startswith(prefixo)
    }

    assets = [
        MonitoredAsset(
            full_name=nome,
            domain=_field(nome, tags_by_table, overrides, c.TAG_DATA_DOMAIN, "domain")
            or c.DEFAULT_DOMAIN,
            criticality=_field(
                nome, tags_by_table, overrides, c.TAG_CRITICALITY, "criticality"
            )
            or "",
            consumers=tuple(overrides.get(nome, {}).get("consumers", ())),
        )
        for nome in sorted(nomes)
    ]
    logger.info("%s.%s: %d ativos monitorados por schema", catalog, schema, len(assets))
    return assets


def _field(
    nome: str,
    tags_by_table: dict[str, dict[str, str]],
    overrides: dict[str, dict],
    tag_key: str,
    override_key: str,
) -> str | None:
    """Valor de um campo — YAML tem precedencia sobre a tag, campo a
    campo, mesma regra de `asset_observability._merge_declaration`."""
    override_value = overrides.get(nome, {}).get(override_key)
    if override_value is not None:
        return str(override_value)
    return tags_by_table.get(nome, {}).get(tag_key)


# ---------------------------------------------------------------------------
# Leitura do estado atual e do estado anterior
# ---------------------------------------------------------------------------


def read_existing_tables(spark: SparkSession, catalog: str, schema: str) -> set[str]:
    """Nomes completos das tabelas que existem HOJE no schema.

    Usado so para detectar `TABLE_DROPPED`: um ativo que tinha snapshot
    gravado e nao aparece mais aqui foi removido, renomeado ou perdeu
    permissao — os tres sao indistinguiveis por este metadado, e os tres
    justificam o mesmo alerta: alguem que consumia esse nome vai quebrar.
    """
    return {
        f"{row.table_catalog}.{row.table_schema}.{row.table_name}"
        for row in spark.sql(
            f"""
            SELECT table_catalog, table_schema, table_name
              FROM {catalog}.information_schema.tables
             WHERE table_schema = '{schema}'
            """
        ).collect()
    }


def read_columns(
    spark: SparkSession, catalog: str, table: str, now_utc: datetime
) -> dict[str, ColumnSnapshot]:
    """Colunas de uma tabela, no instante desta execucao.

    `catalog` vem de fora, do argumento `--catalog` do job, em vez de
    parseado de `table`: `full_name` sempre tem exatamente tres partes
    neste cenario, mas fazer o metodo depender disso a cada chamada e
    fragilidade desnecessaria quando o catalogo ja esta disponivel no
    escopo de quem chama.
    """
    _, table_schema, table_name = table.split(".")
    return {
        row.column_name: ColumnSnapshot(
            table=table,
            column=row.column_name,
            ordinal_position=row.ordinal_position,
            data_type=row.full_data_type,
            is_nullable=(row.is_nullable == "YES"),
            observed_at=now_utc,
        )
        for row in spark.sql(
            f"""
            SELECT column_name, ordinal_position, full_data_type, is_nullable
              FROM {catalog}.information_schema.columns
             WHERE table_schema = '{table_schema}'
               AND table_name = '{table_name}'
            """
        ).collect()
    }


def read_previous_snapshot(
    spark: SparkSession, table: str
) -> dict[str, ColumnSnapshot] | None:
    """Ultimo snapshot gravado para esta tabela, coluna a coluna.

    `None` quando nao ha snapshot anterior — primeira execucao do ativo, e
    por isso sem base de comparacao. Dicionario vazio e outra coisa: a
    ultima execucao viu a tabela sem nenhuma coluna monitorada, o que nao
    deveria acontecer, mas nao e o mesmo caso de "nunca observado".
    """
    linhas = spark.sql(
        f"""
        WITH ultima_execucao AS (
            SELECT MAX(observed_at) AS observed_at
              FROM {c.SNAPSHOT_TABLE}
             WHERE table = '{table}'
        )
        SELECT s.column, s.ordinal_position, s.data_type, s.is_nullable,
               s.observed_at
          FROM {c.SNAPSHOT_TABLE} s
          JOIN ultima_execucao u ON s.observed_at = u.observed_at
         WHERE s.table = '{table}'
        """
    ).collect()
    if not linhas:
        return None

    return {
        row["column"]: ColumnSnapshot(
            table=table,
            column=row["column"],
            ordinal_position=row["ordinal_position"],
            data_type=row["data_type"],
            is_nullable=row["is_nullable"],
            observed_at=row["observed_at"],
        )
        for row in linhas
    }


# ---------------------------------------------------------------------------
# Classificacao (Cenario 3, doc de referencia, §15.3)
# ---------------------------------------------------------------------------


def _base_type(data_type: str) -> str:
    """`decimal(10,2)` -> `decimal`; `int` -> `int`.

    Compara so o tipo base contra `SAFE_WIDENING`. Mudanca de
    precisao/escala dentro do mesmo tipo NAO cai aqui — fica para
    `classify_type_change` decidir, porque nem toda mudanca de decimal e
    alargamento.
    """
    return data_type.split("(")[0].strip().lower()


def classify_type_change(previous: str, current: str) -> str:
    """So chamada quando `previous != current` — COMPATIVEL nunca sai
    daqui: todo tipo que muda e, no minimo, controlado, porque mesmo um
    alargamento seguro pode exigir recompilar um consumidor tipado
    estaticamente (Power BI, uma view). Isso distingue de `COLUMN_ADDED`,
    que de fato nao afeta ninguem ja existente.

    Mesmo tipo base com precisao/escala diferente (ex.: decimal(10,2) ->
    decimal(5,2)) cai direto em INCOMPATIVEL: sem saber a direcao,
    truncar escala perde dado silenciosamente do lado do consumidor, e o
    lado seguro e alertar mesmo que a mudanca real seja so um alargamento
    de precisao.
    """
    anterior, atual = _base_type(previous), _base_type(current)
    if (anterior, atual) in c.SAFE_WIDENING:
        return c.CLASS_CONTROLADA
    return c.CLASS_INCOMPATIVEL


def compare_columns(
    table: str,
    previous: dict[str, ColumnSnapshot],
    current: dict[str, ColumnSnapshot],
    now_utc: datetime,
) -> list[SchemaChange]:
    """As mudancas de coluna entre duas execucoes — sem dominio/severidade
    ainda, que `process()` preenche a partir do `MonitoredAsset`."""
    changes: list[SchemaChange] = []

    for nome, coluna in current.items():
        if nome not in previous:
            changes.append(
                SchemaChange(
                    table=table, domain="", severity="", column=nome,
                    change_type=c.CHANGE_COLUMN_ADDED,
                    classification=c.CLASS_COMPATIVEL,
                    previous_value=None, current_value=coluna.data_type,
                    detected_at=now_utc,
                )
            )
            continue

        anterior = previous[nome]
        if anterior.data_type != coluna.data_type:
            changes.append(
                SchemaChange(
                    table=table, domain="", severity="", column=nome,
                    change_type=c.CHANGE_TYPE_CHANGED,
                    classification=classify_type_change(
                        anterior.data_type, coluna.data_type
                    ),
                    previous_value=anterior.data_type,
                    current_value=coluna.data_type,
                    detected_at=now_utc,
                )
            )

        if anterior.is_nullable and not coluna.is_nullable:
            changes.append(
                SchemaChange(
                    table=table, domain="", severity="", column=nome,
                    change_type=c.CHANGE_NULLABILITY_TIGHTENED,
                    classification=c.CLASS_CONTROLADA,
                    previous_value="NULL", current_value="NOT NULL",
                    detected_at=now_utc,
                )
            )
        elif not anterior.is_nullable and coluna.is_nullable:
            changes.append(
                SchemaChange(
                    table=table, domain="", severity="", column=nome,
                    change_type=c.CHANGE_NULLABILITY_RELAXED,
                    classification=c.CLASS_COMPATIVEL,
                    previous_value="NOT NULL", current_value="NULL",
                    detected_at=now_utc,
                )
            )

    for nome, coluna in previous.items():
        if nome not in current:
            changes.append(
                SchemaChange(
                    table=table, domain="", severity="", column=nome,
                    change_type=c.CHANGE_COLUMN_DROPPED,
                    classification=c.CLASS_INCOMPATIVEL,
                    previous_value=coluna.data_type, current_value=None,
                    detected_at=now_utc,
                )
            )

    return changes


def table_dropped_change(table: str, now_utc: datetime) -> SchemaChange:
    return SchemaChange(
        table=table, domain="", severity="", column=None,
        change_type=c.CHANGE_TABLE_DROPPED,
        classification=c.CLASS_CRITICA,
        previous_value=table, current_value=None,
        detected_at=now_utc,
    )


def _with_asset(change: SchemaChange, asset: MonitoredAsset) -> SchemaChange:
    return SchemaChange(
        table=change.table, domain=asset.domain, severity=asset.severity,
        column=change.column, change_type=change.change_type,
        classification=change.classification,
        previous_value=change.previous_value, current_value=change.current_value,
        detected_at=change.detected_at,
    )


def to_event(change: SchemaChange, asset: MonitoredAsset) -> Event | None:
    """Um evento FAIL, so quando a mudanca alerta (§ do modulo).

    Nunca devolve PASS — ver a limitacao conhecida no docstring do modulo.
    """
    if change.classification not in c.ALERTABLE_CLASSIFICATIONS:
        return None
    if not asset.has_known_consumers:
        return None

    error_code = (
        c.ERROR_CODE_CRITICAL_CHANGE
        if change.classification == c.CLASS_CRITICA
        else c.ERROR_CODE_INCOMPATIBLE_CHANGE
    )
    alvo = f"coluna {change.column}" if change.column else "a tabela inteira"
    descricao = (
        f"Mudanca {change.classification.lower()} em {alvo} "
        f"({change.change_type}): {change.previous_value!r} -> "
        f"{change.current_value!r}. Consumidores conhecidos: "
        f"{', '.join(asset.consumers)}."
    )
    return Event(
        source=c.SOURCE_SCHEMA, result="FAIL", asset=change.table,
        domain=asset.domain, severity=asset.severity,
        error_code=error_code, description=descricao,
    )


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def write_snapshot(
    spark: SparkSession, table: str, columns: dict[str, ColumnSnapshot]
) -> None:
    if not columns:
        return
    rows = [
        Row(
            table=col.table, column=col.column,
            ordinal_position=col.ordinal_position, data_type=col.data_type,
            is_nullable=col.is_nullable, observed_at=col.observed_at,
        )
        for col in columns.values()
    ]
    spark.createDataFrame(rows).write.mode("append").saveAsTable(c.SNAPSHOT_TABLE)


def write_changes(spark: SparkSession, changes: list[SchemaChange]) -> None:
    """Acrescenta a tabela de log — a copia system tables -> log que este
    modulo existe para prover. Toda mudanca vai para ca, alertavel ou nao:
    o log e o registro de auditoria completo, o alerta e so o subconjunto
    que acorda alguem."""
    if not changes:
        return
    rows = [
        Row(
            table=ch.table, domain=ch.domain, severity=ch.severity,
            column=ch.column, change_type=ch.change_type,
            classification=ch.classification,
            previous_value=ch.previous_value, current_value=ch.current_value,
            detected_at=ch.detected_at,
        )
        for ch in changes
    ]
    spark.createDataFrame(rows).write.mode("append").saveAsTable(c.CHANGES_TABLE)
    logger.info("%d mudancas de schema gravadas no log", len(rows))


def write_events(
    spark: SparkSession, events: list[Event], now_utc: datetime
) -> None:
    """Enfileira os eventos para o publicador enviar ao hub.

    `source_event_id` inclui `change_type` e coluna no hash implicito do
    error_code+description — na pratica, o determinismo por dia (mesmo
    padrao de asset_observability) ja basta: a mesma tabela nao deveria
    reportar duas mudancas incompativeis alertaveis diferentes no mesmo
    dia com frequencia, e se reportar, a segunda so incrementa a
    ocorrencia do mesmo incidente em vez de abrir outro — comportamento
    aceitavel, nao um bug.
    """
    if not events:
        return
    day = now_utc.strftime("%Y%m%d")
    rows = [
        Row(
            source=e.source, result=e.result, natural_key=e.asset,
            domain=e.domain, severity=e.severity, error_code=e.error_code,
            description=e.description,
            source_event_id=f"{e.source}:{e.asset}:{day}",
            observed_at=now_utc, sent_at=None,
        )
        for e in events
    ]
    spark.createDataFrame(rows).write.mode("append").saveAsTable(
        c.STAGING_EVENTS_TABLE
    )
    logger.info("%d eventos de schema enfileirados", len(rows))


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------


def process(
    spark: SparkSession,
    catalog: str,
    schema: str,
    now_utc: datetime,
    overrides: dict[str, dict] | None = None,
) -> tuple[list[SchemaChange], list[Event]]:
    """Avalia a estrutura de todos os ativos monitorados de um schema.

    Uma tabela que falhe na leitura de colunas — removida no meio da
    execucao, sem permissao — e registrada e o job segue para as demais,
    mesma politica de robustez de `asset_observability.process`.
    """
    assets = read_monitored_assets(spark, catalog, schema, overrides)
    existing_tables = read_existing_tables(spark, catalog, schema)

    all_changes: list[SchemaChange] = []
    all_events: list[Event] = []
    snapshots: dict[str, dict[str, ColumnSnapshot]] = {}

    for asset in assets:
        if asset.full_name not in existing_tables:
            change = _with_asset(
                table_dropped_change(asset.full_name, now_utc), asset
            )
            all_changes.append(change)
            evento = to_event(change, asset)
            if evento:
                all_events.append(evento)
            continue

        try:
            current = read_columns(spark, catalog, asset.full_name, now_utc)
        except Exception:
            logger.exception("falha ao ler colunas de %s", asset.full_name)
            continue

        snapshots[asset.full_name] = current

        try:
            previous = read_previous_snapshot(spark, asset.full_name)
        except Exception:
            logger.exception(
                "falha ao ler snapshot anterior de %s; comparacao pulada"
                " nesta execucao", asset.full_name,
            )
            continue

        if previous is None:
            logger.info(
                "%s: primeira execucao com snapshot de schema, sem base"
                " de comparacao", asset.full_name,
            )
            continue

        for change in compare_columns(asset.full_name, previous, current, now_utc):
            change = _with_asset(change, asset)
            all_changes.append(change)
            evento = to_event(change, asset)
            if evento:
                all_events.append(evento)

    for full_name, columns in snapshots.items():
        write_snapshot(spark, full_name, columns)

    return all_changes, all_events


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True, action="append")
    parser.add_argument(
        "--monitored-assets-path",
        default=c.DEFAULT_MONITORED_ASSETS_PATH,
        help="YAML de ativos monitorados; tem precedencia sobre as tags",
    )
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()
    now_utc = datetime.now(timezone.utc)
    overrides = read_asset_overrides(args.monitored_assets_path)

    for schema in args.schema:
        changes, events = process(spark, args.catalog, schema, now_utc, overrides)
        write_changes(spark, changes)
        write_events(spark, events, now_utc)


if __name__ == "__main__":
    main()
