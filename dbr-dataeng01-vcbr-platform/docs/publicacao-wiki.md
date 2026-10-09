# Publicação da documentação no wiki

Este documento descreve como a documentação versionada do repositório é publicada no wiki do projeto, o que entra, quais convenções são aplicadas e quais permissões são necessárias.

Atualizado em 02/10/2026.

## 1. Visão geral

A publicação roda como um estágio do pipeline de produção, depois do deploy dos bundles, e só em execuções de merge na `main`, nunca em validação de pull request. O estágio chama o template `.azuredevops/templates/publish-wiki.yml`, que executa dois scripts:

| Script | Papel |
| --- | --- |
| `.azuredevops/scripts/publish-wiki.sh` | Clona o repo do wiki, orquestra, commita e empurra. |
| `.azuredevops/scripts/build-wiki-tree.py` | Lê a documentação do repositório e escreve as páginas dentro do clone. |

O destino é o **wiki provisionado** do projeto, cujo repositório é `VC-ANALYTICS.wiki` na branch `wikiMaster`. A publicação não usa o recurso "Publish code as wiki": aquele recurso mapeia uma pasta por wiki, e aqui a documentação vem de várias pastas, que precisam aparecer em uma única árvore.

A subárvore administrada é definida pela variável `WIKI_ROOT`, no formato de pastas do wiki, em que o hífen representa espaço no título. O padrão é `Projetos-e-Iniciativas/Plataforma`, que publica sob a página **Projetos e Iniciativas > Plataforma**.

Nada fora dessa subárvore é tocado. Páginas irmãs escritas à mão continuam no lugar, e os arquivos `.order` dos níveis acima são apenas complementados com a entrada que falta, preservando as entradas existentes.

## 2. Estrutura gerada

```text
Projetos e Iniciativas/
└── Plataforma/                                  <- README.md da raiz
    ├── Atividades de configuração de CI CD      <- docs/config-cicd-activities.md
    ├── Implantação de Databricks Asset Bundles  <- docs/deploy-bundles.md
    ├── Publicação da documentação no wiki       <- docs/publicacao-wiki.md
    ├── Publicação de módulos Python ...         <- docs/publicacao-modulos-python.md
    └── Observabilidade/                         <- solutions/observability/docs/README.md
        ├── Arquitetura/                         <- .../docs/architecture/README.md
        │   ├── Custom Lineage Reconciler
        │   └── ... demais documentos de architecture
        └── Runbooks                             <- .../docs/runbooks/README.md
```

Solução nova entra sozinha: o script varre `solutions/*/docs`, sem lista fixa de soluções.

## 3. O que é publicado

| Origem no repositório | Destino no wiki |
| --- | --- |
| `README.md` da raiz | A própria página `Plataforma` |
| `docs/**/*.md` | Páginas sob `Plataforma` |
| `solutions/<sol>/docs/README.md` | A página da solução; na falta dele, `solutions/<sol>/README.md` |
| `solutions/<sol>/docs/**/*.md` | Páginas sob a página da solução |

O `README.md` de uma pasta de documentação vira a página daquela pasta, e os demais arquivos dela viram subpáginas. Quando uma pasta não tem `README.md`, o script gera uma página de índice com os links das subpáginas.

Os READMEs de pastas de código ficam fora de propósito: `notebooks`, `sql/ddl`, `tests`, `src/vc_observability` e os READMEs de módulo documentam o repositório para quem está no código, não a plataforma para quem está no wiki. O mesmo vale para `.azuredevops/review-prompt.md`.

## 4. Convenções aplicadas

**Título da página.** Vem do primeiro `# H1` do documento, não do nome do arquivo. Assim `docs/deploy-bundles.md` aparece como "Implantação de Databricks Asset Bundles", sem exigir renomear arquivos no repositório. Crases e asteriscos são removidos do título; sublinhados ficam, porque fazem parte de identificadores como `vc_observability.pipeline_logging`.

**Nome do arquivo de página.** O Azure DevOps usa o nome do arquivo como título, e nele o hífen representa um espaço. O script converte o título em nome de arquivo trocando espaços por hífens, removendo `#` e trocando `/` e `\` por hífen, já que são proibidos. Nomes repetidos na mesma pasta recebem sufixo numérico.

**Página com subpáginas.** É um arquivo `.md` mais uma pasta de mesmo nome, no mesmo nível, como o Azure DevOps exige.

**Sequência das páginas.** Cada pasta recebe um `.order`. A ordem é a dos documentos da pasta primeiro, em ordem alfabética de arquivo de origem, e as soluções depois. Sem `.order` o wiki ordenaria alfabeticamente pelo título.

**Links.** Links relativos entre documentos publicados são reescritos para o caminho equivalente no wiki, inclusive quando atravessam origens — o link do README da observabilidade para `../../docs/deploy-bundles.md` passa a apontar para a página irmã correta. Links para arquivos de código passam a apontar para o navegador de arquivos do Azure Repos, na branch configurada, em vez de quebrarem. Links externos e âncoras não são alterados.

**Imagens.** O wiki só exibe imagem que esteja na sua pasta `/.attachments`; um link para o navegador do Azure Repos mostraria só o texto alternativo. Por isso, toda imagem referenciada com `![texto](caminho)` é copiada para `/.attachments`, com o caminho do repositório achatado no nome e o prefixo da subárvore (por exemplo, `Projetos-e-Iniciativas-Plataforma--solutions-observability-docs-diagramas-cenario5-completo.drawio.png`), e o link passa a apontar para lá. No repositório, a imagem continua referenciada pelo caminho relativo.

**Marca de gerado.** Toda página recebe um comentário HTML invisível indicando que foi gerada. O script se recusa a sobrescrever `<WIKI_ROOT>.md` se a página existir sem essa marca, para não destruir conteúdo escrito à mão; `WIKI_FORCE=true` ignora a recusa.

## 5. Permissões necessárias

A identidade que precisa de permissão é o **Build Service**, não o service principal usado nos outros estágios do pipeline (`sp-vc-dbx-cicd-prod`). São identidades diferentes: o service principal autentica no Azure e fala com a API do Databricks; o Build Service é a identidade interna do próprio Azure DevOps que executa o job, e é ela que `System.AccessToken` carrega para falar com a API do Azure DevOps — neste caso, para dar `git push` no repo do wiki.

| Identidade | Permissão | Onde |
| --- | --- | --- |
| Build Service do projeto | **Contribute** no repositório `VC-ANALYTICS.wiki` | Project Settings → Repos → Repositories → VC-ANALYTICS.wiki → Security |
| Job do pipeline | Acesso ao OAuth token | Já concedido pelo template, que passa `System.AccessToken` |

Na tela de Security do repositório, em **Users**, existe mais de uma identidade com "Build Service" no nome. A correta é:

```
VC-ANALYTICS Build Service (grupovotorantim)
```

nomeada pelo **projeto** do Azure DevOps (`VC-ANALYTICS`), não pelo repositório de código (`dbr-dataeng01-vcbr-platform`). Não selecione **Project Collection Build Service Accounts** — por padrão o escopo de autorização do job é por projeto, então é a identidade de projeto que precisa da permissão. Essa identidade só aparece na lista depois que o pipeline rodou ao menos uma vez.

Não é necessária Service Connection, nem token pessoal, nem secret em variável: a autenticação usa o token do build, enviado em cabeçalho a cada chamada do git e nunca gravado em disco.

Pré-requisito de uma vez só: o wiki do projeto precisa estar **provisionado**, o que cria o repositório `VC-ANALYTICS.wiki` e a branch `wikiMaster`. Se houver política de branch em `wikiMaster` exigindo pull request, o push do pipeline será recusado; nesse caso a política precisa permitir o Build Service ou ser removida dessa branch.

## 6. Execução manual

Fora do pipeline, com um clone do wiki acessível e o git autenticado na sua conta:

```bash
export WIKI_REPO_URL='https://grupovotorantim.visualstudio.com/VC-ANALYTICS/_git/VC-ANALYTICS.wiki'
export REPO_WEB_URL='https://grupovotorantim.visualstudio.com/VC-ANALYTICS/_git/dbr-dataeng01-vcbr-platform'
export DRY_RUN=true
bash .azuredevops/scripts/publish-wiki.sh
```

Com `DRY_RUN=true` o script clona, monta as páginas e mostra o diff, sem commit nem push. É a forma de conferir o efeito de uma alteração antes de mergear.

Para ver apenas a árvore que seria gerada, sem clonar nada:

```bash
python3 .azuredevops/scripts/build-wiki-tree.py --repo . --wiki /tmp/wiki-vazio \
  --root 'Projetos-e-Iniciativas/Plataforma' --dry-run
```

## 7. Limites e cuidados

**Limite de 235 caracteres.** O Azure DevOps limita o caminho completo da página, incluindo a URL do wiki, a 235 caracteres. Com a estrutura atual, a página mais longa chega a 212, ou seja, 23 caracteres de folga. O script avisa quando uma página passa de 205 e falha o passo quando passa de 235. Um H1 longo em uma solução de nome longo é o que consome essa folga; encurtar o H1 do documento resolve.

**O wiki é artefato gerado.** Editar pela interface do wiki uma página dentro da subárvore funciona até o próximo merge na `main`, quando a subárvore é reconstruída e a edição desaparece. Correção de documentação é alteração no repositório, por pull request, como o resto.

**Remoção.** Documento apagado do repositório tem a página removida do wiki na publicação seguinte, e a entrada correspondente sai do `.order`. As imagens geradas pela publicação anterior (as que têm o prefixo da subárvore) são apagadas e copiadas de novo; imagens anexadas à mão no wiki não são tocadas.

**Clone em Windows.** Os nomes de página são longos. Clonar o repositório do wiki em um caminho já profundo pode esbarrar no limite de 260 caracteres do Windows; `git config --global core.longpaths true` resolve.

**Títulos com barra.** `/` é proibido em nome de página e é convertido em hífen, que o wiki exibe como espaço. Por isso "Atividades de configuração de CI/CD" aparece como "Atividades de configuração de CI CD". Para exibir a barra seria preciso mudar o H1 do documento.

## 8. Diagnóstico

**Falha ao clonar o repo do wiki.** O wiki não foi provisionado, a branch não é `wikiMaster`, ou `VC-ANALYTICS Build Service (grupovotorantim)` não tem Contribute no repositório do wiki. O script imprime as três hipóteses na própria mensagem de erro.

**Falha ao empurrar.** Normalmente política de branch em `wikiMaster` exigindo pull request ou revisor.

**A página existe e não foi gerada por este script.** Já existe uma página `Plataforma` escrita à mão no wiki. Renomeie a página existente ou rode com `WIKI_FORCE=true`, ciente de que o conteúdo atual será substituído.

**Aviso de destino inexistente.** Um link relativo do documento aponta para um arquivo que não existe no repositório. O link é mantido como está; o aviso identifica o documento e o alvo.

**Nada a publicar.** A subárvore gerada é idêntica à publicada. É o resultado esperado quando o merge não alterou documentação.

## 9. Próximos passos

- Provisionar o wiki do projeto, se ainda não existir, e conceder Contribute a `VC-ANALYTICS Build Service (grupovotorantim)` (não ao service principal `sp-vc-dbx-cicd-prod`, nem ao Project Collection Build Service Accounts).
- Rodar uma vez com `DRY_RUN=true` a partir da branch, para conferir a árvore antes do primeiro merge.
- Avaliar publicar também a partir de `Develop`, em um `WIKI_ROOT` separado, se houver interesse em ver a documentação antes de chegar à `main`.
- Corrigir o H1 duplicado de `discussoes-tecnicas.md`, hoje "Discussões técnicas técnicas", que virou título de página.
