"""Coleta freshness e volume dos ativos monitorados.

Roda como job Databricks, uma vez por dia. Para cada tabela monitorada:

1. executa DESCRIBE DETAIL, que devolve quando a tabela foi atualizada pela
   ultima vez (lastModified), o tamanho em bytes e a quantidade de arquivos;
2. compara com a expectativa declarada nas tags e com a observacao anterior;
3. grava a observacao no historico e, quando o estado muda, um evento na
   tabela de staging.

O job nunca fala com o banco do hub. Ele so escreve em Delta; quem envia os
eventos e o publicador, que roda separado. Assim uma indisponibilidade do
hub nunca afeta este job nem os pipelines de negocio.

O ambiente (dev ou prd) vem de --environment, que o bundle preenche com
${bundle.target}, ou e detectado pelo workspace. O job varre todos os
catalogos que comecam com dev_ ou prd_, conforme o ambiente.

Uso:

    python asset_observability.py --environment prod
    python asset_observability.py --environment prd --catalog prd_logistics --schema corp_curated
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    BooleanType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

import constants as c

logger = logging.getLogger("asset-observability")

#: Versao do codigo. Atualize a cada deploy: aparece na primeira linha do
#: log, para confirmar qual versao esta de fato rodando.
ASSET_OBSERVABILITY_VERSION = "2026-10-09.1"

#: Tamanho maximo de cada linha da mensagem de erro logada (ver _resumo_erro()).
RESUMO_ERRO_LIMITE = 300

#: Quantas linhas da mensagem o resumo mostra.
#:
#: Uma linha so nao basta para Py4JJavaError: a primeira linha e sempre o
#: wrapper generico do py4j ("An error occurred while calling o499.sql."),
#: e o codigo de erro de verdade (PERMISSION_DENIED, TABLE_OR_VIEW_NOT_FOUND)
#: vem na 2a ou 3a linha. Com so a 1a linha, a causa real fica escondida.
RESUMO_ERRO_MAX_LINHAS = 3

#: Serializa str(exc) entre threads — ver _resumo_erro().
_RESUMO_ERRO_LOCK = threading.Lock()


def _resumo_erro(exc: Exception) -> str:
    """Tipo da excecao + as primeiras `RESUMO_ERRO_MAX_LINHAS` linhas da
    mensagem, cada uma truncada.

    Excecoes vindas da JVM (Py4JJavaError, AnalysisException) embutem a
    stack Java inteira dentro do proprio `str(exc)` — nao e o traceback do
    Python duplicando nada, a mensagem JA vem assim. Logar sem limite
    imprime centenas de linhas por ocorrencia; logar so a 1a esconde o
    codigo de erro de verdade, que normalmente esta na 2a ou 3a linha.
    Sem traceback nenhum: quem precisar da stack completa tem a pagina de
    run do Databricks.

    `str(exc)` de um Py4JJavaError sem wrapper do PySpark (deco()) busca o
    `toString()` do lado Java numa chamada ao vivo de volta pro gateway
    py4j — diferente da execucao normal de spark.sql(), que o Databricks
    documenta como segura em paralelo, essa busca pontual de detalhe de
    excecao nao tem a mesma garantia. Processar varios ativos ao mesmo
    tempo (ver process()) levanta a hipotese de duas threads competindo
    por essa chamada ao mesmo tempo, perdendo o detalhe. O lock serializa
    so esse passo (raro — so roda no caminho de erro), sem tirar o
    paralelismo da coleta em si.
    """
    with _RESUMO_ERRO_LOCK:
        texto = str(exc)
    linhas = [linha for linha in texto.strip().splitlines() if linha.strip()]
    escolhidas = linhas[:RESUMO_ERRO_MAX_LINHAS]
    truncadas = [
        linha[:RESUMO_ERRO_LIMITE] + ("..." if len(linha) > RESUMO_ERRO_LIMITE else "")
        for linha in escolhidas
    ]
    corpo = "\n    ".join(truncadas)
    omitidas = len(linhas) - len(escolhidas)
    sufixo = f"\n    ... (+{omitidas} linhas omitidas)" if omitidas > 0 else ""
    return f"{type(exc).__name__}: {corpo}{sufixo}"


# ---------------------------------------------------------------------------
# Fuso dos timestamps que o Spark devolve
# ---------------------------------------------------------------------------


def session_timezone(spark: SparkSession) -> tzinfo:
    """Fuso em que esta sessao Spark entrega os valores TIMESTAMP.

    O PySpark converte TimestampType para `datetime` SEM tzinfo. O instante
    vem correto, mas o fuso fica implicito em `spark.sql.session.timeZone`.
    Comparar esse naive com um aware — `datetime.now(timezone.utc)`, que e
    como `now_utc` nasce em main() — levanta
    `TypeError: can't subtract offset-naive and offset-aware datetimes`.

    Ler a conf em vez de assumir UTC importa porque o erro e silencioso: um
    cluster configurado em America/Sao_Paulo devolveria timestamps 3h
    deslocados, e toda medicao de atraso sairia errada sem levantar excecao
    nenhuma — pior que quebrar, porque o painel mostraria numero plausivel.
    """
    try:
        nome = spark.conf.get("spark.sql.session.timeZone")
    except Exception as exc:
        logger.warning(
            "nao foi possivel ler spark.sql.session.timeZone (%s); "
            "assumindo %s para os timestamps do Spark",
            _resumo_erro(exc), c.DEFAULT_SESSION_TIMEZONE,
        )
        nome = None

    # Em teste a sessao e um MagicMock e `nome` nao e str: cai no padrao,
    # que e tambem o padrao real de um cluster Databricks.
    if not isinstance(nome, str) or not nome.strip():
        return timezone.utc

    if nome.strip().upper() in c.NOMES_DE_UTC:
        return timezone.utc

    try:
        return ZoneInfo(nome.strip())
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "spark.sql.session.timeZone = %r nao resolve para um fuso "
            "conhecido (base de fusos ausente ou nome invalido); assumindo "
            "%s. ATENCAO: se a sessao nao estiver realmente em UTC, toda "
            "medicao de atraso sai deslocada sem levantar erro.",
            nome, c.DEFAULT_SESSION_TIMEZONE,
        )
        return timezone.utc


def _as_utc(valor: datetime | None, origem: tzinfo) -> datetime | None:
    """Timestamp do Spark normalizado para UTC consciente de fuso.

    `origem` e o fuso em que um valor naive deve ser interpretado — o da
    sessao Spark, nao o do processo Python. Valor que ja chega aware e
    convertido, nao reinterpretado.

    Normalizado aqui, na fronteira onde o dado entra, e nao em cada
    comparacao: `Observation.age_minutes` nao tem como saber de que fuso
    veio o timestamp, e espalhar a conversao por todos os pontos de uso e
    como o defeito volta.
    """
    if valor is None:
        return None
    if valor.tzinfo is None:
        return valor.replace(tzinfo=origem).astimezone(timezone.utc)
    return valor.astimezone(timezone.utc)


def _path_ausente(exc: Exception) -> bool:
    """`True` quando a falha e de tabela externa orfa (caminho inexistente).

    Separado de _tabela_ausente(): aquele e o bootstrap esperado do
    ambiente, este e dado quebrado no lake que precisa de alguem.
    """
    return c.ERRO_PATH_AUSENTE in str(exc)


def _caminho_ausente(exc: Exception) -> str:
    """O caminho de storage citado na mensagem, ou "" se nao der para ler."""
    achado = c.PATH_AUSENTE_RE.search(str(exc))
    return achado.group(1) if achado else ""


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeException:
    """Afrouxa os limites de volume dentro de uma janela do mes.

    AFROUXA, nunca desliga. Se a janela simplesmente desativasse a regra,
    ela viraria ponto cego mensal — e o dia do expurgo e exatamente quando
    um DELETE errado tem mais chance de passar despercebido, porque todo
    mundo espera a tabela encolher.
    """

    reason: str
    days_of_month: tuple[int, ...]
    variation_min_pct: float | None = None
    variation_max_pct: float | None = None
    row_count_min: int | None = None
    row_count_max: int | None = None

    def applies_on(self, dia: int) -> bool:
        return dia in self.days_of_month


@dataclass(frozen=True)
class VolumeRule:
    """Limites de volume de um ativo.

    Percentual e absoluto convivem por AND: viola se QUALQUER limite
    estourar. O mais apertado vence — a leitura segura, porque a
    alternativa faria um limite afrouxar o outro.

    Minimo e maximo sao separados de proposito. A tolerancia unica anterior
    comparava `abs(variacao)`, o que forcava o mesmo numero para os dois
    lados; a maioria das tabelas de fato quer "pode crescer 40%, mas
    encolher 10% ja e suspeito".
    """

    metric: str = c.METRIC_ROW_COUNT
    variation_min_pct: float = c.DEFAULT_VARIATION_MIN_PCT
    variation_max_pct: float = c.DEFAULT_VARIATION_MAX_PCT
    row_count_min: int | None = c.DEFAULT_ROW_COUNT_MIN
    row_count_max: int | None = None
    exceptions: tuple[VolumeException, ...] = ()

    def effective(self, dia: int) -> VolumeRule:
        """A regra com a excecao do dia aplicada por cima, se houver."""
        for excecao in self.exceptions:
            if excecao.applies_on(dia):
                return replace(
                    self,
                    variation_min_pct=(
                        excecao.variation_min_pct
                        if excecao.variation_min_pct is not None
                        else self.variation_min_pct
                    ),
                    variation_max_pct=(
                        excecao.variation_max_pct
                        if excecao.variation_max_pct is not None
                        else self.variation_max_pct
                    ),
                    row_count_min=(
                        excecao.row_count_min
                        if excecao.row_count_min is not None
                        else self.row_count_min
                    ),
                    row_count_max=(
                        excecao.row_count_max
                        if excecao.row_count_max is not None
                        else self.row_count_max
                    ),
                    exceptions=(),
                )
        return self


@dataclass(frozen=True)
class Asset:
    """Uma tabela observada e as expectativas declaradas nas tags dela.

    `freshness_sla_minutes` None significa que nao ha expectativa avaliavel
    (sem tag ou tag invalida): o ativo tem os dados coletados, mas nao gera
    eventos de freshness nem de volume.
    """

    full_name: str
    domain: str
    criticality: str
    freshness_sla_minutes: int | None
    volume: VolumeRule

    @property
    def severity(self) -> str:
        return c.SEVERITY_BY_CRITICALITY.get(
            self.criticality.upper(), c.DEFAULT_SEVERITY
        )


@dataclass(frozen=True)
class Observation:
    """O que o DESCRIBE DETAIL devolveu para um ativo, num instante."""

    asset: str
    updated_at: datetime
    size_bytes: int
    file_count: int
    row_count: int | None
    observed_at: datetime

    @property
    def age_minutes(self) -> float:
        """Quantos minutos desde a ultima atualizacao da tabela.

        Minutos, e nao horas, porque o formato de frequencia admite "5min":
        um prazo sub-horario truncado para um inteiro de horas viraria zero,
        e o ativo ficaria permanentemente atrasado.
        """
        delta = self.observed_at - self.updated_at
        return delta.total_seconds() / 60


@dataclass(frozen=True)
class Event:
    """Resultado de uma avaliacao, no formato que o hub espera.

    `resultado` e sempre PASS ou FAIL: o hub precisa dos dois para abrir e
    para fechar incidente. Enviar so as violacoes faria um incidente de
    freshness abrir e nunca fechar.
    """

    source: str
    result: str
    asset: str
    domain: str
    severity: str
    error_code: str | None
    description: str | None


# ---------------------------------------------------------------------------
# Leitura do estado atual
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Destinations:
    """Nomes completos das tabelas de destino de um ambiente."""

    observations: str
    commit_metrics: str
    events: str

    @classmethod
    def for_environment(cls, environment: str) -> Destinations:
        base = (
            f"{c.GOVERNANCE_CATALOG_BY_ENVIRONMENT[environment]}"
            f".{c.GOVERNANCE_SCHEMA}"
        )
        return cls(
            observations=f"{base}.{c.OBSERVATIONS_TABLE}",
            commit_metrics=f"{base}.{c.COMMIT_METRICS_TABLE}",
            events=f"{base}.{c.STAGING_EVENTS_TABLE}",
        )

    @property
    def all(self) -> frozenset[str]:
        return frozenset((self.observations, self.commit_metrics, self.events))


# ---------------------------------------------------------------------------
# Bootstrap das tabelas de destino
#
# Criadas por DDL explicito, nao por saveAsTable: o write-as-table infere o
# schema do DataFrame e nao declara PRIMARY KEY, comentario ou tag nenhuma.
# Esta criacao roda uma vez no inicio do job, e so quando a tabela ainda nao
# existe — depois disso, write_observations/write_events/write_commit_metrics
# so fazem append numa tabela que ja nasceu do jeito certo.
#
# PRIMARY KEY aqui e a constraint informativa do Unity Catalog (NAO
# ENFORCED — Delta nao rejeita duplicata nenhuma por causa dela). Ela existe
# para documentar o grao de cada tabela para quem consome via catalogo, nao
# para garantir unicidade em runtime.
# ---------------------------------------------------------------------------


def _governance_tags(schema: str) -> dict[str, str]:
    """Tags de bootstrap mais `vc_medallion_layer`, derivada do schema.

    O schema de destino E a camada: GOVERNANCE_SCHEMA e literalmente
    "corp_cleansed", entao a tag nunca pode divergir do schema onde a
    tabela realmente mora.
    """
    camada = schema.split("_", 1)[-1]
    return {**c.GOVERNANCE_TABLE_TAGS, "vc_medallion_layer": camada}


def _set_table_tags(spark: SparkSession, full_name: str, tags: dict[str, str]) -> None:
    pares = ", ".join(f"'{chave}' = '{valor}'" for chave, valor in tags.items())
    spark.sql(f"ALTER TABLE {full_name} SET TAGS ({pares})")


def _create_observations_table(spark: SparkSession, full_name: str) -> None:
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {full_name} (
          asset STRING NOT NULL
            COMMENT 'Nome completo do ativo observado (catalogo.schema.tabela).',
          updated_at TIMESTAMP
            COMMENT 'lastModified do DESCRIBE DETAIL no momento da observacao. NULL quando o job TENTOU ler o ativo e nao conseguiu (tabela externa orfa, sem permissao): a linha registra a tentativa, com as metricas zeradas. Filtre por updated_at IS NOT NULL para considerar so leituras reais.',
          size_bytes BIGINT
            COMMENT 'sizeInBytes do DESCRIBE DETAIL no momento da observacao. Zero numa linha de tentativa (updated_at NULL).',
          file_count BIGINT
            COMMENT 'numFiles do DESCRIBE DETAIL no momento da observacao. Zero numa linha de tentativa (updated_at NULL).',
          row_count BIGINT
            COMMENT 'COUNT(*) do ativo no momento da observacao; NULL quando a contagem falhou — nao e o mesmo que zero linhas. Zero com updated_at NULL e linha de tentativa, nao tabela vazia: tabela vazia de verdade tem zero AQUI e updated_at preenchido.',
          observed_at TIMESTAMP NOT NULL
            COMMENT 'Instante UTC em que esta execucao do job coletou a observacao.',
          CONSTRAINT pk_asset_observability PRIMARY KEY (asset, observed_at)
        )
        USING DELTA
        COMMENT 'Historico de observacoes de freshness e volume por ativo monitorado. Uma linha por ativo por execucao do asset_observability.py.'
        """
    )


def _create_commit_metrics_table(spark: SparkSession, full_name: str) -> None:
    campos_metricas = "\n".join(
        f"  {campo} BIGINT COMMENT '{origem} do operationMetrics; NULL quando a operacao nao publica esta metrica.',"
        for campo, origem in c.COMMIT_METRIC_FIELDS.items()
    )
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {full_name} (
          asset STRING NOT NULL
            COMMENT 'Nome completo do ativo (catalogo.schema.tabela) dono do commit.',
          delta_version BIGINT NOT NULL
            COMMENT 'Versao Delta do commit, ou da ultima versao da janela quando a linha e agregada.',
          commit_timestamp TIMESTAMP
            COMMENT 'Timestamp do commit no transaction log Delta.',
          operation STRING
            COMMENT 'Operacao Delta (WRITE, MERGE, OPTIMIZE, ...), ou AGGREGATED quando o teto de commits por execucao estourou.',
          commits_collapsed BIGINT
            COMMENT 'Quantos commits foram colapsados nesta linha; 1 fora do caso agregado.',
          has_gap BOOLEAN
            COMMENT 'TRUE quando o teto de commits (COMMIT_BATCH_CAP) impediu capturar todos os commits da janela.',
          observed_at TIMESTAMP NOT NULL
            COMMENT 'Instante UTC em que esta execucao do job coletou o commit.',
{campos_metricas}
          CONSTRAINT pk_asset_commit_metrics PRIMARY KEY (asset, delta_version)
        )
        USING DELTA
        COMMENT 'Log de metricas por commit Delta dos ativos monitorados. Fase 1: so observa, nenhuma regra de alerta consome ainda. Retencao de {c.COMMIT_METRICS_RETENTION_MONTHS} meses, aplicada pelo proprio job (o commit mais recente de cada ativo nunca e apagado).'
        """
    )


EVENT_ID_COMMENT = (
    "Id deterministico do produtor, usado pelo publicador para reenviar sem "
    "duplicar. No asset_observability: SHA-256 (hex) de fonte, ativo e "
    "instante da execucao."
)


def _sync_event_id_comment(spark: SparkSession, full_name: str) -> None:
    """Atualiza o comentario de `source_event_id` numa staging que ja existia.

    O CREATE TABLE IF NOT EXISTS nao toca tabela existente, entao o texto
    antigo (id diario) ficaria no catalogo. Falha aqui so e avisada: um
    comentario desatualizado nao justifica derrubar a coleta.
    """
    try:
        spark.sql(
            f"ALTER TABLE {full_name} ALTER COLUMN source_event_id "
            f"COMMENT '{EVENT_ID_COMMENT}'"
        )
    except Exception as exc:
        logger.warning(
            "nao foi possivel atualizar o comentario de source_event_id em %s: %s",
            full_name, _resumo_erro(exc),
        )


def _create_events_table(spark: SparkSession, full_name: str) -> None:
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {full_name} (
          source STRING NOT NULL
            COMMENT 'Pilar da avaliacao: FRESHNESS ou VOLUME.',
          result STRING NOT NULL
            COMMENT 'Veredito da avaliacao: PASS ou FAIL.',
          natural_key STRING NOT NULL
            COMMENT 'Nome completo do ativo avaliado (catalogo.schema.tabela).',
          domain STRING
            COMMENT 'Dominio de negocio do ativo, usado para rotear o alerta no hub.',
          severity STRING
            COMMENT 'Severidade do incidente, derivada da criticidade declarada do ativo.',
          error_code STRING
            COMMENT 'Codigo de erro estavel em ingles (ASSET_STALE, VOLUME_OUT_OF_RANGE, EMPTY_LOAD); NULL quando result = PASS.',
          description STRING
            COMMENT 'Mensagem legivel em pt-BR do incidente; NULL quando result = PASS.',
          source_event_id STRING NOT NULL
            COMMENT '{EVENT_ID_COMMENT}',
          observed_at TIMESTAMP NOT NULL
            COMMENT 'Instante UTC em que esta execucao do job gerou o evento.',
          sent_at TIMESTAMP
            COMMENT 'Instante UTC em que o publicador enviou o evento ao hub; NULL enquanto pendente.',
          CONSTRAINT pk_observability_events PRIMARY KEY (source_event_id)
        )
        USING DELTA
        COMMENT 'Fila de staging dos eventos de freshness/volume a enviar ao hub. Escrita por este job; consumida e marcada como enviada pelo publicador (hub_publisher.py).'
        """
    )


def ensure_destination_tables(spark: SparkSession, destinations: Destinations) -> None:
    """Garante que as tres tabelas de destino existem, criando por DDL quando faltam.

    Chamada uma vez no inicio do job, antes de varrer qualquer catalogo —
    e exatamente o que elimina a corrida que existia antes: ler uma tabela
    de destino que so seria criada no primeiro write, no fim do
    processamento do primeiro schema.
    """
    tabelas = (
        (destinations.observations, _create_observations_table),
        (destinations.commit_metrics, _create_commit_metrics_table),
        (destinations.events, _create_events_table),
    )
    for full_name, criar in tabelas:
        if spark.catalog.tableExists(full_name):
            if full_name == destinations.events:
                _sync_event_id_comment(spark, full_name)
            continue
        criar(spark, full_name)
        _set_table_tags(spark, full_name, _governance_tags(c.GOVERNANCE_SCHEMA))
        logger.info("tabela de destino criada: %s", full_name)


FREQUENCY_PATTERN = re.compile(r"^(\d+)(min|h|d|w|m|y)$")


def parse_frequency(valor: str) -> int:
    """Converte "5min", "1h", "1d", "1w", "1m", "1y" em minutos.

    Levanta ValueError no que nao reconhece, DE PROPOSITO. A versao
    anterior era um dicionario de enum com um default de 24h, e um valor
    fora do mapa caia nesse default em silencio: uma tabela declarando
    "5min" seria avaliada com prazo de 24 horas, 288 vezes a janela
    pretendida, sem erro e sem log. Falha silenciosa e exatamente o modo de
    falha que esta solucao existe para eliminar — inclusive em si mesma.

    `min` vem antes de `m` na alternancia porque o regex e guloso da
    esquerda para a direita: com a ordem invertida, "5min" casaria "5m" e
    sobraria um "in" que invalidaria a expressao inteira.
    """
    casou = FREQUENCY_PATTERN.match(valor.strip().lower())
    if not casou:
        raise ValueError(
            f"frequencia '{valor}' nao reconhecida. Formato esperado: "
            f"<numero><unidade>, unidade em {sorted(c.MINUTES_PER_UNIT)}"
        )
    quantidade, unidade = casou.groups()
    return int(quantidade) * c.MINUTES_PER_UNIT[unidade]


def read_tags_by_table(
    spark: SparkSession, catalog: str, schema: str
) -> dict[str, dict[str, str]]:
    """Tags efetivas de cada tabela do schema, com heranca.

    Le `table_tags` E `schema_tags`. O padrao corporativo aplica
    `vc_data_domain` no SCHEMA, nao na tabela — lendo so `table_tags`, o
    dominio nunca seria encontrado, todo ativo cairia em DEFAULT_DOMAIN, e
    o roteamento por dominio deixaria de existir sem ninguem perceber: o
    alerta continuaria chegando, sempre no mesmo canal.

    A tag da tabela vence a do schema, porque o mais especifico manda.
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
    """Le o YAML de ativos monitorados, indexado por nome completo.

    Este arquivo tem precedencia sobre as tags do Unity Catalog, e existe
    para dois casos: ativo ainda sem tag, durante o onboarding, e
    sobrescrita deliberada quando a tag esta errada e a correcao no
    catalogo depende de outra equipe.

    Arquivo ausente ou vazio NAO e erro e nao apaga nada — mesma protecao
    que vale para a fonte YAML das expectativas de execucao. Tratar "nao
    veio nada" como "nao existe mais nada" transformaria um arquivo movido
    de lugar em perda silenciosa de configuracao.
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


def parse_volume_rule(bloco: dict | None, ativo: str) -> VolumeRule:
    """Converte o bloco `volume` do YAML em VolumeRule.

    Ausente, vale a regra padrao — variacao simetrica de 40% e minimo
    absoluto de 1 linha, que e a forma de dizer "tabela monitorada vazia e
    sempre falha".

    Um bloco mal formado nao derruba o ativo nem a varredura: registra o
    erro e cai no padrao. A alternativa seria um erro de digitacao no YAML
    tirar do ar a observacao de tudo.
    """
    if not bloco:
        return VolumeRule()

    try:
        metric = bloco.get("metric", c.METRIC_ROW_COUNT)
        if metric not in c.VALID_METRICS:
            raise ValueError(
                f"metric '{metric}' invalida; use uma de "
                f"{sorted(c.VALID_METRICS)}"
            )
        excecoes = tuple(
            VolumeException(
                reason=item.get("reason", ""),
                days_of_month=tuple(item.get("days_of_month", ())),
                variation_min_pct=item.get("variation_min_pct"),
                variation_max_pct=item.get("variation_max_pct"),
                row_count_min=item.get("row_count_min"),
                row_count_max=item.get("row_count_max"),
            )
            for item in bloco.get("exceptions", [])
        )
        return VolumeRule(
            metric=metric,
            variation_min_pct=float(
                bloco.get("variation_min_pct", c.DEFAULT_VARIATION_MIN_PCT)
            ),
            variation_max_pct=float(
                bloco.get("variation_max_pct", c.DEFAULT_VARIATION_MAX_PCT)
            ),
            row_count_min=bloco.get("row_count_min", c.DEFAULT_ROW_COUNT_MIN),
            row_count_max=bloco.get("row_count_max"),
            exceptions=excecoes,
        )
    except (ValueError, TypeError, AttributeError) as erro:
        logger.error(
            "%s: bloco `volume` invalido (%s); usando a regra padrao",
            ativo, erro,
        )
        return VolumeRule()


def _tabela_interna(nome_tabela: str) -> bool:
    """Tabela interna de pipeline, que nunca e ativo monitoravel de verdade."""
    return nome_tabela.startswith(c.INTERNAL_TABLE_PREFIX) or bool(
        c.PIPELINE_EVENT_LOG_PATTERN.match(nome_tabela)
    )


def read_assets(
    spark: SparkSession,
    catalog: str,
    schema: str,
    overrides: dict[str, dict] | None = None,
    excluded: frozenset[str] = frozenset(),
) -> list[Asset]:
    """Lista TODAS as tabelas Delta do schema, com ou sem tag.

    `excluded` sao as tabelas de destino do proprio job (Destinations.all).
    Elas moram em <gov>.corp_cleansed, que a varredura sem --schema alcanca,
    e levam `vc_refresh_frequency` pelas tags de bootstrap: sem a exclusao,
    o job observaria a si mesmo e abriria incidente sobre a propria saida.

    Toda tabela tem observacao e metricas de commit coletadas: o historico
    precisa existir antes de alguem declarar uma expectativa, senao a
    primeira comparacao de volume so nasce depois da tag.

    As tags do Unity Catalog sao a fonte das expectativas, e `overrides` (do
    YAML) tem precedencia sobre elas, campo a campo.

    So quem tem expectativa avaliavel (`freshness_sla_minutes` preenchido)
    gera eventos. Ficam sem SLA, e portanto so coletadas, as tabelas:
    - sem a tag de frequencia: ninguem prometeu quando seriam atualizadas;
    - com frequencia invalida: o erro e logado, sem derrubar a varredura.

    Nao ha piso de frequencia. Uma tabela horaria lida por um job que roda
    uma vez por dia so tem o atraso visto quando o job passa, mas, se nesse
    momento ela esta parada ha mais que o prazo, isso e um FAIL verdadeiro:
    detectar tarde e melhor que nunca detectar, e o volume dela tambem
    precisa ser avaliado.
    """
    tags_by_table = read_tags_by_table(spark, catalog, schema)
    overrides = overrides or {}

    # Views, streaming tables e formatos nao Delta nao tem DESCRIBE DETAIL
    # nem DESCRIBE HISTORY uteis; ficam fora da coleta.
    listadas = {
        f"{row.table_catalog}.{row.table_schema}.{row.table_name}"
        for row in spark.sql(
            f"""
            SELECT table_catalog, table_schema, table_name
              FROM {catalog}.information_schema.tables
             WHERE table_schema = '{schema}'
               AND table_type IN ('MANAGED', 'EXTERNAL')
               AND data_source_format = 'DELTA'
            """
        ).collect()
    }

    # Um ativo declarado no YAML entra mesmo que a listagem nao o traga: e o
    # caso de onboarding. Restrito ao catalogo e schema desta varredura, para
    # uma execucao nao arrastar ativo de outro escopo.
    prefixo = f"{catalog}.{schema}."
    candidatos = listadas | {n for n in overrides if n.startswith(prefixo)}

    proprias = candidatos & excluded
    if proprias:
        logger.info(
            "%s.%s: %d tabelas de destino do proprio job excluidas da "
            "observacao: %s",
            catalog, schema, len(proprias), ", ".join(sorted(proprias)),
        )
        candidatos = candidatos - proprias

    # Tabelas internas de pipeline ficam de fora mesmo se alguem declarar uma
    # no YAML por engano: nunca respondem DESCRIBE HISTORY num cluster
    # Assigned, declaradas ou nao. Excluidas em silencio, o job nao diria
    # quais tabelas deixou de observar.
    internas = {n for n in candidatos if _tabela_interna(n.rsplit(".", 1)[-1])}
    if internas:
        logger.info(
            "%s.%s: %d tabelas internas de pipeline excluidas: %s",
            catalog, schema, len(internas), ", ".join(sorted(internas)),
        )
    nomes = candidatos - internas

    assets = []
    for name in sorted(nomes):
        declarado = _merge_declaration(
            tags_by_table.get(name, {}), overrides.get(name, {})
        )

        sla_minutes = None
        if c.TAG_REFRESH_FREQUENCY in declarado:
            try:
                sla_minutes = _sla_in_minutes(declarado)
            except ValueError as erro:
                logger.error("%s: %s — sem avaliacao de freshness", name, erro)

        assets.append(
            Asset(
                full_name=name,
                domain=declarado.get(c.TAG_DATA_DOMAIN, c.DEFAULT_DOMAIN),
                criticality=declarado.get(c.TAG_CRITICALITY, ""),
                freshness_sla_minutes=sla_minutes,
                volume=parse_volume_rule(
                    overrides.get(name, {}).get("volume"), name
                ),
            )
        )

    logger.info(
        "%s.%s: %d ativos coletados, %d com expectativa avaliavel",
        catalog, schema, len(assets),
        sum(a.freshness_sla_minutes is not None for a in assets),
    )
    return assets


def _merge_declaration(tags: dict[str, str], override: dict) -> dict[str, str]:
    """Tags do catalogo com o YAML por cima, campo a campo.

    Precedencia por CAMPO, nao por ativo: uma entrada no YAML que corrige
    so a criticidade nao apaga o dominio nem a frequencia que vieram da
    tag. Substituir o ativo inteiro faria uma sobrescrita pontual virar
    perda de tudo que nao foi repetido no arquivo.

    As chaves do YAML sao as mesmas das tags (`vc_refresh_frequency`,
    `vc_criticality`, ...) exatamente para que esta mesclagem seja direta e
    ninguem precise manter dois vocabularios em sincronia.
    """
    mesclado = dict(tags)
    for chave in (
        c.TAG_REFRESH_FREQUENCY,
        c.TAG_FRESHNESS_SLA,
        c.TAG_DATA_DOMAIN,
        c.TAG_CRITICALITY,
    ):
        if chave in override and override[chave] is not None:
            mesclado[chave] = str(override[chave])
    return mesclado


def _sla_in_minutes(tags: dict[str, str]) -> int:
    """Prazo em minutos: o SLA explicito manda; sem ele, vale a frequencia.

    Os dois usam o mesmo formato. Sao coisas diferentes: a frequencia diz
    de quanto em quanto tempo o dado atualiza, o SLA diz quanto atraso o
    negocio aguenta antes de virar incidente. Uma tabela horaria com SLA de
    2h absorve uma carga lenta sem acordar ninguem, e so alerta quando
    perde dois ciclos seguidos.
    """
    sla = tags.get(c.TAG_FRESHNESS_SLA)
    if sla:
        return parse_frequency(sla)
    return parse_frequency(tags[c.TAG_REFRESH_FREQUENCY])


def describe(
    spark: SparkSession, asset: Asset, now_utc: datetime, session_tz: tzinfo
) -> Observation:
    """Executa DESCRIBE DETAIL e devolve a observacao do momento.

    lastModified e a evidencia de freshness tecnica. Ele tambem se move com
    OPTIMIZE e VACUUM, o que e aceito: manutencao isolada e rara no escopo
    monitorado. Onde a aproximacao nao servir, DESCRIBE HISTORY responde com
    precisao, mas custa uma consulta por tabela.

    `lastModified` chega do Spark sem fuso e e normalizado para UTC aqui,
    na fronteira: `observed_at` e aware, e sem isso a subtracao dos dois em
    Observation.age_minutes levanta TypeError.
    """
    detail = spark.sql(f"DESCRIBE DETAIL {asset.full_name}").collect()[0]
    return Observation(
        asset=asset.full_name,
        updated_at=_as_utc(detail["lastModified"], session_tz),
        size_bytes=detail["sizeInBytes"] or 0,
        file_count=detail["numFiles"] or 0,
        row_count=count_rows(spark, asset.full_name),
        observed_at=now_utc,
    )


def count_rows(spark: SparkSession, full_name: str) -> int | None:
    """COUNT(*) sem filtro — leitura de metadado, nao varredura.

    Uma tabela Delta guarda `numRecords` por arquivo no transaction log, e
    um COUNT(*) SEM PREDICADO e respondido somando essas estatisticas, sem
    abrir um Parquet. Mesma ordem de grandeza do DESCRIBE DETAIL que ja
    fazemos. Com predicado, ou sem estatisticas coletadas, degrada para
    varredura de verdade — nao e o caso aqui.

    Isso e comportamento ESPERADO, ainda nao medido neste ambiente:
    deletion vectors e tabelas convertidas de fora do Delta sao as duas
    excecoes conhecidas. Falha devolve None, e o ativo fica sem avaliacao
    de volume nesta execucao em vez de derrubar a observacao inteira — a
    freshness continua valendo.
    """
    try:
        return spark.sql(f"SELECT COUNT(*) AS n FROM {full_name}").collect()[0]["n"]
    except Exception as exc:
        logger.error("falha ao contar linhas de %s: %s", full_name, _resumo_erro(exc))
        return None


#: Tabelas de destino ja avisadas como ausentes nesta execucao do job.
#:
#: Na primeira execucao em um ambiente/schema novo, as tabelas de governanca
#: ainda nao existem — elas so sao criadas no primeiro write, no fim do
#: processamento. Sem este cache, cada ativo gera um AVISO repetido com o
#: mesmo diagnostico, e um catalogo com centenas de ativos produz centenas
#: de linhas identicas no log por uma unica causa.
_TABELAS_AUSENTES_AVISADAS: set[str] = set()


def _tabela_ausente(exc: Exception) -> bool:
    """`True` quando a excecao e por tabela/view de destino inexistente.

    Distingue o caso esperado — bootstrap do ambiente, a tabela ainda nao
    foi criada — de qualquer outra falha (permissao, timeout, etc.), que
    continua indo para o log com o traceback completo.
    """
    return c.ERRO_TABELA_AUSENTE in str(exc)


def _avisar_tabela_ausente_uma_vez(tabela: str, contexto: str) -> None:
    """Registra UM aviso por tabela ausente por execucao, nao um por ativo."""
    if tabela in _TABELAS_AUSENTES_AVISADAS:
        return
    _TABELAS_AUSENTES_AVISADAS.add(tabela)
    logger.warning(
        "%s: tabela %s ainda nao existe neste ambiente. Esperado na primeira "
        "execucao — ela e criada automaticamente no primeiro write do job. "
        "Avisos repetidos para esta tabela estao suprimidos nesta execucao.",
        contexto,
        tabela,
    )


def read_previous_observations(
    spark: SparkSession,
    assets: list[str],
    destinations: Destinations,
    session_tz: tzinfo,
) -> dict[str, Observation]:
    """Ultima observacao gravada de cada ativo, numa unica consulta por schema.

    Antes era uma consulta (`WHERE asset = '...'`) por ativo — num schema de
    950 ativos, 950 round-trips so pra isso. Aqui e uma consulta soh, com
    ROW_NUMBER() particionado por ativo, porque DESCRIBE DETAIL/HISTORY sao
    os unicos que realmente precisam ser por tabela (Unity Catalog nao tem
    um "DESCRIBE DETAIL de varias tabelas"); a consulta na NOSSA tabela de
    destino nao tem essa limitacao.

    Tabela de destino ausente (primeira execucao do ambiente) e tratada
    como "nenhum ativo tem observacao anterior", nao como falha: e
    exatamente o estado da primeira execucao de verdade, e propagar a
    excecao aqui derrubaria o schema inteiro antes mesmo do primeiro write
    criar a tabela. Ativo que nao aparece no dict devolvido tambem significa
    "sem observacao anterior" — mesma semantica de quando a tabela existe
    mas o ativo e novo.

    `updated_at IS NOT NULL` descarta as linhas de tentativa (ver
    _observacao_nao_lida()): elas registram que o job nao conseguiu ler o
    ativo, e usar uma delas como referencia desligaria a avaliacao de
    variacao de volume no dia em que a tabela voltasse a funcionar — o
    `previous` viria com metrica zero, e _percent_change() nao calcula
    variacao sobre zero. A referencia e sempre a ultima leitura DE VERDADE.
    """
    if not assets:
        return {}

    lista = ", ".join(f"'{nome}'" for nome in assets)
    try:
        linhas = spark.sql(
            f"""
            SELECT asset, updated_at, size_bytes, file_count, row_count, observed_at
              FROM (
                SELECT asset, updated_at, size_bytes, file_count, row_count, observed_at,
                       ROW_NUMBER() OVER (PARTITION BY asset ORDER BY observed_at DESC) AS rn
                  FROM {destinations.observations}
                 WHERE asset IN ({lista})
                   AND updated_at IS NOT NULL
              )
             WHERE rn = 1
            """
        ).collect()
    except Exception as exc:
        if _tabela_ausente(exc):
            _avisar_tabela_ausente_uma_vez(destinations.observations, "observacao anterior")
        else:
            logger.error("falha ao ler observacoes anteriores do schema: %s", _resumo_erro(exc))
        return {}

    # Mesma normalizacao de fuso da observacao corrente: a anterior volta
    # da nossa tabela Delta e chega naive pelo mesmo caminho. Hoje nenhuma
    # comparacao subtrai esses dois campos, mas deixar metade das
    # Observation aware e metade naive e como o defeito volta.
    return {
        row["asset"]: Observation(
            asset=row["asset"],
            updated_at=_as_utc(row["updated_at"], session_tz),
            size_bytes=row["size_bytes"],
            file_count=row["file_count"],
            row_count=row["row_count"],
            observed_at=_as_utc(row["observed_at"], session_tz),
        )
        for row in linhas
    }


# ---------------------------------------------------------------------------
# Log de metricas de commit (fase 1: so observa)
#
# Um registro por commit Delta. Serve para medir as distribuicoes reais
# antes de escrever regra em cima delas — escrever limite por chute e
# calibrar na base do falso positivo destroi a confianca no canal mais
# rapido do que alerta ausente.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitMetric:
    """Metricas de um commit Delta de um ativo."""

    asset: str
    delta_version: int
    commit_timestamp: datetime
    operation: str
    metrics: dict[str, int | None]
    commits_collapsed: int = 1
    has_gap: bool = False


def read_commit_watermarks(
    spark: SparkSession, assets: list[str], destinations: Destinations
) -> dict[str, int]:
    """Ultima versao ja registrada de cada ativo, numa unica consulta por schema.

    Mesmo padrao de watermark do hub-replication e do coletor, so que
    agregado por ativo numa consulta soh (ver read_previous_observations()
    para o motivo). Ativo ausente do dict devolvido significa "nada
    registrado ainda" — quem chama trata isso como -1, pra que a primeira
    execucao pegue tudo que o historico ainda tiver.
    """
    if not assets:
        return {}

    lista = ", ".join(f"'{nome}'" for nome in assets)
    try:
        linhas = spark.sql(
            f"""
            SELECT asset, MAX(delta_version) AS v
              FROM {destinations.commit_metrics}
             WHERE asset IN ({lista})
             GROUP BY asset
            """
        ).collect()
    except Exception as exc:
        if _tabela_ausente(exc):
            _avisar_tabela_ausente_uma_vez(destinations.commit_metrics, "watermark de commits")
        else:
            logger.error("falha ao ler watermark de commits do schema: %s", _resumo_erro(exc))
        return {}

    return {
        row["asset"]: int(row["v"]) for row in linhas if row["v"] is not None
    }


def _metric_value(metrics: dict, chave: str) -> int | None:
    """Valor da metrica, ou None quando a operacao nao a publica.

    NUNCA zero por ausencia. `operationMetrics` traz chaves diferentes
    conforme a operacao — um OPTIMIZE nao publica metrica de linha
    nenhuma —, e um zero inventado aqui viraria "esta carga trouxe zero
    linhas" na analise, que e outra coisa.
    """
    bruto = (metrics or {}).get(chave)
    if bruto is None:
        return None
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return None


def read_commits(
    spark: SparkSession, asset: str, watermark: int
) -> list[CommitMetric]:
    """Commits novos desde o watermark, do mais antigo para o mais recente.

    DESCRIBE HISTORY devolve por padrao a janela de
    `delta.logRetentionDuration` (30 dias). Se o watermark for mais antigo
    que o que o historico ainda tem, houve perda: a serie fica com buraco,
    e quem for escrever regra em cima dela PRECISA saber disso. A linha
    seguinte ao buraco e marcada com `has_gap`.
    """
    try:
        historico = spark.sql(f"DESCRIBE HISTORY {asset}").collect()
    except Exception as exc:
        # Tabela externa orfa falha aqui E no DESCRIBE DETAIL logo depois,
        # pela mesma causa. Quem reporta e o describe(), que classifica o
        # caso e conta a cobertura perdida; logar aqui tambem rendia dois
        # ERROR com plano de execucao do Spark por tabela — 32 linhas de
        # log para 16 tabelas quebradas, na execucao de 2026-10-07.
        if not _path_ausente(exc):
            logger.error("falha ao ler historico de %s: %s", asset, _resumo_erro(exc))
        return []

    novos = sorted(
        (linha for linha in historico if int(linha["version"]) > watermark),
        key=lambda linha: int(linha["version"]),
    )
    if not novos:
        return []

    primeira_versao = int(novos[0]["version"])
    tem_buraco = watermark >= 0 and primeira_versao > watermark + 1
    if tem_buraco:
        logger.warning(
            "%s: buraco no historico — watermark em %d, primeira versao "
            "disponivel %d. A serie perde %d commits, retidos por menos "
            "tempo que o intervalo entre execucoes.",
            asset, watermark, primeira_versao, primeira_versao - watermark - 1,
        )

    if len(novos) > c.COMMIT_BATCH_CAP:
        logger.warning(
            "%s: %d commits nesta janela, acima do teto de %d. Provavelmente "
            "uma tabela de streaming, que por convencao nao deveria estar "
            "monitorada. Gravando uma linha agregada.",
            asset, len(novos), c.COMMIT_BATCH_CAP,
        )
        return [_aggregate(asset, novos, tem_buraco)]

    return [
        CommitMetric(
            asset=asset,
            delta_version=int(linha["version"]),
            commit_timestamp=linha["timestamp"],
            operation=linha["operation"],
            metrics={
                nome: _metric_value(linha["operationMetrics"], chave)
                for nome, chave in c.COMMIT_METRIC_FIELDS.items()
            },
            has_gap=tem_buraco and indice == 0,
        )
        for indice, linha in enumerate(novos)
    ]


def _aggregate(asset: str, linhas: list, tem_buraco: bool) -> CommitMetric:
    """Uma linha resumindo a janela inteira, quando o teto estoura.

    O watermark avanca para a versao mais recente de proposito. Guardar so
    as primeiras N faria o job nunca alcancar uma tabela de streaming, e a
    cada execucao ele reprocessaria o mesmo atraso crescente.
    """
    ultima = linhas[-1]
    somas: dict[str, int | None] = {}
    for nome, chave in c.COMMIT_METRIC_FIELDS.items():
        valores = [
            v for v in (_metric_value(l["operationMetrics"], chave) for l in linhas)
            if v is not None
        ]
        somas[nome] = sum(valores) if valores else None
    return CommitMetric(
        asset=asset,
        delta_version=int(ultima["version"]),
        commit_timestamp=ultima["timestamp"],
        operation=c.OPERATION_AGGREGATED,
        metrics=somas,
        commits_collapsed=len(linhas),
        has_gap=tem_buraco,
    )


def write_commit_metrics(
    spark: SparkSession,
    commits: list[CommitMetric],
    now_utc: datetime,
    destinations: Destinations,
) -> None:
    """Acrescenta as metricas de commit ao log."""
    if not commits:
        return

    rows = _rows_in_schema_order(
        COMMIT_METRICS_SCHEMA,
        [
            dict(
                asset=m.asset,
                delta_version=m.delta_version,
                commit_timestamp=m.commit_timestamp,
                operation=m.operation,
                commits_collapsed=m.commits_collapsed,
                has_gap=m.has_gap,
                observed_at=now_utc,
                **m.metrics,
            )
            for m in commits
        ],
    )
    spark.createDataFrame(rows, schema=COMMIT_METRICS_SCHEMA).write.mode("append").saveAsTable(
        destinations.commit_metrics
    )
    logger.info("%d metricas de commit gravadas", len(rows))


def purge_commit_metrics(spark: SparkSession, destinations: Destinations) -> None:
    """Apaga do log de commits o que passou de COMMIT_METRICS_RETENTION_MONTHS.

    O commit mais recente de cada ativo fica sempre, mesmo vencido: e dele
    que read_commit_watermarks() tira o watermark. Apagado, um ativo parado
    ha mais tempo que a retencao voltaria ao watermark -1, e o job regravaria
    a cada execucao os commits que o DESCRIBE HISTORY ainda tem, so para
    apaga-los de novo na limpeza seguinte.

    A idade e a do commit (`commit_timestamp`); `observed_at` so entra quando
    o timestamp do commit nao veio.
    """
    tabela = destinations.commit_metrics
    resultado = spark.sql(
        f"""
        MERGE INTO {tabela} AS alvo
        USING (
          SELECT DISTINCT m.asset, m.delta_version
            FROM {tabela} m
            JOIN (SELECT asset, MAX(delta_version) AS ultima
                    FROM {tabela}
                   GROUP BY asset) u
              ON m.asset = u.asset
           WHERE m.delta_version < u.ultima
             AND COALESCE(m.commit_timestamp, m.observed_at)
                 < add_months(current_timestamp(), -{c.COMMIT_METRICS_RETENTION_MONTHS})
        ) AS vencidos
        ON alvo.asset = vencidos.asset AND alvo.delta_version = vencidos.delta_version
        WHEN MATCHED THEN DELETE
        """
    ).collect()
    apagadas = resultado[0]["num_deleted_rows"] if resultado else 0
    logger.info(
        "limpeza do log de commits: %s linhas com mais de %d meses apagadas",
        apagadas, c.COMMIT_METRICS_RETENTION_MONTHS,
    )


# ---------------------------------------------------------------------------
# Avaliacao
# ---------------------------------------------------------------------------


def _humanize(minutes: float) -> str:
    """Minutos em texto curto para a mensagem do incidente.

    Esta string e o que o operador le no Teams. Ela e localizavel — hoje
    sai em pt-BR — ao contrario do `error_code`, que e identificador
    estavel em ingles e nunca traduz.
    """
    if minutes < 90:
        return f"{minutes:.0f}min"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f}h"
    return f"{minutes / 1440:.1f}d"


def evaluate_freshness(asset: Asset, current: Observation) -> Event:
    """O ativo foi atualizado dentro do prazo prometido?"""
    is_stale = current.age_minutes > asset.freshness_sla_minutes

    return Event(
        source=c.SOURCE_FRESHNESS,
        result="FAIL" if is_stale else "PASS",
        asset=asset.full_name,
        domain=asset.domain,
        severity=asset.severity,
        error_code=c.ERROR_CODE_STALE if is_stale else None,
        description=(
            f"Atualizado há {_humanize(current.age_minutes)}; "
            f"o prazo declarado é de {_humanize(asset.freshness_sla_minutes)}."
            if is_stale
            else None
        ),
    )


def evaluate_volume(
    asset: Asset, current: Observation, previous: Observation | None
) -> Event:
    """O volume da tabela esta dentro do declarado?

    Quatro limites, avaliados por AND: viola se QUALQUER um estourar. O
    mais apertado vence.

    Sem observacao anterior nao ha variacao a calcular, e so os limites
    absolutos valem. E deliberado: a primeira execucao estabelece a
    referencia, e inventar um veredito de variacao ali geraria alerta falso
    no dia da implantacao. Vale tambem para o dia em que `row_count` entra
    numa tabela que so tinha historico de bytes — o warm-up nao e um
    estado de erro, e o log diz quando esta acontecendo.
    """
    regra = asset.volume.effective(current.observed_at.day)
    atual = _volume_metric(current, regra.metric)

    if atual is None:
        logger.info(
            "%s: sem metrica de volume (%s) nesta execucao; volume nao avaliado",
            asset.full_name, regra.metric,
        )
        return _volume_event(asset, "PASS", None, None)

    violacoes: list[str] = []

    if regra.row_count_min is not None and atual < regra.row_count_min:
        if atual == 0:
            return _volume_event(
                asset, "FAIL", c.ERROR_CODE_EMPTY, "A tabela está vazia."
            )
        violacoes.append(
            f"{atual} abaixo do mínimo declarado de {regra.row_count_min}"
        )
    if regra.row_count_max is not None and atual > regra.row_count_max:
        violacoes.append(
            f"{atual} acima do máximo declarado de {regra.row_count_max}"
        )

    anterior = _volume_metric(previous, regra.metric) if previous else None
    change = _percent_change(atual, anterior)
    if change is not None:
        if change < regra.variation_min_pct:
            violacoes.append(
                f"variação de {change:+.1f}% abaixo do mínimo de "
                f"{regra.variation_min_pct:+.1f}%"
            )
        elif change > regra.variation_max_pct:
            violacoes.append(
                f"variação de {change:+.1f}% acima do máximo de "
                f"{regra.variation_max_pct:+.1f}%"
            )
    elif previous is not None:
        logger.info(
            "%s: sem referencia de %s na observacao anterior; "
            "so os limites absolutos valem nesta execucao",
            asset.full_name, regra.metric,
        )

    if not violacoes:
        return _volume_event(asset, "PASS", None, None)
    return _volume_event(
        asset, "FAIL", c.ERROR_CODE_VOLUME, "; ".join(violacoes) + "."
    )


def _volume_event(
    asset: Asset, result: str, error_code: str | None, description: str | None
) -> Event:
    return Event(
        source=c.SOURCE_VOLUME,
        result=result,
        asset=asset.full_name,
        domain=asset.domain,
        severity=asset.severity,
        error_code=error_code,
        description=description,
    )


def _volume_metric(observation: Observation | None, metric: str) -> int | None:
    """A metrica escolhida, ou None quando ela nao existe nesta observacao."""
    if observation is None:
        return None
    if metric == c.METRIC_BYTES:
        return observation.size_bytes
    return observation.row_count


def _percent_change(current: int | None, previous: int | None) -> float | None:
    """Variacao percentual entre duas medidas, em pontos percentuais."""
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


# ---------------------------------------------------------------------------
# Escrita
#
# As tres gravacoes abaixo passam `schema=` explicito para createDataFrame,
# em vez de deixar o Spark inferir a partir das linhas. Inferencia falha
# com CANNOT_DETERMINE_TYPE sempre que uma coluna vem None em TODAS as
# linhas do lote — e isso e normal aqui, nao excecao: `sent_at` e sempre
# None em todo write_events (preenchido depois pelo publicador), e colunas
# de metrica como `num_target_rows_copied` ficam None no lote inteiro
# quando nenhum commit coletado foi um MERGE. Schema explicito elimina a
# dependencia de amostra: a coluna nasce tipada e nullable, com ou sem
# valor nao-nulo nenhum no lote.
# ---------------------------------------------------------------------------

OBSERVATIONS_SCHEMA = StructType(
    [
        StructField("asset", StringType(), nullable=False),
        StructField("updated_at", TimestampType(), nullable=True),
        StructField("size_bytes", LongType(), nullable=True),
        StructField("file_count", LongType(), nullable=True),
        StructField("row_count", LongType(), nullable=True),
        StructField("observed_at", TimestampType(), nullable=False),
    ]
)

#: Campos fixos + um BIGINT nullable por chave de COMMIT_METRIC_FIELDS.
#: Concatenados como listas Python antes de criar o StructType — a API
#: real do StructType so aceita somar outro StructType ou um StructField
#: unico, nao uma lista.
_COMMIT_METRICS_FIELDS = [
    StructField("asset", StringType(), nullable=False),
    StructField("delta_version", LongType(), nullable=False),
    StructField("commit_timestamp", TimestampType(), nullable=True),
    StructField("operation", StringType(), nullable=True),
    StructField("commits_collapsed", LongType(), nullable=True),
    StructField("has_gap", BooleanType(), nullable=True),
    StructField("observed_at", TimestampType(), nullable=False),
] + [
    StructField(nome, LongType(), nullable=True)
    for nome in c.COMMIT_METRIC_FIELDS
]
COMMIT_METRICS_SCHEMA = StructType(_COMMIT_METRICS_FIELDS)

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


def _rows_in_schema_order(schema: StructType, registros: list[dict]) -> list[tuple]:
    """Valores de cada registro na ordem das colunas do schema, pelo nome.

    `createDataFrame(..., schema=)` associa os valores de uma linha as
    colunas pela POSICAO, nao pelo nome do campo. Montar a tupla a partir do
    schema faz a ordem do codigo deixar de importar: reordenar campos aqui ou
    no StructType nao troca mais colunas do mesmo tipo em silencio. Campo
    ausente no registro levanta KeyError, em vez de virar NULL.
    """
    return [tuple(registro[campo.name] for campo in schema.fields) for registro in registros]


def event_id(source: str, asset: str, now_utc: datetime) -> str:
    """`source_event_id` de um evento: SHA-256 (hex) de fonte, ativo e instante.

    Um id por execucao, nao por dia. Com o id diario anterior, duas execucoes
    no mesmo dia davam o mesmo id ao FAIL da primeira e ao PASS da segunda,
    e o hub descartava o PASS como duplicado: o incidente so fechava no dia
    seguinte. Continua deterministico por linha, que e o que o publicador
    precisa para reenviar sem duplicar.

    Hexadecimal porque o publicador so aceita [A-Za-z0-9_-] no id, e o nome
    do ativo tem pontos.
    """
    identidade = "||".join([source, asset, now_utc.isoformat()])
    return hashlib.sha256(identidade.encode("utf-8")).hexdigest()


def write_observations(
    spark: SparkSession,
    observations: list[Observation],
    destinations: Destinations,
) -> None:
    """Acrescenta as observacoes ao historico."""
    if not observations:
        return

    rows = _rows_in_schema_order(
        OBSERVATIONS_SCHEMA,
        [
            dict(
                asset=o.asset,
                updated_at=o.updated_at,
                size_bytes=o.size_bytes,
                file_count=o.file_count,
                row_count=o.row_count,
                observed_at=o.observed_at,
            )
            for o in observations
        ],
    )
    spark.createDataFrame(rows, schema=OBSERVATIONS_SCHEMA).write.mode("append").saveAsTable(
        destinations.observations
    )
    logger.info("%d observacoes gravadas", len(rows))


def write_events(
    spark: SparkSession,
    events: list[Event],
    now_utc: datetime,
    destinations: Destinations,
) -> None:
    """Enfileira os eventos para o publicador enviar ao hub (ver event_id())."""
    if not events:
        return

    # Nomes de campo em inglês: viram, sem tradução, as colunas físicas da
    # tabela de staging (Delta), lida por hub_publisher.py e escrita
    # também por data_quality.py — os três precisam concordar.
    rows = _rows_in_schema_order(
        EVENTS_SCHEMA,
        [
            dict(
                source=e.source,
                result=e.result,
                natural_key=e.asset,
                domain=e.domain,
                severity=e.severity,
                error_code=e.error_code,
                description=e.description,
                source_event_id=event_id(e.source, e.asset, now_utc),
                observed_at=now_utc,
                sent_at=None,
            )
            for e in events
        ],
    )
    spark.createDataFrame(rows, schema=EVENTS_SCHEMA).write.mode("append").saveAsTable(
        destinations.events
    )
    logger.info("%d eventos enfileirados", len(rows))


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cobertura:
    """Quantos ativos o escopo entregou e quantos ficaram pelo caminho.

    Existe porque perda de cobertura era silenciosa: uma tabela externa
    orfa nao entra na lista de `falhas` de main(), logo nao deixa o job
    vermelho, e a unica pista era a diferenca entre "N ativos coletados" e
    "M observacoes gravadas" — duas linhas distantes do log, que ninguem
    cruza a mao. Na execucao de 2026-10-07 foram 18 ativos perdidos sem um
    unico sinal consolidado.

    Guarda os NOMES, nao so a contagem: "16 ativos sem observacao" manda o
    operador procurar, "16 ativos sem observacao: <lista>" manda ele
    corrigir.
    """

    coletados: int = 0
    com_expectativa: int = 0
    observados: int = 0
    orfaos: tuple[str, ...] = ()
    outras_falhas: tuple[str, ...] = ()

    def __add__(self, outra: Cobertura) -> Cobertura:
        return Cobertura(
            coletados=self.coletados + outra.coletados,
            com_expectativa=self.com_expectativa + outra.com_expectativa,
            observados=self.observados + outra.observados,
            orfaos=self.orfaos + outra.orfaos,
            outras_falhas=self.outras_falhas + outra.outras_falhas,
        )

    @property
    def sem_observacao(self) -> int:
        return len(self.orfaos) + len(self.outras_falhas)


#: Motivo pelo qual um ativo nao rendeu observacao, devolvido por
#: _collect_and_evaluate() para process() classificar a cobertura.
FALHA_PATH_AUSENTE = "path_ausente"
FALHA_OUTRA = "outra"


def _observacao_nao_lida(asset: Asset, now_utc: datetime) -> Observation:
    """Linha que registra "o job tentou ler este ativo e nao conseguiu".

    Metricas zeradas e `updated_at` NULL. O NULL e o marcador: nenhuma
    tabela Delta lida com sucesso devolve lastModified nulo, entao
    `updated_at IS NULL` separa tentativa de observacao em qualquer consulta
    sobre a tabela.

    Esta linha NAO e avaliada — nao gera evento de freshness nem de volume.
    Avaliar um ativo que nao foi lido produziria veredito sobre dado que
    ninguem viu: um ASSET_STALE eterno (sem `updated_at` nao existe atraso a
    medir) e um EMPTY_LOAD falso vindo do zero.
    """
    return Observation(
        asset=asset.full_name,
        updated_at=None,
        size_bytes=c.METRICA_NAO_LIDA,
        file_count=c.METRICA_NAO_LIDA,
        row_count=c.METRICA_NAO_LIDA,
        observed_at=now_utc,
    )


def _collect_and_evaluate(
    spark: SparkSession,
    asset: Asset,
    watermark: int,
    previous: Observation | None,
    now_utc: datetime,
    session_tz: tzinfo,
) -> tuple[Observation | None, list[Event], list[CommitMetric], str | None]:
    """Trabalho de UM ativo: commits, DESCRIBE DETAIL e avaliacao.

    Isolado do loop de process() para poder rodar em thread propria — cada
    chamada spark.sql() aqui e I/O (round-trip ao Unity Catalog), e o
    Python libera o GIL durante a espera, entao threads concorrentes
    chamando a mesma SparkSession sao seguras e uteis (padrao documentado
    pelo proprio Databricks). O try/except por etapa ja existia no loop
    sequencial; so foi movido pra dentro da funcao que agora roda paralela.
    """
    commits: list[CommitMetric] = []
    try:
        commits.extend(read_commits(spark, asset.full_name, watermark))
    except Exception as exc:
        if not _path_ausente(exc):
            logger.error("falha ao coletar commits de %s: %s", asset.full_name, _resumo_erro(exc))

    try:
        current = describe(spark, asset, now_utc, session_tz)
    except Exception as exc:
        if _path_ausente(exc):
            # Dado quebrado no lake, nao falha de execucao: o registro
            # existe no Unity Catalog e o caminho em storage nao. Uma linha
            # com o caminho, sem o plano do Spark — e o caminho que mostra
            # na hora quando o LOCATION aponta para o lugar errado.
            caminho = _caminho_ausente(exc)
            logger.warning(
                "%s: caminho de armazenamento nao existe ou nao e Delta%s. "
                "Tabela externa orfa no Unity Catalog — gravando linha de "
                "tentativa (metricas zeradas, updated_at nulo), precisa de "
                "saneamento no lake.",
                asset.full_name,
                f" ({caminho})" if caminho else "",
            )
            return (
                _observacao_nao_lida(asset, now_utc),
                [],
                commits,
                FALHA_PATH_AUSENTE,
            )
        logger.error(
            "falha ao descrever %s: %s — gravando linha de tentativa "
            "(metricas zeradas, updated_at nulo)",
            asset.full_name, _resumo_erro(exc),
        )
        return _observacao_nao_lida(asset, now_utc), [], commits, FALHA_OUTRA

    # Avaliacao isolada da coleta, e cada avaliador isolado do outro. Antes
    # uma excecao aqui escapava desta funcao, estourava no futuro.result()
    # de process() e era capturada so no laco de schemas de main() — um
    # unico ativo com expectativa derrubava a observacao de TODOS os ativos
    # do schema. Foi o que o TypeError de fuso fez em
    # dev_governance.corp_cleansed em 2026-10-07: 3 ativos perdidos por um.
    events: list[Event] = []
    if asset.freshness_sla_minutes is not None:
        avaliacoes = (
            (c.SOURCE_FRESHNESS, lambda: evaluate_freshness(asset, current)),
            (c.SOURCE_VOLUME, lambda: evaluate_volume(asset, current, previous)),
        )
        for fonte, avaliar in avaliacoes:
            try:
                events.append(avaliar())
            except Exception as exc:
                logger.error(
                    "falha ao avaliar %s de %s: %s — ativo observado, sem "
                    "evento desta fonte nesta execucao",
                    fonte, asset.full_name, _resumo_erro(exc),
                )

    return current, events, commits, None


def process(
    spark: SparkSession,
    catalog: str,
    schema: str,
    now_utc: datetime,
    destinations: Destinations,
    overrides: dict[str, dict] | None = None,
) -> tuple[list[Observation], list[Event], list[CommitMetric], Cobertura]:
    """Coleta todos os ativos de um schema e avalia os que tem expectativa.

    Uma tabela que falhe no DESCRIBE DETAIL — removida, renomeada, sem
    permissao — e registrada e o job segue para as demais. Uma tabela
    problematica nao pode impedir a observacao de todas as outras.

    Duas otimizacoes sobre a versao original, puramente sequencial:
    watermark de commits e observacao anterior saem de "uma consulta por
    ativo" para "uma consulta por schema" (ver read_commit_watermarks() e
    read_previous_observations()); e o que resta por ativo (DESCRIBE
    HISTORY, DESCRIBE DETAIL, COUNT(*) — esses tres sao por tabela de
    verdade, o Unity Catalog nao tem equivalente em lote) roda em paralelo
    num pool de threads, nao mais um ativo de cada vez.
    """
    assets = read_assets(spark, catalog, schema, overrides, destinations.all)
    if not assets:
        return [], [], [], Cobertura()

    session_tz = session_timezone(spark)
    nomes = [asset.full_name for asset in assets]
    watermarks = read_commit_watermarks(spark, nomes, destinations)
    previous_by_asset = read_previous_observations(
        spark, nomes, destinations, session_tz
    )

    observations: list[Observation] = []
    events: list[Event] = []
    commits: list[CommitMetric] = []
    orfaos: list[str] = []
    outras_falhas: list[str] = []

    with ThreadPoolExecutor(max_workers=c.ASSET_COLLECTION_WORKERS) as executor:
        futuros = [
            executor.submit(
                _collect_and_evaluate,
                spark,
                asset,
                watermarks.get(asset.full_name, -1),
                previous_by_asset.get(asset.full_name),
                now_utc,
                session_tz,
            )
            for asset in assets
        ]
        # Ordem de submissao, nao de conclusao: mantem a saida determinista
        # (mesma ordem de read_assets), sem abrir mao do paralelismo — o
        # trabalho de todos os futuros ja esta rodando concorrentemente
        # antes deste loop so drenar os resultados um a um.
        for asset, futuro in zip(assets, futuros):
            observation, asset_events, asset_commits, falha = futuro.result()
            if observation is not None:
                observations.append(observation)
            if falha == FALHA_PATH_AUSENTE:
                orfaos.append(asset.full_name)
            elif falha == FALHA_OUTRA:
                outras_falhas.append(asset.full_name)
            events.extend(asset_events)
            commits.extend(asset_commits)

    # `observados` conta leitura de verdade, nao linha gravada: as linhas de
    # tentativa (updated_at nulo) tambem entram em `observations` para irem
    # ao Delta, e somar len(observations) faria a cobertura parecer cheia
    # justamente quando ela nao esta.
    cobertura = Cobertura(
        coletados=len(assets),
        com_expectativa=sum(a.freshness_sla_minutes is not None for a in assets),
        observados=len(observations) - len(orfaos) - len(outras_falhas),
        orfaos=tuple(orfaos),
        outras_falhas=tuple(outras_falhas),
    )
    _avisar_cobertura_do_schema(catalog, schema, cobertura)
    return observations, events, commits, cobertura


def _avisar_cobertura_do_schema(
    catalog: str, schema: str, cobertura: Cobertura
) -> None:
    """Um aviso por schema quando algum ativo nao rendeu observacao.

    No caminho sem perda nao diz nada: o INFO de read_assets ja contou os
    ativos, e um "0 perdidos" por schema em 40+ schemas afoga o log onde a
    perda e real.

    O aviso continua existindo mesmo com a linha de tentativa gravada: a
    linha serve a quem consulta a tabela depois, o aviso serve a quem le o
    log da execucao. "93 observacoes gravadas" sozinho esconderia que 16
    delas sao tentativas.
    """
    if not cobertura.sem_observacao:
        return

    partes = []
    if cobertura.orfaos:
        partes.append(
            f"{len(cobertura.orfaos)} com caminho de armazenamento ausente "
            f"(tabela externa orfa): {', '.join(sorted(cobertura.orfaos))}"
        )
    if cobertura.outras_falhas:
        partes.append(
            f"{len(cobertura.outras_falhas)} por outras falhas: "
            f"{', '.join(sorted(cobertura.outras_falhas))}"
        )
    logger.warning(
        "%s.%s: %d de %d ativos nao foram lidos nesta execucao (gravados com "
        "metricas zeradas e updated_at nulo) — %s",
        catalog, schema, cobertura.sem_observacao, cobertura.coletados,
        "; ".join(partes),
    )


def detect_environment(spark: SparkSession, requested: str | None) -> str:
    """Ambiente da execucao: "dev" ou "prd".

    Ordem de precedencia: --environment (o bundle passa o target), depois o
    org ID do workspace. Sem nenhum dos dois, levanta erro — assumir um
    ambiente por omissao faria um job de dev varrer producao, ou o inverso.
    """
    if requested:
        ambiente = c.ENVIRONMENT_BY_BUNDLE_TARGET.get(requested, requested)
        if ambiente not in c.CATALOG_PREFIX_BY_ENVIRONMENT:
            raise ValueError(
                f"ambiente '{requested}' desconhecido; use um de "
                f"{sorted(c.CATALOG_PREFIX_BY_ENVIRONMENT)} ou um target do bundle "
                f"{sorted(c.ENVIRONMENT_BY_BUNDLE_TARGET)}"
            )
        return ambiente

    org_id = spark.conf.get(
        "spark.databricks.clusterUsageTags.clusterOwnerOrgId", None
    )
    ambiente = c.ENVIRONMENT_BY_WORKSPACE_ORG_ID.get(org_id or "")
    if ambiente is None:
        raise ValueError(
            f"nao foi possivel detectar o ambiente (org id '{org_id}'); "
            "informe --environment"
        )
    return ambiente


def list_catalogs(spark: SparkSession, environment: str) -> list[str]:
    """Catalogos do ambiente: os que comecam com dev_ ou prd_."""
    prefixo = c.CATALOG_PREFIX_BY_ENVIRONMENT[environment]
    return sorted(
        nome
        for nome in (row[0] for row in spark.sql("SHOW CATALOGS").collect())
        if nome.startswith(prefixo)
    )


def list_schemas(spark: SparkSession, catalog: str) -> list[str]:
    """Schemas de um catalogo, sem os de sistema."""
    return sorted(
        nome
        for nome in (
            row[0] for row in spark.sql(f"SHOW SCHEMAS IN {catalog}").collect()
        )
        if nome not in c.EXCLUDED_SCHEMAS
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logger.info("asset_observability versao %s iniciando", ASSET_OBSERVABILITY_VERSION)
    # basicConfig fixa o nivel do logger ROOT, do qual o py4j herda — sem
    # isto, cada chamada spark.sql() grava uma linha INFO do canal RPC
    # interno ("Received command c on object id p0"), que nao diz nada
    # sobre o job e multiplica o volume de log por ativo observado.
    logging.getLogger("py4j").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment",
        help="dev, prd ou o target do bundle (dev, prod); "
        "sem ele, detecta pelo workspace",
    )
    parser.add_argument(
        "--catalog",
        action="append",
        help="restringe a varredura a estes catalogos (repetivel); "
        "sem ele, varre todos do ambiente",
    )
    parser.add_argument(
        "--schema",
        action="append",
        help="restringe a varredura a estes schemas (repetivel); "
        "sem ele, varre todos de cada catalogo",
    )
    parser.add_argument(
        "--monitored-assets-path",
        default=c.DEFAULT_MONITORED_ASSETS_PATH,
        help="YAML de ativos monitorados; tem precedencia sobre as tags",
    )
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()
    now_utc = datetime.now(timezone.utc)
    overrides = read_asset_overrides(args.monitored_assets_path)

    environment = detect_environment(spark, args.environment)
    destinations = Destinations.for_environment(environment)
    ensure_destination_tables(spark, destinations)
    catalogs = list_catalogs(spark, environment)
    if args.catalog:
        catalogs = [nome for nome in catalogs if nome in args.catalog]
    logger.info("ambiente %s: %d catalogos: %s", environment, len(catalogs), catalogs)

    # Grava schema por schema: o que ja foi coletado nunca se perde por
    # causa de uma falha adiante. Um schema ou catalogo com erro (sem
    # permissao, removido) e registrado e o job segue; no fim ele termina
    # com erro para nao esconder cobertura perdida.
    falhas: list[str] = []
    cobertura = Cobertura()
    for catalog in catalogs:
        try:
            schemas = args.schema or list_schemas(spark, catalog)
        except Exception as exc:
            logger.error("falha ao listar schemas do catalogo %s: %s", catalog, _resumo_erro(exc))
            falhas.append(catalog)
            continue

        for schema in schemas:
            try:
                observations, events, commits, do_schema = process(
                    spark, catalog, schema, now_utc, destinations, overrides
                )
                cobertura = cobertura + do_schema
                write_observations(spark, observations, destinations)
                write_events(spark, events, now_utc, destinations)
                write_commit_metrics(spark, commits, now_utc, destinations)
            except Exception as exc:
                logger.error("falha ao observar %s.%s: %s", catalog, schema, _resumo_erro(exc))
                falhas.append(f"{catalog}.{schema}")

    try:
        purge_commit_metrics(spark, destinations)
    except Exception as exc:
        logger.error("falha na limpeza do log de commits: %s", _resumo_erro(exc))
        falhas.append(f"limpeza de {destinations.commit_metrics}")

    _resumir_execucao(cobertura)

    if falhas:
        raise RuntimeError(f"falhas em: {', '.join(falhas)}")


def _resumir_execucao(cobertura: Cobertura) -> None:
    """Uma linha no fim do log com o que a execucao entregou de verdade.

    Quem opera a esteira le o fim do log, nao os 40+ blocos por schema. Sem
    este resumo, dois problemas graves passavam batido na execucao de
    2026-10-07: 18 ativos perdidos por tabela orfa (que nao deixam o job
    vermelho) e ~2.500 ativos coletados com NENHUMA expectativa avaliavel —
    o job inteiro rodando sem avaliar nada, porque a tag de frequencia nao
    esta aplicada.
    """
    logger.info(
        "resumo da execucao: %d ativos coletados, %d lidos com sucesso, "
        "%d com expectativa avaliavel, %d nao lidos e registrados como "
        "tentativa (%d orfaos, %d outras falhas)",
        cobertura.coletados, cobertura.observados, cobertura.com_expectativa,
        cobertura.sem_observacao, len(cobertura.orfaos),
        len(cobertura.outras_falhas),
    )

    if cobertura.coletados and not cobertura.com_expectativa:
        logger.warning(
            "nenhum dos %d ativos coletados tem expectativa avaliavel: sem a "
            "tag %s aplicada (ou herdada do schema) o job coleta historico "
            "mas NAO emite nenhum evento de freshness ou volume, e nada "
            "chega ao hub. Cobertura de alerta e zero.",
            cobertura.coletados, c.TAG_REFRESH_FREQUENCY,
        )


if __name__ == "__main__":
    main()
