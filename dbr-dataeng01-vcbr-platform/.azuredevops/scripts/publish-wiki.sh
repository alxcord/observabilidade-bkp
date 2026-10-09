#!/usr/bin/env bash
# publish-wiki.sh
# Publica a documentacao do repositorio no wiki do projeto.
#
# O script clona o repo do wiki provisionado, reconstroi a subarvore
# administrada com build-wiki-tree.py e empurra o resultado. Documento
# removido do repositorio desaparece do wiki; nada fora da subarvore e tocado.
#
# A subarvore e definida por WIKI_ROOT, no formato de pastas do wiki, onde o
# hifen representa espaco no titulo da pagina. O padrao,
# "Projetos-e-Iniciativas/Plataforma", publica sob a pagina
# "Projetos e Iniciativas > Plataforma".
#
# Requisitos:
#   - Wiki provisionado no projeto (repo <Projeto>.wiki, branch wikiMaster)
#   - Build Service com Contribute no repo do wiki
#   - Opcao "Allow scripts to access the OAuth token" ativa no job
#
# Variaveis de entrada:
#   WIKI_REPO_URL      (obrigatoria) URL do repo do wiki, ex.: https://.../_git/VC-ANALYTICS.wiki
#   WIKI_BRANCH        branch do wiki (padrao: wikiMaster)
#   WIKI_ROOT          subarvore administrada (padrao: Projetos-e-Iniciativas/Plataforma)
#   REPO_WEB_URL       URL do repo de codigo, usada nos links para arquivos de codigo
#   REPO_BRANCH        branch usada nesses links (padrao: main)
#   WIKI_FORCE         true para sobrescrever pagina existente sem a marca de gerado
#   DRY_RUN            true para montar e mostrar o diff, sem commit nem push
#   DEBUG_MODE         true para log detalhado
#   SYSTEM_ACCESSTOKEN token do build, usado na autenticacao do git

set -euo pipefail

WIKI_REPO_URL="${WIKI_REPO_URL:?WIKI_REPO_URL nao definida}"
WIKI_BRANCH="${WIKI_BRANCH:-wikiMaster}"
WIKI_ROOT="${WIKI_ROOT:-Projetos-e-Iniciativas/Plataforma}"
REPO_WEB_URL="${REPO_WEB_URL:-}"
REPO_BRANCH="${REPO_BRANCH:-main}"
WIKI_FORCE="${WIKI_FORCE:-false}"
DRY_RUN="${DRY_RUN:-false}"
DEBUG_MODE="${DEBUG_MODE:-false}"

log_debug() {
  if [[ "${DEBUG_MODE,,}" == "true" ]]; then
    echo "[debug] $*"
  fi
}

warn() {
  echo "##vso[task.logissue type=warning]$*"
}

cd "$(git rev-parse --show-toplevel)"
REPO_ROOT="$(pwd)"
COMMIT="$(git rev-parse HEAD)"
COMMIT_CURTO="$(git rev-parse --short HEAD)"

echo "Repositorio de codigo: ${REPO_ROOT}"
echo "Commit de origem: ${COMMIT}"
echo "Wiki: ${WIKI_REPO_URL} (branch ${WIKI_BRANCH})"
echo "Subarvore administrada: ${WIKI_ROOT}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "##vso[task.logissue type=error]python3 nao encontrado no agente."
  exit 1
fi
echo "Interprete: $(python3 --version 2>&1)"

# ------------------------------------------------------------------
# Clone do wiki
# ------------------------------------------------------------------
CLONE="$(mktemp -d)/wiki"
echo "##[group]Clone do wiki"

# O cabecalho de autorizacao vai em -c, sem gravar nada no disco e sem
# aparecer no log. Nao usamos a URL com token embutido por isso mesmo.
AUTH=()
if [[ -n "${SYSTEM_ACCESSTOKEN:-}" ]]; then
  AUTH=(-c "http.extraheader=AUTHORIZATION: bearer ${SYSTEM_ACCESSTOKEN}")
else
  warn "SYSTEM_ACCESSTOKEN ausente. O clone vai depender da credencial do ambiente."
fi

if ! git "${AUTH[@]+"${AUTH[@]}"}" clone --quiet --depth 1 --branch "${WIKI_BRANCH}" \
     "${WIKI_REPO_URL}" "${CLONE}"; then
  echo "##vso[task.logissue type=error]Falha ao clonar o repo do wiki."
  echo "Confirme que o wiki do projeto foi provisionado, que a branch ${WIKI_BRANCH} existe"
  echo "e que o Build Service tem Contribute nesse repo."
  exit 1
fi
echo "Clone em: ${CLONE}"
log_debug "$(cd "${CLONE}" && git log --oneline -1)"
echo "##[endgroup]"

# ------------------------------------------------------------------
# Montagem da arvore de paginas
# ------------------------------------------------------------------
echo "##[group]Montagem das paginas"
ARGS=(
  --repo "${REPO_ROOT}"
  --wiki "${CLONE}"
  --root "${WIKI_ROOT}"
  --repo-branch "${REPO_BRANCH}"
)
[[ -n "${REPO_WEB_URL}" ]] && ARGS+=(--repo-url "${REPO_WEB_URL}")
[[ "${WIKI_FORCE,,}" == "true" ]] && ARGS+=(--force)

python3 .azuredevops/scripts/build-wiki-tree.py "${ARGS[@]}"
echo "##[endgroup]"

# ------------------------------------------------------------------
# Commit e push
# ------------------------------------------------------------------
cd "${CLONE}"
git add -A

if git diff --cached --quiet; then
  echo "Wiki já está em dia com o commit ${COMMIT_CURTO}. Nada a publicar."
  exit 0
fi

echo "##[group]Alteracoes a publicar"
git --no-pager diff --cached --stat
echo "##[endgroup]"

if [[ "${DRY_RUN,,}" == "true" ]]; then
  echo "DRY_RUN ativo. Commit e push nao executados."
  exit 0
fi

git -c user.email="noreply@vcimentos.com" \
    -c user.name="Azure DevOps CI" \
    commit --quiet -m "docs: publica documentacao do commit ${COMMIT_CURTO}" \
    -m "Gerado por .azuredevops/scripts/publish-wiki.sh a partir de ${COMMIT}."

if ! git "${AUTH[@]+"${AUTH[@]}"}" push --quiet origin "HEAD:${WIKI_BRANCH}"; then
  echo "##vso[task.logissue type=error]Falha ao empurrar para o wiki."
  echo "Verifique Contribute do Build Service no repo do wiki e politicas na branch ${WIKI_BRANCH}."
  exit 1
fi

echo "Wiki publicado a partir do commit ${COMMIT_CURTO}."
