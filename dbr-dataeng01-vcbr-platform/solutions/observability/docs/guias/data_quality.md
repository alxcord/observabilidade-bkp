# Guia de uso - data_quality

Este guia mostra como escrever regras de qualidade de dados, como o job as executa e onde o resultado e o alerta aparecem. As regras completas estão na [especificação 21](../Especs/21-Databricks-data_quality.md).

## 1. O que o job faz

O `data_quality` lê um arquivo de regras em YAML, executa cada regra como uma consulta SQL contra a tabela e conta quantas linhas foram avaliadas e quantas estão inválidas. Todo resultado vai para o histórico. Só as regras **bloqueantes** viram evento para o hub, onde um `FAIL` abre incidente e o `PASS` seguinte fecha.

O job separa duas situações que costumam se confundir:

| Situação | `evaluation_status` | `quality_outcome` |
|---|---|---|
| A regra rodou e o dado está bom | `SUCCESS` | `PASS` |
| A regra rodou e o dado está ruim | `SUCCESS` | `FAIL` (bloqueante) ou `WARN` (não bloqueante) |
| A regra não conseguiu rodar (tabela inexistente, coluna errada, SQL inválido) | `ERROR` | `UNKNOWN` |

## 2. Onde ficam as regras

Cada domínio tem a sua pasta em `solutions/observability/config/`, com um arquivo de regras ([especificação 40](../Especs/40-Configuracao-YAML.md)):

```
solutions/observability/config/
├── logistics/quality-rules.yaml
├── finance/quality-rules.yaml
└── shared/...
```

O nome da pasta é o domínio. Ele precisa ser o mesmo valor usado em `domain` nas regras e na tag `vc_data_domain`.

Uma regra nova ou alterada entra por pull request e só vale no ambiente depois que a esteira implantar o bundle. Quem aprova a regra está aprovando SQL que roda com a identidade do job: revise como código.

## 3. Como escrever uma regra

```yaml
rules:
  - id: entrega_id_unico          # estável; nunca reaproveite
    version: "1"                  # suba a cada mudança de critério
    type: unique                  # tipo da regra (seção 4)
    asset: prd_logistics.corp_curated.tbl_entrega
    columns: [entrega_id]
    description: Cada entrega aparece uma única vez.
    severity: HIGH                # HIGH, MEDIUM ou LOW
    domain: logistics             # declare em cada regra
    blocking: true                # true abre incidente
    threshold_pct: 0              # percentual de inválidas tolerado
```

| Campo | Obrigatório | Padrão | Para que serve |
|---|---|---|---|
| `id` | Sim | — | Identidade da regra. Nunca reaproveite um `id` para outra regra |
| `version` | Não | `"1"` | Suba quando o critério mudar: o histórico de uma versão não se mistura com o da outra |
| `type` | Sim | — | Tipo da regra (seção 4). Um tipo desconhecido interrompe a carga do arquivo inteiro |
| `asset` | Sim | — | Nome completo da tabela (`catálogo.schema.tabela`) |
| `columns` | Depende do tipo | — | Colunas avaliadas |
| `parameters` | Depende do tipo | — | Parâmetros do tipo |
| `description` | Não | — | Texto para quem lê o histórico |
| `severity` | Não | `MEDIUM` | Severidade do incidente. Use só `HIGH`, `MEDIUM` ou `LOW` |
| `domain` | Não | `DEFAULT` | Domínio do incidente, usado nos relatórios. O destino do alerta vem das regras de alerta (tipo de falha `data_quality`). A chave `domain` no topo do arquivo não vale para as regras: declare em cada uma |
| `blocking` | Não | `false` | `true`: a reprovação abre incidente. `false`: só histórico |
| `threshold_pct` | Não | `0` | Percentual de linhas inválidas aceito antes de reprovar |

## 4. Tipos de regra

| Tipo | Linha inválida quando | Exemplo |
|---|---|---|
| `not_null` | Qualquer coluna listada é nula | `columns: [entrega_id, data_entrega]` |
| `unique` | A chave se repete (cada repetição além da primeira conta) | `columns: [entrega_id]` |
| `domain` | O valor não nulo está fora da lista | `columns: [status]`, `parameters: {values: [PENDENTE, EM_TRANSITO, ENTREGUE, CANCELADA]}` |
| `range` | O valor está abaixo de `min` ou acima de `max` | `columns: [peso_kg]`, `parameters: {min: 0, max: 60000}` |
| `reference` | O valor não nulo não existe na tabela de referência | `columns: [veiculo_id]`, `parameters: {reference_table: prd_logistics.corp_curated.tbl_veiculo, reference_column: veiculo_id}` |
| `expression` | A condição SQL é verdadeira | `parameters: {invalid_condition: "data_entrega < data_saida"}` |

O arquivo de regras de exemplo do domínio de logística, no repositório, tem uma regra de cada tipo, pronta para copiar.

**Cuidado com `unique` em tabela grande:** a regra faz duas varreduras (contagem total e agrupamento).

## 5. Como o resultado é decidido

```
inválidas = linhas que violam a regra
percentual = inválidas / avaliadas × 100
```

- Zero inválidas, ou percentual até `threshold_pct`: `PASS`.
- Acima do limite: `FAIL` se a regra é bloqueante; `WARN` se não é.

### Exemplo

Regra `range` sobre `peso_kg` com `threshold_pct: 0.5`, numa tabela de 10.000 entregas:

| Linhas fora da faixa | Percentual | Resultado |
|---|---|---|
| 30 | 0,30% | `PASS` (dentro do limite de 0,5%) |
| 80 | 0,80% | `WARN` se não bloqueante, `FAIL` se bloqueante |

Mensagem do `FAIL`: "80 de 10000 linhas invalidas (0.80%), acima do limite de 0.50%."

Uma regra bloqueante que **não conseguiu rodar** também vira `FAIL` no hub, com `error_code = RULE_NOT_EVALUATED` e a mensagem "A regra nao pode ser avaliada: <erro>". Uma regra que nunca roda não pode passar despercebida.

## 6. Como disparar

O job `data_quality` do bundle recebe o domínio como parâmetro (`domain`) e lê `config/<domain>/quality-rules.yaml`. O ADF dispara uma execução por domínio, no horário de cada um ou ao fim do pipeline que carrega as tabelas. Uma execução com um domínio sem pasta falha ao abrir o arquivo, com o erro no run.

Para testar uma regra localmente, num ambiente com Spark:

```bash
python data_quality.py --regras ../../../config/<domínio>/quality-rules.yaml
```

`--regras` pode ser repetido para executar mais de um arquivo.

## 7. Onde o resultado aparece

### 7.1 Histórico (todas as regras)

```sql
-- Últimos resultados de uma tabela
SELECT rule_id, rule_version, rule_type, blocking, evaluation_status, quality_outcome,
       evaluated_row_count, invalid_row_count, invalid_pct, error, executed_at
  FROM prd_governance.corp_cleansed.tbl_dbr_it_data_quality_results
 WHERE asset = 'prd_logistics.corp_curated.tbl_entrega'
 ORDER BY executed_at DESC;
```

```sql
-- Regras que não conseguiram rodar nos últimos 7 dias
SELECT asset, rule_id, error, executed_at
  FROM prd_governance.corp_cleansed.tbl_dbr_it_data_quality_results
 WHERE evaluation_status = 'ERROR'
   AND executed_at >= current_timestamp() - INTERVAL 7 DAYS;
```

### 7.2 Incidente (só regras bloqueantes)

O caminho é o mesmo do asset_observability: staging Delta → hub-publisher (de hora em hora, no minuto 0) → incidente no hub → mensagem no Teams pelo coletor (a cada 15 minutos) → Power BI e API. Ver a seção 6 do [guia do asset_observability](asset_observability.md).

- Na staging, o evento tem `source = 'DATA_QUALITY'` e `natural_key = '<rule_id>@<tabela>'`.
- No hub, a chave do incidente é `data_quality:<rule_id>@<tabela>`: uma regra, um incidente.
- O `PASS` da próxima execução da mesma regra fecha o incidente.

```sql
SELECT result, error_code, description, observed_at, sent_at
  FROM prd_governance.corp_cleansed.tbl_dbr_it_observability_events
 WHERE source = 'DATA_QUALITY'
   AND natural_key = 'entrega_id_unico@prd_logistics.corp_curated.tbl_entrega'
 ORDER BY observed_at DESC;
```

As colunas das tabelas estão em [Tabelas-Databricks.md](../Tabelas-Databricks.md).
