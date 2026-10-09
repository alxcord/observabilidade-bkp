# cae-observ-vcbr — Observabilidade em Container Apps

Componentes da solução de observabilidade da VC que rodam como **Azure Container Apps Jobs**: detectam falhas, ausências e travamentos em Databricks e ADF, mantêm o estado dos incidentes no hub (Azure SQL) e notificam o Teams. Os jobs Databricks de avaliação (asset_observability, data_quality, schema_observability, custom_lineage_reconciler), a API de configuração (Azure Functions) e a infraestrutura de plataforma (Terraform) vivem em outros repositórios.

## Visão de conjunto

```
 ADF ──Diagnostic Settings──▶ Log Analytics (ADFActivityRun) ───────┐
 Databricks ──Diagnostic Settings──▶ Log Analytics (DatabricksJobs) ┤
 Databricks ──Jobs API──────────────────────────────────────────────┤
                                                                    ▼
                                    observability-collector (15 min)
                                         │  resolve_event()
                                         ▼
 sync-expectations (diário) ──▶   Hub Azure SQL   ◀── API de configuração (outro repo)
                                    │  ▲   │
                         outbox ─▶ Teams │   └──▶ Power BI (DirectQuery nas views)
                                         │ resolve_event()
 jobs Databricks ─▶ staging Delta ──▶ hub-publisher (horário)
                                    hub-replication (horário) ──▶ Delta (histórico)
```

O estado de um incidente mora só no hub e só é alterado por `resolve_event()` (`src/shared/`), que decide abrir, incrementar, fechar ou ignorar a partir de uma observação `PASS`/`FAIL`. Nenhum componente informa o estado do incidente.

## Estrutura

| Pasta | O que é | Documentação |
|---|---|---|
| [src/observability-collector/](src/observability-collector/) | Coletor, a cada 15 min: Databricks, ADF (atividade, pipeline, trigger e nós do Self-Hosted IR), rede de apoio, ausência/travado, Teams, heartbeat | [doc/10](doc/10-observability-collector.md) |
| [src/sync-expectations/](src/sync-expectations/) | Diário: preenche as expectativas de execução (Databricks, ADF, YAML) | [doc/11](doc/11-sync-expectations.md) |
| [src/hub-publisher/](src/hub-publisher/) | Horário: leva os eventos da staging Delta ao hub | [doc/12](doc/12-hub-publisher.md) |
| [src/hub-replication/](src/hub-replication/) | Horário: replica execuções e incidentes resolvidos do hub para Delta | [doc/13](doc/13-hub-replication.md) |
| [src/hub-schema-init/](src/hub-schema-init/) | Manual: cria/confere o schema do hub e as permissões | [doc/14](doc/14-hub-schema-init.md) |
| [src/shared/](src/shared/) | `event_resolution.py`, máquina de estado do incidente, copiada para as imagens que a usam (coletor e publisher) | [doc/02](doc/02-resolucao-de-eventos.md) |
| [src/templates/](src/templates/) | Template de esteira único (teste → build → deploy dev → deploy prod) | — |

Cada projeto tem `Dockerfile`, `infra/` (Bicep + `params.json`), `azure-pipelines.yml`, `requirements*.txt`, `tests/` e um README próprio. O hub em si (modelo de dados, índices, views e permissões) está em [doc/01](doc/01-hub-azure-sql.md); a configuração de negócio em YAML, em [doc/40](doc/40-configuracao-yaml.md).

## Documentação

A pasta [doc/](doc/) traz uma especificação sintetizada por componente: o que faz, de onde lê, onde grava, regras de negócio, configuração, permissões, implantação, critérios de aceite e pendências conhecidas. Foi portada das especificações da solução (estado de outubro de 2026); onde divergir do código, vale o código — as **pendências** de cada documento descrevem o estado daquela revisão e devem ser reconferidas antes de agir.

## Build e esteira

Todos os projetos usam [src/templates/test-build-push-deploy.yml](src/templates/test-build-push-deploy.yml): falha se faltar `tests/`, roda `pytest`, constrói a imagem **uma vez** (tag `$(Build.BuildId)`) e a promove de dev para prod sem rebuild; prod tem approval gate no Environment.

- Os caminhos do pipeline e dos `COPY` dos Dockerfiles são a partir da **raiz do repositório** (`src/<projeto>`).
- `observability-collector` e `hub-publisher` usam a raiz como contexto de build porque copiam `src/shared/event_resolution.py`; uma alteração nele dispara o pipeline dos dois.
- Os demais usam a própria pasta do projeto como contexto.

## Desenvolvimento local

Cada projeto roda seus testes de dentro da própria pasta:

```
cd src/<projeto>
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/
```

Os testes de `src/shared/` rodam com `pytest src/shared/tests`. Fora do Docker, o import de `event_resolution` cai num `except ImportError` e aponta direto para `src/shared/`; não é preciso copiar nada à mão.

## Convenções

- Identidade de execução: `mi-observ-coletor-{dev-,}vcbr` (todos os jobs); acesso ao Azure SQL só por Entra ID, sem senha nem chave.
- Catálogo Databricks de governança: `prd_governance` (produção) e `dev_governance` (desenvolvimento).
- Cron do Container Apps é em UTC.
