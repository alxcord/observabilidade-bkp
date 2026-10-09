# Log de execução de pipeline

Módulo da solução de observabilidade que registra, em formato append-only, cada
operação de carga executada nas camadas **raw**, **cleansed** e **curated**. É a
única fonte de verdade do schema do log: nenhum pipeline deve emitir
`CREATE`/`ALTER` sobre a tabela por conta própria.

O módulo substitui gradualmente o import legado
`modules.vc_pipeline_observability_coe`. Os consumidores devem migrar para o
wheel `vc-observability` antes que a implementação antiga seja removida.

**Destino:** `{env}_it.corp_raw.tbl_dbr_it_log_execucao_pipeline`

## Por que um módulo apartado

Uma tabela escrita por três pipelines precisa de **um dono**. Antes, o logger
vivia dentro do pacote de ingestão SAP→RAW, o que obrigaria a Cleansed a
importar um pacote de extração SAP só para logar — ou a duplicar o logger,
criando duas versões do schema que divergem no primeiro `ALTER TABLE`.

O módulo é deliberadamente pequeno: append-only, sem leitura, sem transformação.
Falha de escrita do log **nunca** propaga — observabilidade não pode derrubar a
carga que observa.

## Uso

O caminho recomendado é injetar o logger como **sink** do pipeline:

```python
from vc_observability.pipeline_logging import (
    EventLogger,
    probe_audit_sink,
    audit_status,
)
from modules import vc_dbr_shared_functions as sf

catalogo_it = sf.get_catalog_name("it")
probe_audit_sink(spark, catalogo_it)  # no INÍCIO do run

audit = EventLogger(
    spark,
    catalog=catalogo_it,
    layer="raw",
    environment=sf.get_environment(),
    dbutils=dbutils,
    orchestrator_run_id=request.orchestrator_run_id,
)

relatorio = RawPipeline(spark, dbutils, settings, sink=audit).run(VIEWS, request)
payload["audit"] = audit_status()
```

O pipeline chama `audit.emit(dict)` a cada desfecho; o logger acumula e grava em
blocos. Quem fecha o buffer é o `finally` do `run()` — inclusive quando o run
morre no meio, que é justamente quando os eventos anteriores importam.

Caso avulso, um evento só:

```python
from vc_observability.pipeline_logging import PipelineEvent, build_execution_id

audit.register(
    PipelineEvent(
        execution_batch_id=batch_id,
        execution_id=build_execution_id(full_table),
        status="PUBLISHED",
        layer="raw",
        target_table=full_table,
        load_strategy="FULL",
        rows_inserted=21_591_538,
        detail="RECREATED — FULL overwrite",
    )
)
```

### Nunca chame `register()` num laço

Cada chamada é **um commit Delta**. Um run de 95 tabelas emite 190 eventos
(STARTED + terminal), ou seja 190 transações e 190 arquivos minúsculos por
execução; diariamente, ~69 mil commits por ano numa tabela que ninguém otimiza.

| Forma | Commits para 190 eventos |
|---|---|
| `emit()` + `close()` (buffer 50, default) | **4** |
| `register_many(eventos)` | **1** |
| `register()` num laço | **190** |

## Localização física e propriedade da tabela

| Aspecto | Valor | Consequência |
|---|---|---|
| Catálogo | `{env}_it` | Log centralizado: consulta cross-área sem `UNION` |
| Schema | `corp_raw` | Registro operacional bruto, append-only, sem transformação de qualidade |
| Tipo | **EXTERNAL** sempre | Obrigatório no VCBR: `{env}_it` não tem managed location |
| Nome | `tbl_dbr_it_log_execucao_pipeline` | Fórmula 2.1: `tbl` (tabela física) · `dbr` (produzida pelo Databricks) · `it` (domínio TI) |
| Clustering | `(dt_execution, layer, target_table)` | As três dimensões de filtro reais |

### External, não gerenciada — e por quê

Comprovado em 2026-08-17 via `DESCRIBE SCHEMA EXTENDED` (Location vazio em
`{env}_it` e em `{env}_it.corp_raw`): sem managed location, o Unity Catalog não
tem onde materializar tabela gerenciada e **todo write falha** com
`INVALID_STATE.UC_CLOUD_STORAGE_ACCESS_FAILURE`.

O caminho é resolvido **dentro deste módulo**, por
`ddl.resolve_default_audit_location(environment)`. Antes ele era
responsabilidade do notebook chamador, o que espalhava hardcode em cada ponto de
composição — e a conta de storage muda por ambiente (`sadatalakedevvcbr` em DEV,
`sadatalakevcbr` em PRD). Passe `location=` ao `EventLogger` apenas para
experimento.

Nunca usar o segmento literal `vol` no path: o UC bloqueia `PATH_CREATE_TABLE`
achando que é Volume UC.

## Vocabulário de status

`status` é **genérico às três camadas** e é para continuar pequeno: cada valor
novo obriga toda consulta cross-camada a conhecê-lo.

```
STARTED · PUBLISHED · NOOP · RETRY · FAILED · EMPTY_SOURCE · SKIPPED · OPTIMIZED
```

Refinamento específico de uma camada vai em **`status_detail`**, sem entrar em
`VALID_STATUSES`. A ingestão RAW, por exemplo, mapeia assim:

| Status da camada | `status` | `status_detail` |
|---|---|---|
| `FIRST_LOAD` | `PUBLISHED` | `FIRST_LOAD` |
| `SKIPPED_PREFLIGHT_NOOP` | `SKIPPED` | `SKIPPED_PREFLIGHT_NOOP` |
| `SKIPPED_EMPTY_WINDOW` | `SKIPPED` | `SKIPPED_EMPTY_WINDOW` |
| `REJECTED` | `FAILED` | `REJECTED` |

Quem consulta as três camadas filtra por `status`; quem investiga uma camada
desce para `status_detail`.

## Janela

Quatro colunas, duas granularidades:

| Coluna | Tipo | Para quê |
|---|---|---|
| `window_start` / `window_end` | DATE | Filtro por dia; existem desde a primeira versão |
| `window_start_at` / `window_end_at` | TIMESTAMP | Instante exato do corte |

Com janela ancorada em control point, o corte tem hora e a data sozinha não
explica o volume lido. Informar só as colunas `*_at` basta — as DATE são
derivadas em `as_row`.

## Evolução de schema

Migração é **sempre aditiva**: coluna nova entra com NULL nos eventos antigos.
Nunca remover nem alterar tipo — o histórico do log é evidência, e reescrita
destrutiva apaga justamente o que se queria auditar.

**Coluna nova entra no FIM** das três listas (`ddl.LOG_COLUMNS`,
`ddl.LOG_SPARK_SCHEMA`, `PipelineEvent.as_row`). `ALTER TABLE ADD COLUMNS`
acrescenta ao final do schema das tabelas existentes; inserir no meio faz o
CREATE divergir do que já está em produção.

`ensure_log_table` lê as colunas atuais e emite ALTER só para o que falta. A
versão anterior disparava um ALTER por coluna do schema em todo run — 33
statements, todos falhando, um round-trip de metastore cada.

## Falha do sink

`probe_audit_sink` escreve e apaga uma linha sentinela no início do run. Um
`DESCRIBE` responde pelo metastore e nunca toca o storage: não detecta falha de
credencial nem de managed location.

Se a sonda falhar (ou se uma escrita posterior falhar), as flags de módulo
desligam a auditoria e todo `emit`/`register` seguinte vira no-op contado. O run
segue. `audit_status()` devolve o que aconteceu:

```python
{
    "audit_enabled": False,
    "audit_events_suppressed": 60,
    "audit_disable_reason": "UC_CLOUD_STORAGE_ACCESS_FAILURE",
}
```

**Coloque isso no payload da orquestração.** Sem esse campo, um run inteiro sem
eventos gravados sai indistinguível de um run normal.

O echo no console acontece ANTES do short-circuit, de propósito: quando o sink
está fora, o console é a única observabilidade que resta.

## Divergência conhecida

As colunas estão em inglês, não em português com prefixo semântico
(`dh_inicio`, `qt_linhas_inseridas`...). É tabela técnica de infraestrutura,
consumida por código e não por negócio, e o restante dos módulos é em inglês.
Pendente de decisão formal da governança — se a decisão for português, renomear
via `ALTER TABLE RENAME COLUMN` (barato em append-only).

## Módulos

| Arquivo | Responsabilidade |
|---|---|
| `contracts.py` | `PipelineEvent`, vocabulários e serialização posicional. Puro, sem Spark |
| `ddl.py` | Schema, DDL, comentários e migração aditiva. Única fonte de verdade |
| `logger.py` | `EventLogger`, sonda, buffer e log de console |
| `__init__.py` | API pública — importe só daqui |
