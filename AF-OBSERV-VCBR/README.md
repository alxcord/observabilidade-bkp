# AF-OBSERV-VCBR

Azure Function `observability-api`: API HTTP do hub operacional de observabilidade e data quality. Encapsula o Azure SQL — nenhum consumidor abre conexão direta com o banco — e expõe:

- consulta de incidentes abertos e recém-fechados;
- CRUD das tabelas de configuração (workspaces, rotas de alerta, expectativas de execução e mapeamento de domínio).

A API não grava incidentes nem execuções. Isso é feito pelo coletor e pelo `hub-publisher`, que ficam no repositório `cae-observ-vcbr` (`src/observability-collector` e `src/hub-publisher`; documentação em `doc/10-observability-collector.md` e `doc/12-hub-publisher.md`).

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `src/` | Código da Function (Python 3.11, modelo v2): `function_app.py` (rotas), `auth.py`, `db.py`, `repository.py`, `resources.py`, `constants.py`, `host.json` |
| `src/tests/` | Testes unitários, com stubs de `pyodbc` e `azure-identity` (rodam sem banco e sem Azure) |
| `src/infra/` | Bicep e parâmetros da infraestrutura da Function, aplicados pela esteira a cada deploy |
| `src/azure-pipelines.yml` | Esteira: test → build (zip) → deploy dev → deploy prod, com aprovação em prod |
| `doc/` | Documentação: [observability-api.md](doc/observability-api.md) (especificação funcional, configuração, permissões, implantação e pendências) |

Detalhes de rotas, autorização e como acrescentar um recurso estão em [src/README.md](src/README.md).

## Build e testes

```bash
cd src
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/
```

## Deploy

A esteira em `src/azure-pipelines.yml` dispara em alterações sob `src/` na `main`. Cada estágio de deploy aplica o Bicep de `src/infra/` e publica o mesmo pacote zip, sem reempacotar entre ambientes. As pendências que bloqueiam o primeiro deploy estão na seção 9 de [doc/observability-api.md](doc/observability-api.md).
