# Estrutura da pasta da solução

A pasta `solutions/observability/` contém o código Python (`vc_observability`), o Databricks Asset Bundle que implanta os jobs, a configuração de negócio em YAML e os testes desses componentes. A visão geral da solução, os repositórios relacionados e os diagramas estão em [docs/README.md](../README.md).

## Estrutura

```text
observability/
├── databricks.yml              entrada do Databricks Asset Bundle
├── pyproject.toml              pacote vc-observability
├── src/vc_observability/
│   ├── asset_observability/    freshness e volume (com testes próprios)
│   ├── data_quality/           regras de qualidade (com testes próprios)
│   ├── schema_observability/   mudanças de schema (com testes próprios)
│   ├── lineage/                reconciliador de External Lineage
│   ├── pipeline_logging/       contrato e logger de execução de pipelines
│   └── alerting/               reservado para alertas
├── resources/
│   ├── jobs.yml                jobs do bundle
│   ├── pipelines.yml           pipelines do bundle
│   └── dashboards.yml          dashboards do bundle
├── config/                     configuração de negócio (ver especificação 40)
│   ├── <domínio>/              regras de qualidade de cada domínio
│   └── shared/                 ativos monitorados
├── notebooks/                  notebooks operacionais (ver abaixo)
├── scripts/
│   └── log-analytics/          ativação dos Diagnostic Settings (ADF e Databricks)
├── tests/                      testes do reconciliador de lineage (ver abaixo)
└── docs/                       documentação (ver docs/README.md)
```

O coletor de falhas de execução, o sync-expectations, o hub-publisher e o hub-replication não estão nesta pasta: são Container Apps Jobs do repositório `cae-observ-vcbr` (ver [docs/README.md](../README.md)). O padrão comum aos jobs Databricks está em [jobs-databricks.md](jobs-databricks.md), e as tabelas que eles gravam, em [Tabelas-Databricks.md](../Tabelas-Databricks.md).

## Jobs do bundle

`resources/jobs.yml` define os jobs:

- **`custom_lineage_reconciler`**: roda `reconcile_custom_lineage` como wheel. O parâmetro `mode` começa em `dry-run` e só grava com `apply`. Os filtros vêm das variáveis `lineage_catalog` e `lineage_schema`; quando vazias, examinam todos os catálogos e schemas permitidos para a Service Principal, exceto os internos excluídos pelo código. Detalhes em [custom-lineage-reconciler.md](../architecture/custom-lineage-reconciler.md) e na [spec 23](../Especs/23-Databricks-custom_lineage_reconciler.md).
- **`asset_observability`**: roda `asset_observability.py` com `--environment ${bundle.target}` e `--monitored-assets-path ${workspace.file_path}/config/shared/assets/monitored-assets.yaml`. Não tem agenda; é disparado pelo ADF. Ver a [spec 20](../Especs/20-Databricks-asset_observability.md).

A forma de disparo do `data_quality` e do `schema_observability` está nas [spec 21](../Especs/21-Databricks-data_quality.md) e [spec 22](../Especs/22-Databricks-schema_observability.md).

## Pacote e wheel

O `pyproject.toml` define o pacote `vc-observability` e descobre o código em `src`. Os imports seguem o namespace `vc_observability`, por exemplo `from vc_observability.lineage import ...`.

O bundle gera o wheel com `python -m pip wheel . --no-deps --wheel-dir dist`. Esse é o comando de `artifacts` em `databricks.yml`.

## Reconciliador de lineage

O código está em `src/vc_observability/lineage/reconciler.py` e os testes em `tests/test_lineage_reconciler.py`. As tags lidas das tabelas do Unity Catalog são `vc_source_system`, `vc_source_object` e `vc_source_column`, mais as opcionais `SOURCE_SCHEMA` e `SOURCE_OBJECT_TYPE`.

## Log de execução de pipelines

O módulo `vc_observability.pipeline_logging` mantém o contrato de eventos, o DDL da tabela governada e o logger com escrita em lote. Consumidores novos importam assim:

```python
from vc_observability.pipeline_logging import EventLogger, probe_audit_sink
```

Os imports antigos a partir de `modules.vc_pipeline_observability_coe` funcionam só no repositório original. Ver [pipeline-execution-logging.md](../architecture/pipeline-execution-logging.md) e a seção 9 de [Tabelas-Databricks.md](../Tabelas-Databricks.md).

## Testes

Os testes ficam em dois lugares:

- `tests/`: reconciliador de lineage, com clientes falsos, sem acesso a workspace. Os testes desta pasta devem refletir os módulos existentes em `src/vc_observability`; a separação entre testes unitários e de integração só deve ser criada quando houver volume suficiente para justificá-la.
- `src/vc_observability/<módulo>/tests/`: `asset_observability`, `data_quality` e `schema_observability`, cada um com `requirements.txt` e `requirements-dev.txt` próprios. Usam stub de `pyspark` e rodam em qualquer máquina, sem workspace nem cluster.

Para rodar a suíte da raiz:

```bash
python -m pytest
```

Para rodar a de um módulo:

```bash
cd src/vc_observability/asset_observability && python -m pytest tests
```

Quem altera código deve rodar a suíte localmente antes de subir.

## Notebooks

A pasta `notebooks/` é para notebooks operacionais ou exploratórios da observabilidade. A lógica reutilizável deve permanecer no pacote `vc_observability`; os notebooks devem apenas receber parâmetros, chamar o pacote e apresentar resultados.

## Validação e implantação

O target padrão é `dev`. Com o Databricks CLI autenticado, valide localmente na raiz da pasta da solução:

```bash
databricks bundle validate --target dev
```

Como o CLI resolve a identidade de execução (`run_as`) na workspace, a validação precisa de um profile ou de `DATABRICKS_HOST` exportado:

```bash
databricks bundle validate --target dev --profile <profile-da-workspace-dev>
databricks bundle deploy --target dev --profile <profile-da-workspace-dev>
```

Na pipeline, o host é injetado por ambiente pelo template `.azuredevops/templates/databricks-bundle-deploy.yml`. A autenticação usa Workload Identity Federation pela service connection, sem credencial no YAML. Produção não reutiliza a identidade de DEV.

### Identidades de CI/CD

| Ambiente | Service connection (Azure DevOps) | Service principal de CI/CD (Databricks) | Observação |
| --- | --- | --- | --- |
| DEV | `sc-vc-dbx-cicd-dev` | `sp-vc-dbx-cicd-dev` | Usada pelas pipelines de `Develop` |
| PRD | `sc-vc-dbx-cicd-prod` | `sp-vc-dbx-cicd-prod` | Somente pela pipeline autorizada de `main` |

- A service connection autentica a pipeline no Azure. A service principal de CI/CD **apenas implanta** o bundle e atualiza o Git folder protegido.
- Os jobs **executam** como a identidade definida em `run_as` (`VC_OWNER_SERVICE`), separada da identidade de deploy. A service principal de CI/CD precisa de `CAN_USE` sobre ela.
- O bundle é gravado em `/Workspace/.bundle/platform/${bundle.name}`, sem o nome do ambiente no caminho. A pasta da solução, inclusive `config/`, fica em `${workspace.file_path}` dentro dele. Permissões, ACLs da pasta e diagnóstico de falhas de deploy e execução estão em [docs/deploy-bundles.md](../../../../docs/deploy-bundles.md).

## Desenvolvimento local

Para instalar o pacote em modo editável, na raiz da pasta da solução:

```bash
python -m pip install --editable .
```
