# Implantação de Databricks Asset Bundles

Este documento registra como os Databricks Asset Bundles deste repositório são implantados: quais identidades participam, quais permissões cada uma precisa, onde o bundle é gravado na workspace e como diagnosticar as falhas mais comuns.

Ele complementa [Atividades de configuração de CI/CD](config-cicd-activities.md), que trata do pipeline que atualiza os Git folders. São processos distintos: aquele move a branch do Git folder, este implanta jobs, pipelines e dashboards como bundle.

Atualizado em 02/10/2026.

## 1. Visão geral

A implantação separa duas identidades, com responsabilidades e permissões diferentes:

| Papel | Identidade | O que faz |
| --- | --- | --- |
| Deploy | `sp-vc-dbx-cicd-dev` / `sp-vc-dbx-cicd-prod` | Autentica pela Service Connection, grava os arquivos do bundle na workspace e cria ou atualiza os jobs. Não executa nada. |
| Execução | `VC_OWNER_SERVICE` | É quem o job assume em tempo de execução: cria o cluster, lê os arquivos do bundle e acessa os dados no Unity Catalog. |

A separação é exigência de boa prática do Databricks e tem efeito prático: a identidade de CI/CD pode ficar sem acesso a dado nenhum, e rotacionar ou restringir o acesso a dados não exige mexer no pipeline.

No bundle, a identidade de execução é declarada em `run_as`, no topo de `solutions/observability/databricks.yml`:

```yaml
variables:
  run_as_service_principal:
    lookup:
      service_principal: VC_OWNER_SERVICE

run_as:
  service_principal_name: ${var.run_as_service_principal}
```

O campo `service_principal_name` exige o Application ID, não o nome de exibição. O `lookup` faz essa tradução no momento do deploy, consultando a workspace de destino. Por isso o YAML pode citar `VC_OWNER_SERVICE` e continuar válido nas duas workspaces, cada uma com seu próprio Application ID.

Quando a identidade de deploy é diferente da identidade de `run_as`, o bundle só pode conter jobs e pipelines. Model serving endpoints, por exemplo, passam a ser rejeitados.

## 2. Pasta de implantação na workspace

O bundle é gravado em `/Workspace/.bundle/platform/<nome-do-bundle>`, definido em `workspace.root_path`:

```yaml
workspace:
  root_path: /Workspace/.bundle/platform/${bundle.name}
```

O deploy grava nessa pasta três coisas: `files/`, com o código sincronizado, `artifacts/`, com a wheel, e `state/`, com o estado do bundle.

O sufixo `${bundle.name}` é obrigatório: `deploy-changed-bundles.sh` percorre todos os `databricks.yml` do repositório, e sem ele duas soluções de `solutions/` gravariam `files/` e `state/` na mesma pasta, uma sobrescrevendo o estado da outra.

O target não entra no caminho de propósito, para que `artifacts/` e `files/` fiquem no mesmo lugar em todos os ambientes. Isso só é seguro porque dev e produção são workspaces distintas. Um segundo target apontado para uma workspace que já recebe outro target passaria a disputar o mesmo `state/`, e nesse caso o sufixo `${bundle.target}` precisa voltar.

Os outros caminhos possíveis foram descartados:

| Caminho | Por que não |
| --- | --- |
| `/Workspace/Users/<SP de deploy>/...` | É o padrão do CLI, mas a pasta é privada da identidade que implanta. A `run_as` não consegue ler o `python_file` nem a wheel das tasks, e o job falha mesmo com o cluster criado. |
| `/Workspace/Shared/...` | Não é possível restringir a ACL: todo usuário da workspace fica com CAN_MANAGE sobre o código de produção. |
| `/Workspace/Repos/...` | É o Git folder atualizado pelo pipeline de CI/CD. O deploy gravaria `files/` e `state/` dentro do checkout. |

`/Workspace/.bundle` fica na raiz da workspace, onde a ACL não herda de `Shared` e pode ser restringida identidade por identidade. Em troca, a pasta precisa ser criada por um administrador antes do primeiro deploy, conforme a seção 3.3.

## 3. Permissões necessárias

### 3.1 VC_OWNER_SERVICE, identidade de execução

Conceder nas duas workspaces, dev e produção.

| Permissão | Onde | Por que |
| --- | --- | --- |
| Entitlement **Workspace access** | Settings, Identity and access, Service principals, VC_OWNER_SERVICE, Entitlements | Sem ele a identidade não é utilizável na workspace. |
| Entitlement **Allow unrestricted cluster creation**, ou CAN_USE em uma cluster policy que permita `Standard_D4ds_v5` | Mesma tela, ou Compute, Policies | Os jobs usam `job_clusters` com `new_cluster`. O cluster é criado em tempo de execução pela `run_as`. É a permissão que falta quando aparece o `PERMISSION_DENIED` da seção 7.1. |
| CAN_READ na pasta `/Workspace/.bundle` | Workspace, pasta, Permissions | A task precisa ler o `python_file` e a wheel gravados pelo deploy. |
| `USE CATALOG` e `USE SCHEMA` nos catálogos `dev_*` ou `prd_*` | Unity Catalog | O job `asset_observability` varre os catálogos do ambiente. |
| `SELECT` nas tabelas monitoradas | Unity Catalog | `DESCRIBE DETAIL` e leitura das tags. |
| `MODIFY` e `SELECT` no schema de destino | Unity Catalog | Gravação do histórico de observações e dos eventos em Delta. |

Os clusters dos jobs usam `data_security_mode: SINGLE_USER`, então os grants do Unity Catalog são avaliados como `VC_OWNER_SERVICE`, não como a identidade de deploy nem como quem disparou a execução.

### 3.2 Identidades de deploy

| Permissão | Onde | Por que |
| --- | --- | --- |
| **CAN_USE** sobre a service principal `VC_OWNER_SERVICE` | Settings, Identity and access, Service principals, VC_OWNER_SERVICE, Permissions | Sem isso o deploy falha ao declarar `run_as` apontando para outra identidade. É também o que permite ao `lookup` resolver o Application ID. |
| **CAN_MANAGE** na pasta `/Workspace/.bundle` | Workspace, pasta, Permissions | O deploy grava `files/` e `state/`. |

As permissões de Azure DevOps, a Service Connection, a licença Basic e a credencial Git dessas identidades estão documentadas em [Atividades de configuração de CI/CD](config-cicd-activities.md). Nada disso muda por causa do `run_as`.

### 3.3 Pasta /Workspace/.bundle

Executar uma vez por workspace, com uma conta administradora, antes do primeiro deploy:

1. Na workspace, abrir Workspace e selecionar a raiz.
2. Criar a pasta `.bundle`.
3. Abrir o menu da pasta, Permissions.
4. Conceder CAN_MANAGE à service principal de deploy do ambiente, `sp-vc-dbx-cicd-dev` ou `sp-vc-dbx-cicd-prod`.
5. Conceder CAN_READ a `VC_OWNER_SERVICE`.
6. Não conceder permissão a grupos amplos como `users` ou `account users`.

Objetos criados na raiz da workspace exigem perfil de administrador. Se a criação da pasta não for possível diretamente, ela deve ser solicitada por chamado, junto com as duas atribuições de ACL.

### 3.4 Permissão de execução para pessoas

Os jobs são criados pela identidade de deploy e não aparecem para os demais usuários. Quem precisar disparar ou acompanhar as execuções precisa de permissão explícita no job, concedida pela interface ou declarada no bundle pelo mapeamento `permissions`.

## 4. Nome dos recursos e modos de deploy

O nome dos jobs é definido em `resources/jobs.yml` e é o mesmo em todos os targets, sem prefixo de ambiente:

```yaml
name: 'vc-observability-asset-observability'
```

O nome igual em DEV e PRD permite que o pipeline do ADF chame o job pelo mesmo nome nos dois ambientes. Não há conflito porque cada target é implantado numa workspace própria; um target novo na mesma workspace de outro exigiria nome diferente, pelo mesmo motivo que exigiria `root_path` diferente (seção 2).

O target `dev` não usa `mode: development`. Aquele modo acrescenta ao nome de todo recurso o prefixo `[dev ${workspace.current_user.short_name}]`, o que produzia nomes como `[dev sp_vc_dbx_cicd_dev] vc-observability-asset-observability`, e exige que `root_path` fique dentro da pasta do usuário que implanta, o que é incompatível com a seção 2.

Os comportamentos do modo que interessam ao ambiente de desenvolvimento foram declarados explicitamente:

```yaml
targets:
  dev:
    default: true
    presets:
      trigger_pause_status: PAUSED
      pipelines_development: true
```

| Comportamento de `mode: development` | Situação atual |
| --- | --- |
| Prefixo `[dev <usuário>]` nos nomes | Removido de propósito. |
| Agendas e triggers pausados | Mantido por `presets.trigger_pause_status: PAUSED`. |
| Pipelines marcados como `development: true` | Mantido por `presets.pipelines_development: true`. |
| `root_path` restrito à pasta do usuário | Removido de propósito. Ver seção 2. |
| Tag `dev` automática nos recursos | Não reposta. As tags de custo são declaradas em `resources/jobs.yml`. |
| Lock de deploy desabilitado | Volta a ficar habilitado, comportamento mais conservador. |
| `--cluster-id` sobrescreve os clusters do bundle | Não disponível. Não era usado. |

O target `prd` mantém `mode: production`. A validação daquele modo que proíbe `root_path` específico de usuário só se aplica quando `run_as` não é uma service principal, o que não é o caso.

## 5. Testes automatizados no deploy

`deploy-changed-bundles.sh` executa os testes de cada bundle antes do `validate` e do `deploy`. Um bundle com teste falhando não é implantado; os demais seguem, e o script termina com erro no fim.

A descoberta é por convenção, não por configuração. Para cada bundle afetado, o script procura todas as pastas chamadas `tests` abaixo da raiz do bundle, ignorando `.venv`, `build`, `node_modules` e `.git`, e executa as que tiverem pelo menos um arquivo `test_*.py`. Cada pasta roda em um ambiente virtual próprio, criado do zero.

As dependências vêm da pasta que contém a pasta `tests`:

| Arquivo na pasta do projeto | Efeito |
| --- | --- |
| `requirements.txt` | Instalado com `pip install -r`. |
| `requirements-dev.txt` | Instalado com `pip install -r`. |
| `pyproject.toml` | O projeto é instalado em modo editável, com `pip install -e`. |

Na solução de observabilidade isso resulta em quatro suítes: `tests` na raiz, que usa o `pyproject.toml` da solução, e uma pasta `tests` em cada um de `asset_observability`, `data_quality` e `schema_observability`, cada uma com os seus `requirements`.

Os relatórios são gravados em formato JUnit em `TEST_RESULTS_DIR` e publicados pela task `PublishTestResults@2`, que roda com `condition: always()` para que os resultados apareçam também quando os testes falham.

O log registra, por suíte, a pasta encontrada, os arquivos de teste, a pasta de execução do pytest, o ambiente virtual, o interpretador, os arquivos de dependências usados e a saída do pytest em modo verboso. Com `DEBUG_MODE: 'true'`, o Databricks CLI também passa a receber `--debug`.

Os três módulos declaram `pyspark` em `requirements.txt`, mas os testes substituem a biblioteca por um stub. A instalação acontece de todo modo, e é a maior parte do tempo de execução da etapa de testes.

## 6. Imagem do agente

Os pipelines e o template de deploy fixam `vmImage: ubuntu-24.04` em vez de `ubuntu-latest`. O label `ubuntu-latest` migra para Ubuntu 26.04 entre 19 de outubro e 19 de novembro de 2026, e usá-lo fazia o Azure DevOps emitir um aviso em cada execução. Fixar a imagem elimina o aviso e evita que o agente mude de versão sem alteração no repositório.

A atualização para uma imagem nova passa a ser uma mudança explícita, validada no pipeline de desenvolvimento antes de chegar a produção.

## 7. Diagnóstico de falhas

### 7.1 PERMISSION_DENIED ao preparar o cluster

```text
Unexpected user error while preparing the cluster for the job.
Cause: PERMISSION_DENIED: You are not authorized to create clusters.
Please contact your administrator.
```

A identidade de execução não tem permissão para criar cluster. O deploy funcionou; a falha é na execução. Confirmar em Runs qual identidade aparece em Run as. Se for a service principal de CI/CD, o `run_as` não foi aplicado. Se for `VC_OWNER_SERVICE`, falta o entitlement da seção 3.1.

### 7.2 Falha ao resolver a service principal

O deploy falha ao resolver a variável `run_as_service_principal`. A service principal não existe com o nome `VC_OWNER_SERVICE` naquela workspace, ou a identidade de deploy não tem CAN_USE sobre ela. Conferir o nome exato em Settings, Identity and access, Service principals.

Se o nome de exibição divergir entre as workspaces, sobrescrever a variável no target afetado em vez de trocar o valor global.

### 7.3 root_path recusado na validação

```text
root_path must start with '~/' or contain the current username
to ensure uniqueness when using 'mode: development'
```

O target voltou a declarar `mode: development` junto com um `root_path` compartilhado. As duas coisas são incompatíveis: ou o caminho fica na pasta do usuário, ou o modo sai do target.

### 7.4 Task falha ao ler o arquivo

O cluster sobe e a task falha ao localizar o `python_file` ou a wheel. A `run_as` não tem CAN_READ na pasta do bundle. Conferir a ACL de `/Workspace/.bundle` conforme a seção 3.3.

### 7.5 Jobs duplicados depois de mudar o root_path

O estado do bundle vive em `root_path/state`. Mudar o caminho faz o deploy começar com estado vazio e criar jobs novos, deixando os antigos na workspace. Os jobs órfãos precisam ser removidos pela interface, ou por `databricks bundle destroy` executado com a configuração antiga antes da mudança.

## 8. Validação local

A validação local exige um profile autenticado, porque o CLI consulta a workspace para resolver o `lookup` da service principal e a identidade corrente:

```bash
cd solutions/observability
databricks bundle validate --target dev --profile dbr01dev
```

Sem autenticação válida o comando falha antes de avaliar a configuração, com `invalid_grant: Refresh token is invalid`. Nesse caso, reautenticar o profile.

Para conferir apenas a estrutura do YAML, sem workspace, é possível validar a configuração contra o schema publicado pelo próprio CLI:

```bash
databricks bundle schema > schema.json
```

## 9. Próximos passos

- Criar `/Workspace/.bundle` e aplicar as ACLs da seção 3.3 nas duas workspaces.
- Conceder os entitlements e grants da seção 3.1 a `VC_OWNER_SERVICE` nas duas workspaces.
- Conceder CAN_USE sobre `VC_OWNER_SERVICE` às duas identidades de deploy.
- Remover os jobs órfãos criados pelos `root_path` anteriores, conforme a seção 7.5. São dois
  momentos: a saída de `/Workspace/Users/<SP de deploy>` e a retirada do target do caminho.
- Declarar o mapeamento `permissions` no bundle, para que o time veja e execute os jobs sem atribuição manual.
- Registrar o Application ID de `VC_OWNER_SERVICE` de cada ambiente na tabela de identidades de [Atividades de configuração de CI/CD](config-cicd-activities.md).
