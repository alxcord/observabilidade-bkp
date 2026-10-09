# Índice das especificações

Este conjunto descreve, módulo a módulo, o que cada peça da solução faz, de onde lê, onde grava, quais regras aplica e o que precisa para rodar. Foi escrito a partir do código em `src/` (estado de outubro de 2026), não do documento de arquitetura: onde os dois divergem, a especificação registra o comportamento real e aponta a divergência. O público-alvo é quem vai implantar, corrigir e operar a solução sem ter participado do desenho.

| Item | Valor |
|---|---|
| Repositório | `dbr-dataeng01-vcbr-platform` (este repositório). Cada spec de módulo indica o repositório onde o código está ou vai estar |
| Arquivo desta spec | `solutions/observability/docs/Especs/00-Indice.md` |
| Código | Não se aplica. Cada spec de módulo traz o seu repositório, o próprio caminho e o caminho do código |

## 1. Visão de conjunto

```
 ADF ──Diagnostic Settings──▶ Log Analytics (ADFActivityRun) ───────────┐
 Databricks ──Diagnostic Settings──▶ Log Analytics (DatabricksJobs) ─────┤
 Databricks ──Jobs API──────────────────────────────────────────────────┤
                                                                         ▼
                                              [10] observability-collector (15 min)
                                                   │  resolve_event() [02]
                                                   ▼
 [11] sync-expectations (diário) ──▶ [01] Hub Azure SQL ◀── [30] observability-api (CRUD de config, leitura)
                                                   │  ▲                    ▲
                                    outbox ─▶ Teams│  │ resolve_event()    │ Power BI (DirectQuery nas views)
                                                   │  │
 [20] asset_observability ─┐                       │  [12] hub-publisher (horário)
 [21] data_quality ────────┼─▶ staging Delta ──────┼──▶ (Statement Execution API)
 [22] schema_observability ┘                       │
                                                   ▼
                                    [13] hub-replication (horário) ──▶ Delta (histórico 2 anos)
 [23] custom_lineage_reconciler ──▶ Unity Catalog (External Lineage)       
 [40] YAML de configuração ──▶ 20, 21, 22              [50] scripts ──▶ Diagnostic Settings
```

**Convenção de ambiente.** O catálogo Databricks de governança, que guarda a staging, o histórico e as réplicas do hub, é `prd_governance` em produção e `dev_governance` em desenvolvimento. As especificações o escrevem como `<gov>`. Os recursos Azure seguem a convenção `-dev` / sem sufixo já descrita no inventário de arquitetura.

## 2. Documentos

| Nº | Tipo de recurso | Módulo | Execução |
|---|---|---|---|
| [01](01-Hub-Azure-SQL.md) | Azure SQL Database | Hub operacional | Banco transacional, sempre disponível |
| [02](02-Resolucao-de-Eventos.md) | Biblioteca Python | Resolução de eventos (event_resolution.py) | Dentro de 10 e 12 |
| [10](10-Job-observability-collector.md) | Azure Container Apps Job | observability-collector | A cada 15 min |
| [11](11-Job-sync-expectations.md) | Azure Container Apps Job | sync-expectations | Diário |
| [12](12-Job-hub-publisher.md) | Azure Container Apps Job | hub-publisher | Horário |
| [13](13-Job-hub-replication.md) | Azure Container Apps Job | hub-replication | Horário |
| [20](20-Databricks-asset_observability.md) | Databricks | asset_observability | Diário |
| [21](21-Databricks-data_quality.md) | Databricks | data_quality | Disparado pelo ADF, uma vez por domínio |
| [22](22-Databricks-schema_observability.md) | Databricks | schema_observability | Diário |
| [23](23-Databricks-custom_lineage_reconciler.md) | Databricks | custom_lineage_reconciler | 1 a 2 vezes ao dia |
| [30](30-Azure-Functions-observability-api.md) | Azure Functions | observability-api | Flex Consumption, sob demanda |
| [40](40-Configuracao-YAML.md) | YAML | Configuração de negócio | Publicado com o bundle, a partir de solutions/observability/config/ |
| [50](50-Scripts-Diagnostic-Settings.md) | Scripts PowerShell | Diagnostic Settings | Execução esporádica |

Todos seguem a mesma estrutura: identificação, objetivo funcional, entradas, saídas, fluxo de processamento, regras de negócio, configuração, permissões, implantação e critérios de aceite.

## 3. Ordem de implantação recomendada

1. Plataforma (`terraform/platform/`): rede, Azure SQL, identidades, Container Apps Environment, Key Vault, papel `Log Analytics Reader`, DCE/DCR e Access Policy da API.
2. Hub (01): DDL, usuários de banco, workspaces cadastrados, destino padrão de alerta com o segredo real e regra `* / * / *`, watermarks semeados.
3. Diagnostic Settings (50) em todos os ADFs e workspaces Databricks.
4. Registro da identidade dos jobs na conta Databricks e nos workspaces.
5. Esteiras; jobs 10 e 11. Neste ponto já existe o caminho mínimo: falha detectada, incidente aberto, Teams avisado.
6. Tabelas Delta por DDL (staging, histórico, réplicas); jobs 20, 21, 22 e 12.
7. Hub-replication (13) e a purga do hub.
8. API (30); lineage (23) em simulação e depois com `--apply`.
