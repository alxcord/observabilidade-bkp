# dbr-dataeng01-vcbr-platform

> **Documentação publicada automaticamente na Wiki.** As páginas da Wiki em **Projetos e Iniciativas > Plataforma** são geradas a partir deste repositório a cada merge na `main`. Não edite essas páginas direto na Wiki: a alteração é sobrescrita na próxima publicação. Para corrigir ou acrescentar documentação, altere o arquivo `.md` no repositório e abra um pull request. Ver [Publicação da documentação no wiki](docs/publicacao-wiki.md).

Este repositório contém componentes da plataforma de dados da Votorantim Cimentos assim como seus pipelines usados para atualizar os Git folders protegidos no Azure Databricks.

## Organização no Databricks

O nome atual do recurso no Databricks é **Git folder**. O caminho histórico `/Workspace/Repos` continua sendo utilizado neste projeto como área central e protegida de implantação.

Os desenvolvedores trabalham em clones individuais dentro de suas respectivas pastas de usuário. Os Git folders abaixo são bloqueados para alteração manual e atualizados exclusivamente pelo processo de CI/CD:

```text
/Workspace/Repos/VC-ANALYTICS/
├── dbr-dataeng01-vcbr
└── dbr-dataeng01-vcbr-platform
```

| Repositório | Caminho no Databricks | Finalidade |
|---|---|---|
| `dbr-dataeng01-vcbr` | `/Workspace/Repos/VC-ANALYTICS/dbr-dataeng01-vcbr` | Repositório genérico existente |
| `dbr-dataeng01-vcbr-platform` | `/Workspace/Repos/VC-ANALYTICS/dbr-dataeng01-vcbr-platform` | Componentes da plataforma |

Os mesmos caminhos são utilizados nas duas workspaces, mas cada workspace possui seu próprio Git folder e, consequentemente, seu próprio Repo ID:

- Workspace de desenvolvimento: branch `Develop`.
- Workspace de produção: branch `main`.

O repositório remoto deste projeto é:

```text
https://grupovotorantim.visualstudio.com/VC-ANALYTICS/_git/dbr-dataeng01-vcbr-platform
```

## Organização interna do repositório

O repositório é organizado por soluções implantáveis. Cada nova solução deve ser criada em `solutions/<nome-da-solucao>` e manter no mesmo diretório seu código, recursos Databricks, testes e documentação. Essa convenção permite que uma solução evolua de forma independente sem espalhar seus arquivos pela raiz.

```text
dbr-dataeng01-vcbr-platform/
├── .azuredevops/
│   ├── templates/
│   └── review-prompt.md
├── docs/
│   ├── config-cicd-activities.md
│   ├── deploy-bundles.md
│   ├── publicacao-modulos-python.md
│   └── publicacao-wiki.md
├── solutions/
│   └── observability/
│       ├── databricks.yml
│       ├── pyproject.toml
│       ├── README.md
│       ├── src/
│       │   └── vc_observability/
│       ├── resources/
│       ├── notebooks/
│       ├── sql/
│       │   └── ddl/
│       ├── tests/
│       └── docs/
│           ├── architecture/
│           └── runbooks/
├── azure-pipelines-develop-ci.yml
├── azure-pipelines-main-ci.yml
└── README.md
```

A pasta `.azuredevops` concentra arquivos usados pelo Azure DevOps. Os templates de bundle ficam em `.azuredevops/templates`, enquanto o prompt da revisão automatizada permanece em `.azuredevops/review-prompt.md`. Os YAMLs principais de atualização dos Git folders continuam na raiz para preservar os caminhos já configurados nos pipelines.

Dentro de cada solução, `databricks.yml` define o Databricks Asset Bundle e seus ambientes, enquanto `pyproject.toml` descreve o pacote Python. O código importável segue o padrão `src/<nome_do_pacote>`, que separa o pacote de notebooks, testes e arquivos de implantação. A repetição entre `solutions/observability` e `src/vc_observability` é intencional: o primeiro nome identifica a solução no repositório e o segundo define o namespace usado em imports Python.

Os arquivos de jobs, Lakeflow Declarative Pipelines e dashboards ficam em `resources`. Notebooks devem funcionar como pontos de entrada leves, deixando a lógica reutilizável no pacote Python. Todos os objetos SQL são mantidos em `sql/ddl`, sem separação adicional por tipo. Testes permanecem juntos em `tests` até que o volume justifique uma divisão. A documentação técnica fica em `docs/architecture`, incluindo decisões arquiteturais, e os procedimentos de operação ficam em `docs/runbooks`.

Não devem ser criadas pastas `dev` e `prod` dentro das soluções. Os mesmos arquivos são promovidos entre as workspaces e as diferenças de ambiente são expressas pelos targets do bundle ou pelas variáveis dos pipelines. Também não haverá infraestrutura Terraform neste repositório. Bibliotecas compartilhadas por várias soluções continuam pertencendo ao repositório específico de bibliotecas; somente código exclusivo de uma solução deve permanecer em seu diretório. O processo proposto para empacotar, versionar e promover essas bibliotecas está descrito em [Publicação de módulos Python compartilhados](docs/publicacao-modulos-python.md).

## Configuração de CI/CD

O histórico das atividades realizadas, chamados, permissões, variáveis e procedimentos de configuração dos pipelines está documentado em [Atividades de configuração de CI/CD](docs/config-cicd-activities.md).

A implantação das soluções como Databricks Asset Bundles é um processo distinto, com identidades e permissões próprias, documentado em [Implantação de Databricks Asset Bundles](docs/deploy-bundles.md). Aquele documento registra a separação entre a identidade que implanta e a identidade que executa os jobs, a pasta usada na workspace e as permissões exigidas por cada uma.

Esta documentação é publicada automaticamente no wiki do projeto, sob **Projetos e Iniciativas > Plataforma**, a cada merge na `main`. O processo reúne em uma única árvore os documentos de `docs/` e os de cada solução, em `solutions/<solucao>/docs/`, e está descrito em [Publicação da documentação no wiki](docs/publicacao-wiki.md). As páginas são geradas: correção de documentação é alteração no repositório, não edição pelo wiki.

## Templates para Databricks Asset Bundles

Os templates `.azuredevops/templates/databricks-bundle-validate.yml` e `.azuredevops/templates/databricks-bundle-deploy.yml` podem ser reutilizados por qualquer solução. Ambos autenticam com a Service Connection recebida como parâmetro e configuram o Databricks CLI para usar a sessão federada do Azure CLI. O template de implantação sempre executa a validação antes do deploy.

Um pipeline pode validar a solução de observabilidade desta forma:

```yaml
steps:
- template: .azuredevops/templates/databricks-bundle-validate.yml
  parameters:
    serviceConnection: 'sc-vc-dbx-cicd-dev'
    databricksHost: 'https://adb-1886204700223023.3.azuredatabricks.net'
    bundleRoot: '$(Build.SourcesDirectory)/solutions/observability'
    target: 'dev'
```

Depois da validação, o deployment utiliza os mesmos parâmetros:

```yaml
steps:
- template: .azuredevops/templates/databricks-bundle-deploy.yml
  parameters:
    serviceConnection: 'sc-vc-dbx-cicd-dev'
    databricksHost: 'https://adb-1886204700223023.3.azuredatabricks.net'
    bundleRoot: '$(Build.SourcesDirectory)/solutions/observability'
    target: 'dev'
```

Em produção, devem ser usados `sc-vc-dbx-cicd-prod`, o host da workspace PROD e o target `prod`. A Service Connection precisa estar autorizada para o pipeline que chama o template. Os agentes também precisam acessar `raw.githubusercontent.com` para instalar o Databricks CLI; se a política corporativa bloquear esse endereço, o instalador deve ser disponibilizado em um repositório de artefatos aprovado.

Esses templates não substituem automaticamente os pipelines atuais que atualizam o Git folder por Repo ID. Eles fornecem o caminho para implantar jobs, pipelines e dashboards como bundle quando cada solução estiver pronta para esse modelo.
