# Discussões técnicas


## Como obter a


%md
## Recomendação: `DESCRIBE DETAIL`

Para capturar a data/hora da última atualização de um grupo de tabelas, a abordagem recomendada é usar `DESCRIBE DETAIL` iterando por cada tabela.

### Por quê?
- **Leve e rápido**: retorna apenas uma linha de metadados por tabela, sem varrer o histórico completo de transações.
- **Campo `lastModified`**: timestamp UTC da última transação registrada no Delta log.

### ⚠️ Limitação importante do `lastModified`

O campo `lastModified` reflete o timestamp do **último commit no Delta log**, o que inclui:

| Operação | Atualiza `lastModified`? | É modificação de dados? |
| --- | --- | --- |
| INSERT / MERGE / UPDATE / DELETE | Sim | Sim |
| OPTIMIZE | **Sim** | Não (apenas compacta arquivos) |
| VACUUM | **Sim** (DBR 13.3+) | Não (apenas remove arquivos antigos) |
| ALTER TABLE SET TBLPROPERTIES | Sim | Não |
| RESTORE | Sim | Depende |

**Conclusão**: se a tabela sofrer OPTIMIZE periódico, o `lastModified` não representa necessariamente a última vez que dados novos chegaram. Para ambientes com OPTIMIZE agendado, considere filtrar operações no `DESCRIBE HISTORY`.

Temos também algumas métricas que podem ajudar a acompanhar a avolução do tamanho da tabela.


---

### Alternativas (e por que são piores)

#### 1. `DESCRIBE HISTORY tabela LIMIT 1`
- **Prós**: permite filtrar por tipo de operação (ex: `operation IN ('WRITE', 'MERGE', 'DELETE')`) para pegar apenas modificações reais de dados.
- **Contras**: muito mais pesado — carrega metadados do log transacional, parseia JSON com métricas. Em 187 tabelas, a diferença de tempo é significativa.
- **Quando usar**: quando você precisa distinguir entre OPTIMIZE e escrita real de dados.

#### 2. `system.information_schema.tables.last_altered`
- **Prós**: consulta única para todas as tabelas de um schema, sem loop.
- **Contras**: `last_altered` reflete apenas **alterações de estrutura** (DDL como ALTER TABLE, ADD COLUMN). **NÃO** rastreia INSERT/UPDATE/DELETE. Inútil para monitorar freshness de dados.

#### 3. `system.information_schema.table_storage` (Delta metadata)
- **Prós**: metadados de storage em batch.
- **Contras**: não expõe timestamp de última modificação de dados de forma confiável.

#### 4. Ler diretamente o Delta log (`_delta_log/`)
- **Prós**: controle total, pode filtrar por tipo de ação.
- **Contras**: complexidade alta, frágil a mudanças de formato, não recomendado para produção.

---

### Resumo

| Critério | DESCRIBE DETAIL | DESCRIBE HISTORY | information_schema |
| --- | --- | --- | --- |
| Velocidade | Rápido | Lento | Muito rápido |
| Reflete escrita de dados | Sim (com ressalva de OPTIMIZE) | Sim (filtrável) | Não |
| Granularidade | Timestamp único | Histórico completo | Apenas DDL |
| Complexidade | Baixa | Média | Baixa |

**Para observabilidade geral de freshness**: `DESCRIBE DETAIL` é o melhor equilíbrio entre velocidade e acurácia. Se OPTIMIZE/VACUUM rodar em horários conhecidos e separados das cargas de dados, o resultado será confiável.

No caso, são raros os pipelines que tem optimize e vacuum em processos separados, o impacto disso é minimo podemos ignorar esse detalhe em favor da performance e poder monitorar mais tabelas.


```python
from pyspark.sql import functions as F
from datetime import datetime

schema = "prd_logistics.corp_cleansed"

# Filtrar apenas tabelas e materialized views (excluir views)
tabelas_df = spark.sql(f"""
    SELECT table_catalog || '.' || table_schema || '.' || table_name AS full_name
    FROM prd_logistics.information_schema.tables
    WHERE table_schema = 'corp_cleansed'
      AND table_type IN ('MANAGED', 'MANAGED_SHALLOW_CLONE', 'EXTERNAL')
""")
tabelas = [row.full_name for row in tabelas_df.collect()]

# Medir tempo de execução
ts_inicio = datetime.now()

resultado = None
erros = []

for t in tabelas:
    try:
        df = spark.sql(f"DESCRIBE DETAIL {t}").select(
            F.lit(t).alias("tabela"),
            F.col("lastModified"),
            F.col("sizeInBytes"),
            F.col("numFiles"),
            # F.col("description"),
            F.col("createdAt")
        )
        resultado = df if resultado is None else resultado.union(df)
    except Exception as e:
        erros.append((t, str(e)))


if resultado:
    resultado = resultado.withColumn("sizeInMB", F.round(F.col("sizeInBytes") / 1024 / 1024, 2))
    resultado.orderBy("lastModified", ascending=False).display()


ts_fim = datetime.now()
duracao = (ts_fim - ts_inicio).total_seconds()

# Resultados
print(f"Início:     {ts_inicio.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Fim:        {ts_fim.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Duração:    {duracao:.2f} segundos")
print(f"Tabelas medidas: {len(tabelas) - len(erros)}")
print(f"Erros:      {len(erros)}")
print("-" * 50)

if erros:
    print(f"\n⚠️ Tabelas com erro:")
    for t, e in erros:
        print(f"  - {t}: {e}")
```

|tabela|lastModified|sizeInBytes|numFiles|createdAt|sizeInMB|
|---|---|---|---|---|---|
|prd_logistics.corp_cleansed.tbl_ds_log_caminhoes|2026-08-27T20:42:38.000Z|1215954496|13|2026-08-27T20:13:57.686Z|1159.62|
|prd_logistics.corp_cleansed.tbl_ds_log_condicao_precos|2026-08-27T20:28:07.000Z|1801151177|15|2026-08-27T20:08:37.660Z|1717.71|
|prd_logistics.corp_cleansed.tbl_ds_log_caminhoes_log_status|2026-08-27T20:15:44.000Z|0|0|2026-08-27T20:15:37.258Z|0|
|prd_logistics.corp_cleansed.five9_centrais_remotas_pesquisa_nps|2026-08-27T20:11:06.000Z|65154098|5578|2025-10-08T18:11:12.465Z|62.14|
|prd_logistics.corp_cleansed.five9_centrais_remotas_telefone_0800|2026-08-27T20:10:30.000Z|32891544|3137|2025-10-08T18:11:20.639Z|31.37|
|prd_logistics.corp_cleansed.five9_centrais_remotas_whatsapp|2026-08-27T20:10:08.000Z|35705721|161|2025-10-08T18:11:39.359Z|34.05|
|... continua ... |2025-08-04T19:28:25.000Z|201635|1|2025-05-28T12:30:38.878Z|0.19|
|prd_logistics.corp_cleansed.tbl_relatorio_pendencias_qvd|2025-08-04T19:28:09.000Z|92074106|1|2025-05-28T12:24:30.885Z|87.81|
|prd_logistics.corp_cleansed.tbl_relatorio_meta_otif|2025-08-04T19:24:22.000Z|2651949|1|2025-05-30T19:30:40.065Z|2.53|
|prd_logistics.corp_cleansed.tbl_ods_ordem_vendas_existe|2025-08-04T19:22:24.000Z|12592054|1|2025-05-28T12:26:07.443Z|12.01|
|prd_logistics.corp_cleansed.tbl_metas_pcp_otif|2025-08-04T19:22:07.000Z|11695|1|2025-05-28T12:33:34.217Z|0.01|
|prd_logistics.corp_cleansed.tbl_meta_pais|2025-08-04T19:21:30.000Z|1333|1|2025-05-30T19:34:17.363Z|0|
|prd_logistics.corp_cleansed.tbl_grade_cif|2025-08-04T19:21:04.000Z|9808|1|2025-07-01T20:44:10.347Z|0.01|
|prd_logistics.corp_cleansed.tbl_feriado_nacional|2025-08-04T19:19:23.000Z|2180|1|2025-07-01T13:45:09.254Z|0|
|prd_logistics.corp_cleansed.tbl_faixas_roterizador|2025-08-04T19:19:09.000Z|132458|1|2025-07-14T20:21:02.832Z|0.13|
|prd_logistics.corp_cleansed.tbl_dim_produto|2025-08-04T19:12:46.000Z|1271|1|2025-05-30T19:33:24.417Z|0|
|prd_logistics.corp_cleansed.tbl_centro_totem|2025-08-04T19:12:05.000Z|6730856|1|2025-05-28T12:40:22.101Z|6.42|

```Text
Início:     2026-08-27 20:51:16
Fim:        2026-08-27 20:51:57
Duração:    41.33 segundos
Tabelas medidas: 181
Erros:      0
--------------------------------------------------
```
