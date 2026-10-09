# Publicação de módulos Python compartilhados

Os módulos usados por mais de uma solução devem possuir uma única fonte de verdade e ser distribuídos como pacotes Python versionados. Eles não devem ser copiados entre repositórios nem consumidos por caminhos físicos do workspace como prática permanente.

## Decisão

`vc_dbr_shared_functions` continuará sendo uma biblioteca corporativa independente. Durante a transição, consumidores legados ainda podem depender de `/Workspace/Repos/VC-ANALYTICS/dbr-dataeng01-vcbr/modules/vc_dbr_shared_functions`, mas novos desenvolvimentos devem importar um pacote instalado. O caminho do workspace é uma compatibilidade temporária, não uma interface pública da biblioteca.

O repositório destinado às bibliotecas compartilhadas deve concentrar o código, os testes, a documentação e o processo de publicação. Quando a migração começar, o módulo deve ser movido para esse repositório, preservando seu histórico quando possível. Uma cópia não deve permanecer ativa no repositório antigo depois que todos os consumidores forem migrados.

## Estrutura do pacote

Uma biblioteca deve seguir o padrão de pacote Python com código em `src`:

```text
vc-dbr-shared-functions/
├── README.md
├── pyproject.toml
├── src/
│   └── vc_dbr_shared_functions/
│       ├── __init__.py
│       └── ...
└── tests/
```

O nome da distribuição pode usar hífens, como `vc-dbr-shared-functions`, enquanto o namespace importado pelo Python utiliza sublinhados:

```python
from vc_dbr_shared_functions import get_environment
```

O `pyproject.toml` deve declarar metadados, versão mínima do Python, dependências de execução e configuração de build. A biblioteca não deve depender implicitamente de notebooks ou de alterações manuais em `sys.path`.

## Versionamento

As versões devem seguir Semantic Versioning. Correções compatíveis incrementam a versão de patch, novas funcionalidades compatíveis incrementam a versão minor e mudanças incompatíveis incrementam a versão major. Cada publicação deve ser imutável; uma versão existente não deve ser sobrescrita com conteúdo diferente.

Os consumidores devem fixar uma versão aprovada, por exemplo:

```toml
dependencies = [
  "vc-dbr-shared-functions==1.2.0"
]
```

Evite dependências sem versão, intervalos excessivamente amplos ou instalação automática da versão mais recente em produção.

## Processo de publicação

O pipeline da biblioteca executa testes, valida o pacote, gera o wheel e publica o mesmo artefato para consumo. O arquivo produzido terá um nome semelhante a `vc_dbr_shared_functions-1.2.0-py3-none-any.whl`.

O Azure Artifacts pode funcionar como registro corporativo e fonte oficial das versões. Para execução no Databricks, o wheel também pode ser promovido para um Unity Catalog Volume de cada ambiente. A promoção deve reutilizar exatamente o artefato testado; não se deve reconstruir um wheel diferente para produção.

Um fluxo esperado é:

```text
Pull Request
    ↓
testes e análise estática
    ↓
build do wheel
    ↓
publicação da versão no Azure Artifacts
    ↓
promoção do mesmo wheel para DEV
    ↓
aprovação e promoção do mesmo wheel para PROD
```

A identidade do pipeline de publicação precisa de permissão para publicar no feed ou gravar no Volume de destino. Os jobs consumidores precisam apenas de leitura. Tokens, senhas e chaves não devem ser incluídos no pacote nem no YAML; devem ser fornecidos por Service Connections, identidade federada ou secret scopes.

## Consumo por soluções e bundles

Para desenvolvimento e testes locais, a dependência pode ser resolvida pelo índice Python corporativo. No Databricks, um job pode instalar o wheel publicado em um Unity Catalog Volume:

```yaml
resources:
  jobs:
    observability_job:
      tasks:
        - task_key: process
          libraries:
            - whl: /Volumes/<catalogo>/<schema>/<volume>/vc_dbr_shared_functions-1.2.0-py3-none-any.whl
```

O catálogo, schema, volume e versão devem ser parametrizados pelo bundle quando variarem entre ambientes. O código consumidor continua usando imports Python normais e não precisa conhecer o caminho do repositório que originou a biblioteca.

## Migração do modelo atual

A migração começa pelo inventário dos consumidores de `vc_dbr_shared_functions`, incluindo `%run`, alterações em `sys.path` e imports dependentes do Git folder. Em seguida, o módulo atual é empacotado sem mudanças funcionais relevantes, testado e publicado em uma primeira versão controlada.

Cada consumidor passa a declarar a dependência, instalar o wheel e remover a referência ao caminho `/Workspace/Repos`. A migração deve ocorrer primeiro em DEV e depois em produção. O código antigo só deve ser removido quando não houver consumidores restantes e existir um plano de retorno para a versão anterior do pacote.

## Regra para revisão de código

Novas PRs não devem duplicar funções da biblioteca, reimplementar APIs corporativas sem justificativa ou adicionar dependências por caminho absoluto do workspace. O esperado é um import do pacote, uma versão declarada e a instalação explícita pelo ambiente de execução. Exceções temporárias devem registrar o motivo, o responsável e a condição para remoção.
