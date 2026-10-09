# 02 - Biblioteca Python - Resolução de eventos (event_resolution.py)

| Item | Valor |
|---|---|
| Tipo de componente | Biblioteca Python compartilhada (não é processo próprio) |
| Repositório | `cae-observ-vcbr` (criado, ainda vazio), onde é copiado no build de cada imagem |
| Arquivo desta spec | `solutions/observability/docs/Especs/02-Resolucao-de-Eventos.md` |
| Código | `src/shared/event_resolution.py`, testes em `src/shared/tests/` |
| Consumidores | observability-collector (10) e hub-publisher (12). Copiado para dentro de cada imagem no build do Docker; nunca duplicado à mão |
| Lê | `tbl_ref_source`, `tbl_technical_events`, `tbl_cfg_domain_scope`, `tbl_problems` |
| Grava | `tbl_problems`, `tbl_technical_events`, `tbl_notification_outbox` |
| Dependências | `pyodbc` e uma conexão já autenticada ao hub |

## 1. Objetivo funcional

É o único ponto da solução que transforma uma observação em mudança de estado de incidente. Recebe "observei este resultado (`PASS` ou `FAIL`) para esta origem e esta chave" e decide, dentro de uma transação, se abre um incidente, se soma uma ocorrência a um incidente aberto, se fecha um incidente ou se não faz nada. O chamador nunca informa o estado do incidente.

## 2. Interface

`resolve_event(conn, *, source, result, natural_key, key_rule='JOB_ID', adf_activity_run_id=None, scope=None, workspace_id=None, severity='MEDIUM', error_code=None, error_description=None, run_id=None, source_event_id=None, observed_at=None) -> (problem_id | None, action)`

| Parâmetro | Significado |
|---|---|
| `source` | Uma das origens ativas de `tbl_ref_source`. Origem desconhecida gera `ValueError` |
| `result` | `FAIL` ou `PASS`. Qualquer outro valor gera `ValueError` |
| `natural_key` | Identidade estável da origem do sinal (ver tabela da seção 3.2) |
| `key_rule` | Rótulo de qual regra gerou a chave; só informativo, gravado em `tbl_problems.key_rule` (até 24 caracteres) |
| `adf_activity_run_id` | Quando presente, a correlação passa a ser `adf:<id>`, o que junta o lado ADF e o lado Databricks no mesmo incidente |
| `scope` / `workspace_id` | Usados para buscar o domínio em `tbl_cfg_domain_scope`; `workspace_id` também é gravado no incidente |
| `severity` | `HIGH`, `MEDIUM` ou `LOW`. Entra na escolha do destino pelas regras de alerta (ver especificação 01, seção 2.6) |
| `source_event_id` | Identificador determinístico do produtor, para idempotência. Obrigatório na prática para quem pode reenviar (hub-publisher) |
| `observed_at` | Instante da observação; vira `opened_at`, `closed_at` e `registered_at`. Padrão: agora, em UTC |

`action` devolvido: `ABERTO`, `INCREMENTADO`, `RESOLVIDO`, `IGNORADO` ou `DUPLICADO`.

## 3. Processamento

### 3.1 Passos

1. Valida `result` e confirma que `source` existe e está ativa em `tbl_ref_source`.
2. Idempotência: se `source_event_id` foi informado e já existe em `tbl_technical_events` com a mesma origem, devolve `DUPLICADO` sem alterar nada.
3. Calcula a chave de correlação, sempre em minúsculas: `adf:<adf_activity_run_id>` se este veio; senão `adf:<natural_key>` se `source = 'ADF'`; senão `<source>:<natural_key>`.
4. Resolve o domínio: primeira linha ativa de `tbl_cfg_domain_scope` com a mesma origem e `scope` igual a `scope` (ou, na falta, `workspace_id`). Sem correspondência, `DEFAULT`.
5. Lê o incidente aberto da mesma correlação com `WITH (UPDLOCK, HOLDLOCK)`, o que serializa chamadas concorrentes.
6. Aplica a tabela de decisão (3.3) e faz `commit`.
7. Se o `INSERT` do incidente violar o índice único `ux_problems_open` (erros 2601/2627), outro processo abriu o mesmo incidente entre a leitura e a escrita: faz `rollback`, relê o vencedor e aplica o caminho de incremento.

### 3.2 Chaves por origem (contrato com os produtores)

| Origem | `natural_key` | Correlação resultante |
|---|---|---|
| Databricks `SUBMIT_RUN` disparado pelo ADF | `run_name` sem o UUID final, minúsculo | `adf:<activity_run_id>` |
| Databricks `SUBMIT_RUN` sem UUID | `run_name` inteiro, minúsculo | `databricks:<run_name>` |
| Databricks `JOB_RUN` | `<workspace_id>:<job_id>` | `databricks:<workspace_id>:<job_id>` |
| ADF (atividade observada no Log Analytics) | `<data_factory>/<pipeline>/<atividade>` | `adf:<activity_run_id>` |
| Ausência / job travado (expectativa) | a `natural_key` da expectativa | `<source>:<natural_key>` |
| Data Quality | `<rule_id>@<ativo>` | `data_quality:<rule_id>@<ativo>` |
| Freshness, Volume, Schema | nome completo do ativo | `<source>:<ativo>` |

### 3.3 Tabela de decisão

| Observação | Existe `OPEN` com a mesma correlação? | Efeito |
|---|---|---|
| `FAIL` | Não | Insere `tbl_problems` (`OPEN`, `occurrence_count = 1`), um evento filho e uma outbox `OPEN`. Ação `ABERTO` |
| `FAIL` | Sim | Soma 1 em `occurrence_count`, sobe a severidade para `HIGH` se a nova for `HIGH` (nunca desce), atualiza código e descrição de erro se vieram, insere evento filho. Não notifica de novo. Ação `INCREMENTADO` |
| `PASS` | Sim | Muda para `RESOLVED`, grava `closed_at` e `closing_run_id`, insere outbox `CLOSE` e evento filho com `error_code = 'RECUPERADO'`. Ação `RESOLVIDO` |
| `PASS` | Não | Nada. Ação `IGNORADO` |

## 4. Regras gerais

A transação é controlada pelo Python (`commit`/`rollback`), não por T-SQL. A notificação nunca é enviada aqui: só a intenção vai para a outbox, na mesma transação. Um `PASS` ignorado não deixa rastro, portanto não consome o `source_event_id`.

## 5. Critérios de aceite

Testes unitários: `python -m pytest -q src/shared/tests`. Em ambiente com Azure SQL real, duas sessões chamando `resolve_event` com `FAIL` para a mesma correlação ao mesmo tempo devem resultar em um incidente com `occurrence_count = 2`, um único `OPEN` na outbox e nenhum erro.
