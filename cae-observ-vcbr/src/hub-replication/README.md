# hub-replication

Container Apps Job que replica o hub (Azure SQL) para Delta, de hora em
hora. Sem ele, `tbl_ctl_replication.replicated_watermark` nunca avança, e a
purga do hub (documento §10) trava para sempre — as duas condições dela
são idade **e** replicação, nunca só idade.

## Por que roda aqui, não como job Databricks

Faz o inverso do
[`hub-publisher`](../hub-publisher/): em vez de escrever no hub, lê o hub.
Ler o Azure SQL **de dentro do Databricks** por JDBC exigiria o compute do
Databricks alcançar o Private Endpoint do banco — e um workspace **sem
VNet injection** roda esse compute numa VNet gerenciada pela Microsoft,
sem nenhuma rota possível até lá. Não é configuração faltando, é
impossibilidade estrutural. É o mesmo problema que já motiva os jobs de
Data Quality e dos pilares nunca escreverem direto no hub (`databricks/README.md`)
— e o mesmo problema que motivou tirar `hub_publisher` de dentro do
Databricks também (era o único dos jobs Databricks que atravessava a rede,
por HTTPS a uma API; ver `src/hub-publisher/README.md`).

A saída usa a mesma assimetria que já sustenta `sync-expectations`: as
APIs REST do Databricks (Files API, Statement Execution API) são HTTPS
públicas, alcançáveis de qualquer lugar, **porque quem inicia a conexão é
sempre o lado de fora** — nunca o compute do Databricks. Este job:

1. lê o Azure SQL por `pyodbc` + Managed Identity — mesmo padrão do
   coletor e do sync-expectations, já resolvido;
2. grava o lote em CSV;
3. sobe o CSV para um Volume do Unity Catalog pela **Files API**;
4. executa `COPY INTO` pela **Statement Execution API**, contra um SQL
   Warehouse.

## Pré-requisito que este repositório não provisiona

Um **SQL Warehouse serverless**, com **auto-stop curto** (5–10 min), e um
**Volume do Unity Catalog** em `prd_governance.corp_raw` para pouso do CSV
(`constants.STAGING_VOLUME`). Nenhum dos dois é recurso do Azure — são
recursos do Databricks, fora do escopo do Bicep deste projeto, do mesmo
jeito que `tbl_cfg_workspaces` referencia workspaces sem este repositório
provisioná-los.

**Serverless, e não classic/pro, importa para o custo, não para a rede.**
A rede já está resolvida pelo desenho acima, independente do tipo de
warehouse. Serverless com auto-stop é o que faz o job custar só os minutos
em que efetivamente recebe o `COPY INTO`, em vez de manter compute ligado
o resto do tempo — mas de hora em hora (`15 * * * *`, ver
`infra/params.json`) o warehouse liga ~24×/dia em vez de 1×/dia. Com um
lote pequeno por execução, o custo por wake-up passa a pesar mais do que
pesava na cadência diária original. **Não medido contra o warehouse
real** — é o primeiro número que vale medir antes de considerar esta
cadência definitiva.

## Idempotência

O nome do arquivo de staging é **determinístico** por tabela e por
watermark anterior — nunca aleatório. Um retry após falha parcial (upload
subiu, `COPY INTO` falhou, processo caiu no meio) relê exatamente as
mesmas linhas do Azure SQL, porque o watermark só avança depois de tudo
confirmado; e `COPY INTO` não recarrega um arquivo que já processou. As
duas garantias juntas: retry nunca duplica linha em Delta.

## Testes

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Usa stub de `pyodbc`, `httpx` e `azure-identity`: roda sem banco, sem rede
e sem workspace.
