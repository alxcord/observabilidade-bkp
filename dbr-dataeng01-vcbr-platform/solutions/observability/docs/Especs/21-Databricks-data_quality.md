# 21 - Databricks - data_quality

| Item | Valor |
|---|---|
| Tipo de componente | Job Databricks (`spark_python_task`), ou tarefa dentro de um pipeline de negócio |
| Repositório | `dbr-dataeng01-vcbr-platform` (este repositório) |
| Arquivo desta spec | `solutions/observability/docs/Especs/21-Databricks-data_quality.md` |
| Código | `solutions/observability/src/vc_observability/data_quality/data_quality.py`, `constants.py` |
| Cadência | Sem agenda no bundle. O ADF dispara uma execução por domínio, no horário de cada domínio ou ao fim do pipeline que carrega os ativos |
| Parâmetros | `--regras <arquivo YAML>` (obrigatório, repetível). No job, o parâmetro `domain` monta o caminho do arquivo |
| Especificações relacionadas | [12](12-Job-hub-publisher.md), [40](40-Configuracao-YAML.md) |

> **Catálogo por ambiente.** O catálogo de governança onde ficam as tabelas e o volume Delta da solução depende do ambiente: `prd_governance` em produção e `dev_governance` em desenvolvimento. Neste documento ele aparece como `<gov>` (por exemplo, `<gov>.corp_cleansed`); troque pelo nome do ambiente ao criar objetos e conceder permissões.

## 1. Objetivo funcional

Executar as regras de qualidade declaradas em YAML contra as tabelas, guardar o resultado de todas as regras em histórico e publicar na staging apenas as regras **bloqueantes**, cujo `FAIL` abre incidente e cujo `PASS` o fecha. Separa claramente "a regra não conseguiu rodar" (`evaluation_status = ERROR`) de "o dado está ruim" (`quality_outcome = FAIL`).

## 2. Entradas

Um ou mais YAML de regras e as tabelas citadas nas regras, consultadas por Spark SQL. Cada domínio tem suas regras em `solutions/observability/config/<domínio>/quality-rules.yaml` (especificação 40), e cada execução recebe o arquivo do seu domínio.

## 3. Saídas (modo append)

| Tabela | Conteúdo |
|---|---|
| `<gov>.corp_cleansed.tbl_dbr_it_data_quality_results` | Uma linha por regra por execução: `result_id STRING` (SHA-256 de ativo, regra, versão e instante), `rule_id`, `rule_version`, `asset`, `rule_type`, `severity`, `blocking BOOLEAN`, `evaluation_status`, `quality_outcome`, `evaluated_row_count BIGINT`, `invalid_row_count BIGINT`, `invalid_pct DOUBLE`, `error STRING`, `executed_at TIMESTAMP` |
| `<gov>.corp_cleansed.tbl_dbr_it_observability_events` | Uma linha por regra **bloqueante**: `source = 'DATA_QUALITY'`, `natural_key = '<rule_id>@<ativo>'`, `result` `PASS` ou `FAIL`, `source_event_id = result_id` |

## 4. Fluxo de processamento

1. Para cada arquivo de regras, carrega as regras. Tipo desconhecido interrompe a carga do arquivo (regra que não roda não pode passar despercebida).
2. Para cada regra, monta e executa uma consulta que devolve `avaliadas` e `invalidas` (seção 5.1). Exceção na consulta vira resultado `ERROR`/`UNKNOWN` com a mensagem truncada em 500 caracteres.
3. Classifica: zero inválidas ou percentual inválido até `threshold_pct` → `PASS`; acima do limite → `FAIL` se `blocking`, `WARN` se não.
4. Grava todos os resultados e, depois, os eventos das regras bloqueantes. Para uma regra bloqueante com `ERROR`, o evento sai como `FAIL` com `error_code = 'RULE_NOT_EVALUATED'`; com `FAIL` de dado, `error_code = 'RULE_FAILED'` e descrição "N de M linhas inválidas (x%), acima do limite de y%".

## 5. Regras de negócio

### 5.1 Tipos de regra e SQL gerado

| Tipo | Campos | Linha inválida |
|---|---|---|
| `not_null` | `columns` (uma ou mais) | Qualquer coluna listada nula |
| `unique` | `columns` (chave) | Cada repetição além da primeira (contagem por agrupamento) |
| `domain` | `columns[0]`, `parameters.values` | Valor não nulo fora da lista |
| `range` | `columns[0]`, `parameters.min` e/ou `max` | Valor abaixo do mínimo ou acima do máximo |
| `reference` | `columns[0]`, `parameters.reference_table`, `reference_column` | Valor não nulo sem correspondência na tabela de referência (`LEFT JOIN`) |
| `expression` | `parameters.invalid_condition` | Linhas em que a expressão SQL é verdadeira |

### 5.2 Campos de regra

`id` (estável, nunca reaproveitado), `version` (sobe a cada mudança de critério; entra no `result_id`), `type`, `asset` (nome completo), `description`, `columns`, `parameters`, `severity` (padrão `MEDIUM`; usar só `HIGH`, `MEDIUM` ou `LOW`), `domain` (padrão `DEFAULT`), `blocking` (padrão `false`), `threshold_pct` (padrão 0).

### 5.3 Segurança do SQL

As regras são concatenadas em SQL. O YAML é configuração revisada por pull request e precisa ser tratado como código: quem aprova regra está aprovando SQL executado com a identidade do job.

## 6. Permissões e implantação

A identidade do job precisa de `SELECT` em todas as tabelas citadas nas regras (inclusive as de referência) e de `MODIFY`/`SELECT` nas duas tabelas de saída. Um único job `data_quality` em `resources/jobs.yml`, sem agenda, com o parâmetro de job `domain`. A tarefa passa `["--regras", "${workspace.file_path}/config/{{job.parameters.domain}}/quality-rules.yaml"]`. O ADF dispara o job uma vez por domínio, cada execução no seu horário e com o seu `domain`, para que o atraso ou a falha de um domínio não segure os outros. Por isso `max_concurrent_runs` precisa ser maior que 1. Uma execução sem `domain`, ou com um domínio sem pasta, falha ao abrir o arquivo; o erro aparece no run, nada é avaliado em silêncio. Cluster e biblioteca seguem o job da especificação 20. O YAML chega ao workspace pelo deploy do bundle, junto com o código; não há Git Folder de configuração.

Como o job não tem agenda no Databricks, o `sync-expectations` não cria expectativa para ele (especificação 11). A detecção de "não rodou" vem da agenda do pipeline ADF que o dispara ou, se o pipeline for disparado pelo Control-M, de uma expectativa `MANUAL` cadastrada pela `observability-api` (especificação 11, seção 5.3).

## 7. Critérios de aceite

Com a tabela de exemplo de `logistics.yaml`, cada regra gera uma linha no histórico; uma duplicidade em `id_entrega` gera `FAIL` e um evento `FAIL` na staging; corrigida, a execução seguinte gera `PASS` e fecha o incidente; uma regra sobre tabela inexistente gera `ERROR`/`UNKNOWN`.
