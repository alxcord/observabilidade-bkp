# 02 — Resolução de eventos (`event_resolution.py`)

Biblioteca compartilhada, não é processo. Código em [src/shared/event_resolution.py](../src/shared/event_resolution.py), testes em [src/shared/tests/](../src/shared/tests/). Consumida pelo [coletor](10-observability-collector.md) e pelo [hub-publisher](12-hub-publisher.md); é **copiada para dentro de cada imagem no build** do Docker (contexto de build = raiz do repositório), nunca duplicada à mão. A Azure Function da API vive em outro repositório e não a consome.

É o único ponto que transforma uma observação em mudança de estado de incidente: recebe "observei `PASS`/`FAIL` para esta origem e chave" e decide, em uma transação, se abre, incrementa, fecha ou ignora. O chamador nunca informa o estado.

## Interface

`resolve_event(conn, *, source, result, natural_key, key_rule='JOB_ID', correlation_ref=None, scope=None, workspace_id=None, severity='MEDIUM', error_code=None, error_description=None, run_id=None, source_event_id=None, observed_at=None) -> (problem_id | None, action)`

| Parâmetro | Significado |
|---|---|
| `source` | Origem ativa de `tbl_ref_source`; desconhecida → `ValueError` |
| `result` | `FAIL` ou `PASS`; outro valor → `ValueError` |
| `natural_key` | Identidade estável da origem do sinal (tabela abaixo) |
| `correlation_ref` | Identificador da execução observada (hoje, o `ActivityRunId` do ADF). **Não** decide correlação nem estado: é gravado no evento filho, para ligar depois o evento do ADF e o do run Databricks da mesma execução |
| `scope` / `workspace_id` | Busca do domínio em `tbl_cfg_domain_scope` |
| `severity` | `HIGH`, `MEDIUM` ou `LOW`; precisa existir rota |
| `source_event_id` | ID determinístico do produtor, para idempotência (obrigatório na prática para quem reenvia) |
| `observed_at` | Vira `opened_at`/`closed_at`/`registered_at`; padrão agora (UTC) |

Retorno `action`: `ABERTO`, `INCREMENTADO`, `RESOLVIDO`, `IGNORADO` ou `DUPLICADO`.

## Processamento

1. Valida `result` e `source`.
2. Idempotência: `source_event_id` já existente para a origem → `DUPLICADO`, sem alterar nada.
3. Correlação (minúscula): `adf:<natural_key>` se `source = 'ADF'`; senão `<source>:<natural_key>`. Sempre derivada da `natural_key`, que é estável entre execuções do mesmo processo — é essa estabilidade que permite ao `PASS` seguinte fechar o incidente.
4. Domínio: primeira linha ativa de `tbl_cfg_domain_scope` com mesma origem e `scope` (ou `workspace_id`); sem correspondência, `DEFAULT`.
5. Lê o incidente aberto da correlação com `UPDLOCK, HOLDLOCK` (serializa concorrentes).
6. Aplica a decisão e faz `commit`.
7. Se o `INSERT` violar `ux_problems_open` (2601/2627), outro processo abriu antes: `rollback`, relê o vencedor e incrementa.

| Observação | `OPEN` existente? | Efeito |
|---|---|---|
| `FAIL` | Não | Insere incidente (`OPEN`, `occurrence_count = 1`), evento filho e outbox `OPEN` → `ABERTO` |
| `FAIL` | Sim | +1 em `occurrence_count`; severidade sobe para `HIGH` se a nova for `HIGH` (nunca desce); atualiza erro; evento filho; **não notifica de novo** → `INCREMENTADO` |
| `PASS` | Sim | `RESOLVED`, grava `closed_at`/`closing_run_id`, outbox `CLOSE`, evento com `error_code = 'RECUPERADO'` → `RESOLVIDO` |
| `PASS` | Não | Nada → `IGNORADO` (não consome o `source_event_id`) |

A transação é do Python, não de T-SQL. A notificação nunca é enviada aqui: só a intenção vai para a outbox, na mesma transação.

## Chaves por origem (contrato com os produtores)

| Origem | `natural_key` | Correlação |
|---|---|---|
| Databricks `SUBMIT_RUN` disparado pelo ADF | `run_name` sem o UUID final | `databricks:<run_name sem UUID>` |
| Databricks `SUBMIT_RUN` sem UUID | `run_name` inteiro | `databricks:<run_name>` |
| Databricks `JOB_RUN` | `<workspace_id>:<job_id>` | `databricks:<workspace_id>:<job_id>` |
| ADF, atividade | `<factory>/<pipeline>/<atividade>` | `adf:<factory>/<pipeline>/<atividade>` |
| ADF, pipeline | `<factory>/<pipeline>/pipeline` | `adf:<factory>/<pipeline>/pipeline` |
| ADF, trigger | `<factory>/trigger/<nome>` | `adf:<factory>/trigger/<nome>` |
| ADF, nós do Self-Hosted IR | `<factory>/ir/<nome>` | `adf:<factory>/ir/<nome>` |
| Ausência / job travado | `natural_key` da expectativa | `<source>:<natural_key>` |
| Data Quality | `<rule_id>@<ativo>` | `data_quality:<rule_id>@<ativo>` |
| Freshness, Volume, Schema | nome completo do ativo | `<source>:<ativo>` |

Toda correlação é estável por processo, e nenhuma carrega identificador de execução. Consequência aceita: a atividade do ADF e o run Databricks que ela disparou produzem **dois** incidentes, e não um. Os dois fecham sozinhos, e o que liga um ao outro é o `correlation_ref` (o `ActivityRunId`) gravado nos eventos filhos.

## Critérios de aceite

Suíte de `src/shared/tests/` passa. Falta, contra Azure SQL real, o teste de corrida: duas sessões com `FAIL` na mesma correlação ao mesmo tempo → um incidente com `occurrence_count = 2`, uma outbox `OPEN`, nenhum erro.

## Histórico: o fechamento automático de incidentes de ADF

Até 2026-10, um evento de ADF (e o run Databricks que ele disparava) era correlacionado pelo `ActivityRunId` da execução, para que os dois lados caíssem num incidente só. O efeito colateral era grave: o ADF gera um `ActivityRunId` novo a cada execução, então o `PASS` seguinte caía em outra correlação, era `IGNORADO`, e **todo** incidente originado no ADF ficava aberto até alguém fechar à mão.

A correção trocou a correlação pela chave estável e passou o identificador da execução para `correlation_ref`. O preço é o alerta em dois níveis descrito acima; a troca se justifica porque um incidente que não fecha sozinho envenena o painel inteiro, enquanto um alerta a mais custa uma mensagem.

## Pendências conhecidas

| Gravidade | Descrição | Direção |
|---|---|---|
| Alta | Domínio só vem de `tbl_cfg_domain_scope`; coletor calcula o domínio e descarta, hub-publisher não passa `scope` → quase tudo cai na rota `DEFAULT` | Aceitar `domain` opcional como fallback e passá-lo dos produtores |
| Média | Severidade fora de `HIGH/MEDIUM/LOW` (ex.: `CRITICAL`) não acha rota e a notificação fica pendente para sempre | Normalizar aqui ou cadastrar rotas |
| Baixa | `RECUPERADO` e as ações em português contra a convenção em inglês | Padronizar junto com a migração de enums |
