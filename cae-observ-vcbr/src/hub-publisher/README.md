# hub-publisher

Container Apps Job que publica no hub (Azure SQL) os eventos que os jobs de
avaliação do Databricks (`asset_observability`, `data_quality`,
`custom_lineage_reconciler`) deixaram em
`tbl_dbr_it_observability_events`, de hora em hora. Sem ele, esses eventos
nunca viram incidente: ficam parados em staging para sempre.

## Por que roda aqui, não como job Databricks (e não mais atrás de uma API)

Até 2026-09, este job rodava dentro do Databricks
(`src/databricks/hub_publisher/`) e enviava cada evento por `POST
/api/events` para uma API (Azure Functions), que então chamava
`event_resolution.resolve_event()`. A API existia porque um workspace
**sem VNet injection** roda o compute do Databricks numa VNet gerenciada
pela Microsoft, sem rota até o Private Endpoint do Azure SQL — só uma
chamada HTTPS de saída (para um endpoint público) atravessava essa
fronteira.

Faz o inverso do `hub-replication`, e usa a mesma assimetria: as APIs REST
do Databricks (aqui, a **Statement Execution API**) são HTTPS públicas,
alcançáveis de qualquer lugar, porque quem inicia a conexão é sempre o
lado de fora — nunca o compute do Databricks. Rodando em Container Apps em
vez de Databricks, este job:

1. lê `tbl_dbr_it_observability_events` pela Statement Execution API
   (`SELECT ... WHERE sent_at IS NULL`);
2. chama `event_resolution.resolve_event()` direto contra o Azure SQL por
   `pyodbc` + Managed Identity — mesmo padrão do coletor, do
   sync-expectations e do hub-replication;
3. marca `sent_at` (mesma Statement Execution API) só nos eventos que
   `resolve_event()` confirmou.

Isso elimina a necessidade de uma API HTTP no meio para atravessar a rede
— a rota `POST /api/events` foi removida da Azure Function (repositório próprio). A API
continua existindo para leitura (`GET /problems`) e CRUD de configuração
(`/config/*`); só a ingestão de eventos saiu de lá.

## Cadência e ordem com hub-replication

Roda de hora em hora, na virada da hora (`0 * * * *`) — antes de
`hub-replication` (`15 * * * *`), de propósito: um incidente aberto no
início de um ciclo já está confirmado no hub a tempo de a replicação do
mesmo ciclo exportá-lo para Delta. Os dois cronExpression estão em
`infra/params.json` de cada projeto, não aqui — mudar a cadência de um dos
dois (ou dos dois) é editar o cron ali, sem precisar mexer em código.

## Risco aberto — schema do TIMESTAMP na resposta da Statement Execution API

`_parse_observed_at()` assume que a API devolve TIMESTAMP como texto
`"YYYY-MM-DD HH:MM:SS[.ffffff]"`, sem fuso explícito, e trata como UTC.
**Não validado contra uma resposta real** — mesma lacuna que o README
principal da Observabilidade já registra para o `run_name` da Jobs API. Resolve-se
testando contra o warehouse real antes de operar em produção; se o formato
vier diferente, só `_parse_observed_at()` precisa mudar.

## `domain` lido mas não usado

`EVENT_COLUMNS` inclui `domain`, mas ele nunca é repassado a
`resolve_event()` — quem decide o domínio do incidente é a própria função,
a partir de `tbl_cfg_domain_scope`. Isto reproduz fielmente o comportamento
da API que este job substitui (`repository.resolve_event` também nunca
mapeava `domain` do corpo da requisição): não foi corrigido de propósito,
para não mudar resolução de domínio sem decisão explícita. Ver comentário
em `constants.py`.

## Idempotência

O envio é at-least-once: `resolve_event()` já deduplica por
`source_event_id` (ver docstring de `src/shared/event_resolution.py`), e um
evento cujo `sent_at` não foi confirmado por falha no meio do lote volta a
ser lido no próximo ciclo — reenviá-lo não produz efeito novo.

## Testes

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Usa stub de `pyodbc`, `httpx` e `azure-identity`, e mocka
`event_resolution.resolve_event` — sua lógica é testada em
`src/shared/tests/`.
