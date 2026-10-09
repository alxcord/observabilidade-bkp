# 40 - YAML - Configuração de negócio

| Item | Valor |
|---|---|
| Tipo de componente | Arquivos de configuração versionados (sem código) |
| Repositório | `dbr-dataeng01-vcbr-platform` (este repositório) |
| Arquivo desta spec | `solutions/observability/docs/Especs/40-Configuracao-YAML.md` |
| Local | `solutions/observability/config/`, com uma subpasta por domínio e a subpasta `shared/` para o que vale para todos os domínios |
| Especificações relacionadas | [01](01-Hub-Azure-SQL.md), [20](20-Databricks-asset_observability.md), [21](21-Databricks-data_quality.md), [22](22-Databricks-schema_observability.md), [30](30-Azure-Functions-observability-api.md) |

## 1. Objetivo

Separar o que muda por decisão de negócio do código. A configuração tem dois lugares, conforme quem a usa:

| Quem usa | Onde fica a configuração | Como muda |
|---|---|---|
| Jobs Databricks (`data_quality`, `asset_observability`, `schema_observability`) | YAML nesta pasta (`solutions/observability/config/`) | Pull request neste repositório; vale depois do deploy do bundle |
| Container Apps Jobs do CAE (coletor, sync-expectations) | Tabelas `tbl_cfg_*` do hub (Azure SQL): destinos e regras de alerta, expectativas manuais, workspaces, domínios | Pela `observability-api` ([especificação 30](30-Azure-Functions-observability-api.md)); vale no ciclo seguinte do job |

O CAE não lê YAML: assim nenhum Container Apps Job depende do Azure DevOps em tempo de execução, e as identidades do CAE não precisam de licença nem de acesso a repositório. As tabelas do hub estão na [especificação 01](01-Hub-Azure-SQL.md). Resultados de execução nunca ficam nesta pasta nem nas tabelas de configuração.

## 2. Organização da pasta

```
solutions/observability/config/
├── <domínio>/                          uma pasta por domínio (ex.: logistics/)
│   └── quality-rules.yaml              regras de Data Quality do domínio
└── shared/                             vale para todos os domínios
    └── assets/monitored-assets.yaml
```

O nome da pasta do domínio é o mesmo valor usado em `domain` nas regras (ver seção 5). Cada pasta de domínio corresponde a uma execução própria do job `data_quality` (especificação 21). O ADF dispara o job com o parâmetro `domain` igual ao nome da pasta, no horário daquele domínio.

## 3. Arquivos, consumidores e como chegam ao ambiente

| Arquivo | Conteúdo | Consumido por | Como chega |
|---|---|---|---|
| `<domínio>/quality-rules.yaml` | Regras de Data Quality do domínio (especificação 21, seção 5) | `data_quality` | Deploy do bundle `vc-observability`, que publica a pasta da solução em `${workspace.file_path}`; caminho passado em `--regras` |
| `shared/assets/monitored-assets.yaml` | Ativos monitorados: sobrescrita de tags, bloco `volume`, `consumers`, `business_owner` | `asset_observability`, `schema_observability` | Deploy do bundle; caminho passado em `--monitored-assets-path` |

O caminho é sempre absoluto e montado com a variável do bundle, por exemplo `${workspace.file_path}/config/logistics/quality-rules.yaml`. Não há Git Folder de configuração: o YAML vai junto com o código no mesmo deploy, então uma mudança de regra só vale no ambiente depois que a esteira implanta o bundle.

## 4. Esquema de cada arquivo

**`monitored-assets.yaml`**: lista `assets`, cada item com `name` (nome completo `catálogo.schema.tabela`, obrigatório), `vc_refresh_frequency`, `vc_freshness_sla` (formato `<número><min|h|d|w|m|y>`), domínio e criticidade, `volume` (`metric`, `variation_min_pct`, `variation_max_pct`, `row_count_min`, `row_count_max`, `exceptions` com `reason`, `days_of_month` e limites substitutos), `consumers` (lista de texto; presença ativa o alerta de schema) e `business_owner` (informativo). Atenção: o `asset_observability` lê domínio e criticidade das chaves `vc_data_domain` e `vc_criticality`; o `schema_observability` lê das chaves `domain` e `criticality`. Declare as duas formas.

**`quality-rules.yaml`**: chave `rules` com os campos da especificação 21, seção 5.2. Declare `domain` em cada regra.

## 5. Regras de convivência

Chaves em inglês; valores de negócio como estão. Suba a `version` da regra a cada mudança de critério e nunca reaproveite um `id`. `blocking: true` abre incidente; use com critério. O valor de domínio precisa ser o mesmo no nome da pasta do domínio e no `domain` das regras.

Para onde vai cada alerta não é configurado aqui: o destino é escolhido pelas regras de alerta do hub (`tbl_cfg_alert_rules`), a partir do nome do objeto que falhou (catálogo, schema, tabela, pipeline ADF ou job Databricks), do tipo de falha e da severidade. Ver as especificações [01](01-Hub-Azure-SQL.md) e [10](10-Job-observability-collector.md).
