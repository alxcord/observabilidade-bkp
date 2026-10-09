# API do hub operacional

Encapsula o Azure SQL. **Nenhum consumidor abre conexão com o banco** — o
que dá um ponto único para autorização, validação e evolução de schema, e
evita que uma alteração de tabela quebre quem consome.

**Não faz mais ingestão.** Até 2026-09 havia `POST /api/events`, chamada
pelo job Databricks `hub_publisher` para escrever no hub. Esse caminho foi
substituído pelo `hub-publisher` (repositório `cae-observ-vcbr`, em `src/hub-publisher` (documentação em `doc/12-hub-publisher.md`)), que roda fora do Databricks e escreve no
Azure SQL direto por `pyodbc`, sem API HTTP no meio.

## Rotas

| Método | Rota | Escopo |
|---|---|---|
| `GET` | `/api/problems` | leitura |
| `GET` | `/api/problems/closed` | leitura |
| `GET` | `/api/config/{resource}` | leitura |
| `GET` | `/api/config/{resource}/{key}` | leitura |
| `POST` | `/api/config/{resource}` | escrita |
| `PATCH` | `/api/config/{resource}/{key}` | escrita |
| `DELETE` | `/api/config/{resource}/{key}` | escrita |

`/api/problems` traz o que está aberto agora (`vw_open_problems`).
`/api/problems/closed` traz o que fechou hoje e ontem
(`vw_recently_closed_problems`) — pergunta de rotina de quem acompanha o
turno, que a primeira rota nunca respondia por conter só `OPEN`.

Recursos: `workspaces`, `alert-routes`, `expectations`, `domains`,
`integration-runtimes`.

Chave composta vai na URL separada por `~`, na ordem declarada em
`resources.py`:

```bash
curl -H "Authorization: Bearer $TOKEN_READ" \
  https://<app>.azurewebsites.net/api/config/alert-routes/Logistics~HIGH~teams
```

### Self-Hosted Integration Runtime

`integration-runtimes` diz quais SHIR o coletor acompanha e quantos nós
espera em cada um. IR que não está na tabela não é monitorado, e o número
de nós nunca é fixo no código do coletor — sai daqui.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN_WRITE" \
  -H "Content-Type: application/json" \
  -d '{"data_factory":"adf-analytics-vcbr","integration_runtime":"shir-onprem-01","expected_nodes":2}' \
  https://<app>.azurewebsites.net/api/config/integration-runtimes
```

Só o ADF **dono** do SHIR entra. Linked IR, que é o mesmo SHIR
compartilhado com outro ADF, não se cadastra: o sinal vem do ADF dono, e
cadastrar os dois geraria incidente duplicado.

Para tirar um IR do monitoramento sem perder o cadastro, `active = 0`:

```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN_WRITE" \
  -H "Content-Type: application/json" -d '{"active": 0}' \
  https://<app>.azurewebsites.net/api/config/integration-runtimes/adf-analytics-vcbr~shir-onprem-01
```

### Campos que a API decide

`expectations` grava `config_source = MANUAL` sozinha, e recusa o campo se
ele vier no corpo. Não é capricho: com `AUTO` ou `YAML` a linha passaria a
ser gerenciada pelo sincronizador de expectativas, que a sobrescreveria na
execução seguinte sem avisar quem cadastrou.

## O que NÃO tem CRUD, e por quê

`tbl_problems`, `tbl_executions` e `tbl_technical_events` são **estado operacional**.
Quem escreve neles é o coletor (`cae-observ-vcbr`, `src/observability-collector`) e o `hub-publisher`, nunca esta API. Expor um `PUT` ali
permitiria alterar um incidente por fora da máquina de estado — fechar um
incidente sem que a condição tenha se resolvido, por exemplo. É o mesmo
motivo pelo qual a antiga rota de ingestão nunca decidia estado.

**Não há stored procedure no banco**, e esta API não contém lógica de
resolução de eventos.

## Deploy

Esteira em `azure-pipelines.yml`: não há imagem nem Azure Container
Registry — o plano Flex Consumption publica um pacote de código. Estágios:
test → build (empacota um zip) → deploy dev → deploy prod,
com approval gate em prod. Cada estágio de deploy aplica o Bicep de
`infra/` (idempotente) e só então publica o mesmo pacote via
`az functionapp deploy --type zip` — sem reempacotar entre ambientes.

## Autorização

Dois tokens com escopos separados — quem lê não escreve. Os riscos são de
tamanhos diferentes: um token de leitura vazado expõe a lista de
incidentes; um de escrita permite injetar incidente falso ou fechar
incidente real.

Os valores vêm do Key Vault por referência, nas variáveis `TOKEN_READ` e
`TOKEN_WRITE`. **Sem token configurado, ninguém entra** — falha fechada,
para que um erro de deploy não vire API aberta.

**Bearer estático não expira sozinho.** A rota de escrita precisa de
restrição de rede por allowlist da rede corporativa — não mais dos IPs de
saída do Databricks: sem a rota de ingestão, nada aqui é chamado pelo
Databricks. Isso é requisito, não recomendação. Migrar para Entra ID com
client credentials é a evolução natural.

## Banco

Conexão por Managed Identity — sem usuário e senha em lugar nenhum. O token
entra pelo atributo do driver ODBC.

**Nenhum valor entra por interpolação.** Nomes de tabela e coluna vêm da
descrição em `resources.py`, que é código nosso; valores vão sempre como
parâmetro.

## Acrescentar um recurso

Uma entrada em `resources.py`. O repositório e as rotas não mudam:

```python
"new-resource": Resource(
    name="new-resource",
    table="tbl_cfg_new_table",
    key=("id",),
    columns=("id", "description", "quantity", "active"),
    required=("id", "description"),
    # Valor que a API define e o cliente não envia.
    fixed={"origin": c.SOME_POLICY},
    # Validação de valor, além da presença exigida em `required`.
    checks={"description": preenchido, "quantity": minimo(c.SOME_MINIMUM)},
),
```

`fixed` e `checks` são opcionais. Os limites e valores vêm de
`constants.py`, nunca escritos direto na entrada: é o mesmo número que o
teste lê para saber o que esperar.

Só tabelas `tbl_cfg_*`. Há um teste que falha se alguém expuser outra
coisa.

## Testes

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/
```

Mesmo comando da esteira — o que roda na sua máquina é o que roda no build.
Os casos são escritos com `unittest.TestCase`, que o `pytest` coleta sem
configuração.

Usam stub de `pyodbc`, `azure-identity` e `azure-functions`: rodam sem banco
e sem Azure.
