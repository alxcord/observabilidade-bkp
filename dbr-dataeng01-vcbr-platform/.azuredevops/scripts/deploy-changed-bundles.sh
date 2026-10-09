#!/usr/bin/env bash
# deploy-changed-bundles.sh
# Detecta os Databricks Asset Bundles afetados pelas alteracoes desta execucao
# e roda validate + deploy em cada um.
#
# Regra de deteccao:
#   Para cada arquivo alterado, sobe a arvore de pastas a partir da pasta do
#   arquivo ate a raiz do repositorio. A primeira pasta com databricks.yml
#   (ou databricks.yaml) e a raiz do bundle. Arquivos fora de bundles sao ignorados.
#   Exemplo: a/b/c.py -> procura em a/b, depois em a, depois na raiz.
#
# Base do diff (primeira opcao valida):
#   1. BASE_SHA informado manualmente
#   2. Commit do ultimo build com sucesso deste pipeline na mesma branch
#   3. Primeiro pai do HEAD (HEAD^1), cobre merge commit e squash
#   4. Sem pai (primeiro commit): todos os bundles do repositorio
#
# Testes: antes do validate/deploy de cada bundle, roda os testes automatizados
#   dele. Cada pasta "tests" com test_*.py dentro do bundle e executada com
#   pytest em um venv proprio, instalando requirements.txt e
#   requirements-dev.txt da pasta pai (e o pacote, se ela tiver pyproject.toml).
#   Se algum teste falha, o bundle NAO e implantado; os demais seguem.
#
# Variaveis de entrada:
#   BUNDLE_TARGET   (obrigatoria) target do databricks.yml, ex.: dev, prd
#   FORCE_ALL       true para implantar todos os bundles do repositorio
#   BASE_SHA        commit base manual (opcional)
#   DRY_RUN         true para listar os bundles sem executar o CLI
#   DEBUG_MODE      true para log detalhado e --debug no Databricks CLI
#   TEST_RESULTS_DIR pasta dos relatorios JUnit (padrao: ./test-results)
#   SYSTEM_ACCESSTOKEN e variaveis predefinidas do Azure DevOps para consultar o ultimo build

set -euo pipefail

TARGET="${BUNDLE_TARGET:?BUNDLE_TARGET nao definido}"
FORCE_ALL="${FORCE_ALL:-false}"
BASE_SHA="${BASE_SHA:-}"
BASE_SHA="${BASE_SHA//[[:space:]]/}"
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
HEAD_SHA="$(git rev-parse HEAD)"
echo "HEAD: ${HEAD_SHA}"
echo "Target: ${TARGET}"

# ------------------------------------------------------------------
# Base do diff
# ------------------------------------------------------------------
is_valid_base() {
  local sha="$1"
  [[ -n "${sha}" ]] || return 1
  git cat-file -e "${sha}^{commit}" 2>/dev/null || return 1
  # Se houve force push e o commit saiu do historico, a base nao serve
  git merge-base --is-ancestor "${sha}" "${HEAD_SHA}" 2>/dev/null || return 1
  return 0
}

# Preenche LAST_BUILD_SHA (variavel global, sem subshell para os logs do Azure DevOps funcionarem)
LAST_BUILD_SHA=""
last_successful_build_sha() {
  if [[ -z "${SYSTEM_ACCESSTOKEN:-}" || -z "${SYSTEM_DEFINITIONID:-}" || -z "${BUILD_SOURCEBRANCH:-}" ]]; then
    log_debug "Contexto do Azure DevOps ausente, sem consulta ao ultimo build."
    return 0
  fi

  local url="${SYSTEM_TEAMFOUNDATIONCOLLECTIONURI%/}/${SYSTEM_TEAMPROJECTID}/_apis/build/builds"
  local response
  response="$(mktemp)"

  local http_code
  http_code=$(curl --silent --show-error --get \
    --output "${response}" --write-out '%{http_code}' \
    --header "Authorization: Bearer ${SYSTEM_ACCESSTOKEN}" \
    --data-urlencode "definitions=${SYSTEM_DEFINITIONID}" \
    --data-urlencode "branchName=${BUILD_SOURCEBRANCH}" \
    --data-urlencode "resultFilter=succeeded" \
    --data-urlencode "statusFilter=completed" \
    --data-urlencode "queryOrder=finishTimeDescending" \
    --data-urlencode "\$top=1" \
    --data-urlencode "api-version=7.1" \
    "${url}") || http_code="000"

  log_debug "Consulta ultimo build: HTTP ${http_code}"

  if [[ "${http_code}" == "200" ]]; then
    LAST_BUILD_SHA=$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
v=d.get("value") or []
print(v[0].get("sourceVersion","") if v else "")' "${response}")
    log_debug "Commit do ultimo build com sucesso: ${LAST_BUILD_SHA:-nenhum}"
  else
    warn "Nao foi possivel consultar o ultimo build com sucesso (HTTP ${http_code})."
  fi
  rm -f "${response}"
}

BASE=""
BASE_ORIGIN=""

if [[ "${FORCE_ALL,,}" != "true" ]]; then
  if is_valid_base "${BASE_SHA:-}"; then
    BASE="${BASE_SHA}"
    BASE_ORIGIN="BASE_SHA informado"
  else
    last_successful_build_sha
    if is_valid_base "${LAST_BUILD_SHA}"; then
      BASE="${LAST_BUILD_SHA}"
      BASE_ORIGIN="ultimo build com sucesso"
    elif git rev-parse --verify --quiet "${HEAD_SHA}^1" >/dev/null; then
      if [[ -n "${LAST_BUILD_SHA}" ]]; then
        warn "Commit do ultimo build (${LAST_BUILD_SHA}) fora do historico atual. Usando HEAD^1."
      fi
      BASE="$(git rev-parse "${HEAD_SHA}^1")"
      BASE_ORIGIN="HEAD^1 (fallback)"
    else
      warn "HEAD sem commit pai. Todos os bundles serao implantados."
      FORCE_ALL="true"
    fi
  fi
fi

# ------------------------------------------------------------------
# Descoberta dos bundles
# ------------------------------------------------------------------
declare -A BUNDLES=()   # raiz do bundle -> 1
declare -A CACHE=()     # pasta -> raiz do bundle ("" quando nao ha)

# Preenche FOUND_ROOT (variavel global, mantem o cache entre chamadas)
FOUND_ROOT=""
find_bundle_root() {
  local dir="$1"
  local visited=()
  local root=""

  while :; do
    if [[ -n "${CACHE[$dir]+x}" ]]; then
      root="${CACHE[$dir]}"
      break
    fi
    visited+=("${dir}")
    if [[ -f "${dir}/databricks.yml" || -f "${dir}/databricks.yaml" ]]; then
      root="${dir}"
      break
    fi
    [[ "${dir}" == "." ]] && break
    dir="$(dirname "${dir}")"
  done

  local v
  for v in "${visited[@]}"; do
    CACHE["$v"]="${root}"
  done
  FOUND_ROOT="${root}"
}

if [[ "${FORCE_ALL,,}" == "true" ]]; then
  echo "Modo: todos os bundles do repositorio"
  while IFS= read -r -d '' f; do
    BUNDLES["$(dirname "$f")"]=1
    echo "  manifesto: ${f}"
  done < <(git ls-files -z | grep -zE '(^|/)databricks\.ya?ml$' || true)
else
  echo "Base do diff: ${BASE} (${BASE_ORIGIN})"
  # --no-renames: arquivo renomeado aparece como remocao + inclusao,
  # assim os bundles de origem e de destino entram na lista
  changed=0
  while IFS= read -r -d '' f; do
    changed=$((changed + 1))
    find_bundle_root "$(dirname "$f")"
    root="${FOUND_ROOT}"
    if [[ -n "${root}" ]]; then
      BUNDLES["${root}"]=1
      echo "  ${f} -> bundle ${root}"
    else
      if [[ ! -e "$f" && "$(basename "$f")" =~ ^databricks\.ya?ml$ ]]; then
        warn "Bundle removido: $(dirname "$f"). O deploy nao remove recursos. Avalie 'databricks bundle destroy' manual."
      fi
      echo "  ${f} -> fora de bundle, ignorado"
    fi
  done < <(git diff --name-only --no-renames -z "${BASE}" "${HEAD_SHA}")
  echo "Arquivos alterados: ${changed}"
fi

if [[ ${#BUNDLES[@]} -eq 0 ]]; then
  echo "Nenhum bundle afetado. Nada a implantar."
  exit 0
fi

mapfile -t ORDERED < <(printf '%s\n' "${!BUNDLES[@]}" | sort)

echo "Bundles a implantar (${#ORDERED[@]}):"
printf '  - %s\n' "${ORDERED[@]}"

if [[ "${DRY_RUN,,}" == "true" ]]; then
  echo "DRY_RUN ativo. CLI nao executado."
  exit 0
fi

# ------------------------------------------------------------------
# Testes automatizados
# ------------------------------------------------------------------
REPO_ROOT="$(pwd)"
TEST_RESULTS_DIR="${TEST_RESULTS_DIR:-${REPO_ROOT}/test-results}"
mkdir -p "${TEST_RESULTS_DIR}"

# Roda os testes de um bundle. Retorna 0 se passaram (ou se nao ha testes).
run_bundle_tests() {
  local bundle="$1"
  local tests_dirs=()
  local d

  while IFS= read -r -d '' d; do
    tests_dirs+=("${d}")
  done < <(find "${bundle}" \( -name .venv -o -name build -o -name node_modules -o -name .git \) -prune -o -type d -name tests -print0 | sort -z)

  echo "Pastas 'tests' encontradas em ${bundle}: ${#tests_dirs[@]}"
  if [[ ${#tests_dirs[@]} -gt 0 ]]; then
    printf '  - %s\n' "${tests_dirs[@]}"
  fi

  local failed=0
  local suites=0
  for d in "${tests_dirs[@]+"${tests_dirs[@]}"}"; do
    if ! compgen -G "${d}/test_*.py" >/dev/null; then
      echo "  ${d}: nenhum arquivo test_*.py, pasta ignorada"
      continue
    fi

    local project slug venv py count
    project="$(dirname "${d}")"
    slug="$(echo "${d}" | tr '/ ' '__')"
    venv="$(mktemp -d)/venv"
    count="$(compgen -G "${d}/test_*.py" | wc -l)"
    suites=$((suites + 1))

    echo "##[group]Testes: ${d} (${count} arquivo(s) test_*.py)"
    compgen -G "${d}/test_*.py" | sed 's/^/  arquivo: /'
    echo "Projeto (pasta de execucao do pytest): ${project}"
    echo "Relatorio JUnit: ${TEST_RESULTS_DIR}/${slug}.xml"
    echo "Ambiente virtual: ${venv}"
    echo "Interprete: $(python3 --version 2>&1)"

    if ! python3 -m venv "${venv}"; then
      echo "##vso[task.logissue type=error]Falha ao criar o ambiente virtual dos testes de ${d}"
      failed=1
      echo "##[endgroup]"
      continue
    fi
    if [[ -x "${venv}/bin/python" ]]; then py="${venv}/bin/python"; else py="${venv}/Scripts/python"; fi

    local args=(pytest)
    if [[ -f "${project}/requirements.txt" ]]; then
      args+=(-r "${project}/requirements.txt")
      echo "Dependencias: ${project}/requirements.txt"
    fi
    if [[ -f "${project}/requirements-dev.txt" ]]; then
      args+=(-r "${project}/requirements-dev.txt")
      echo "Dependencias de desenvolvimento: ${project}/requirements-dev.txt"
    fi
    if [[ -f "${project}/pyproject.toml" ]]; then
      args+=(-e "${project}")
      echo "Pacote instalado em modo editavel: ${project}/pyproject.toml"
    fi

    echo "pip install ${args[*]}"
    if ! "${py}" -m pip install --disable-pip-version-check "${args[@]}"; then
      echo "##vso[task.logissue type=error]Falha ao instalar dependencias dos testes de ${d}"
      failed=1
      echo "##[endgroup]"
      continue
    fi

    if ( cd "${project}" && "${py}" -m pytest "${d##*/}" -v -ra \
           --junitxml="${TEST_RESULTS_DIR}/${slug}.xml" ); then
      echo "Testes OK: ${d}"
    else
      echo "##vso[task.logissue type=error]Testes falharam em ${d}"
      failed=1
    fi
    echo "##[endgroup]"
  done

  echo "Suites de teste executadas em ${bundle}: ${suites}"
  return "${failed}"
}

# ------------------------------------------------------------------
# Validate + deploy
# ------------------------------------------------------------------
if ! command -v databricks >/dev/null 2>&1; then
  echo "Instalando Databricks CLI..."
  curl --fail --silent --show-error --location \
    https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
fi
databricks version

# --debug no CLI quando DEBUG_MODE estiver ativo
CLI_FLAGS=()
if [[ "${DEBUG_MODE,,}" == "true" ]]; then
  CLI_FLAGS+=(--debug)
fi

FAILED=()
TESTED=0
DEPLOYED=0
for b in "${ORDERED[@]}"; do
  manifest="${b}/databricks.yml"
  [[ -f "${manifest}" ]] || manifest="${b}/databricks.yaml"

  echo "##[section]Bundle ${b} (target ${TARGET})"
  echo "Manifesto: ${manifest}"
  echo "Nome declarado: $(sed -n 's/^[[:space:]]*name:[[:space:]]*//p' "${manifest}" | head -1)"

  # Testes antes do deploy: bundle com teste falhando nao e implantado,
  # os demais seguem.
  if run_bundle_tests "${b}"; then
    TESTED=$((TESTED + 1))
  else
    echo "##vso[task.logissue type=error]Testes falharam. Bundle ${b} NAO sera implantado."
    FAILED+=("${b}")
    continue
  fi

  echo "##[group]Validate + deploy: ${b}"
  if ( cd "${b}" \
       && databricks bundle validate --target "${TARGET}" "${CLI_FLAGS[@]+"${CLI_FLAGS[@]}"}" \
       && databricks bundle deploy --target "${TARGET}" --auto-approve "${CLI_FLAGS[@]+"${CLI_FLAGS[@]}"}" ); then
    echo "OK: ${b}"
    DEPLOYED=$((DEPLOYED + 1))
    # Resumo dos recursos implantados e suas URLs. Falha aqui nao invalida
    # o deploy que ja foi concluido.
    ( cd "${b}" && databricks bundle summary --target "${TARGET}" ) \
      || warn "Nao foi possivel obter o resumo do bundle ${b}."
  else
    echo "##vso[task.logissue type=error]Falha no bundle ${b}"
    FAILED+=("${b}")
  fi
  echo "##[endgroup]"
done

echo "Resumo: ${#ORDERED[@]} bundle(s) avaliado(s), ${TESTED} com testes aprovados, ${DEPLOYED} implantado(s)."

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "Bundles com falha:"
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi

echo "Todos os bundles implantados com sucesso."
