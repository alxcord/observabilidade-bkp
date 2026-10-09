# Métricas e critérios de aceite

## Indicadores

### Freshness

| Indicador | Cálculo |
|---|---|
| Conformidade | ativos dentro do SLA ÷ ativos avaliados × 100 |
| Atraso | horário real de disponibilidade − horário limite |
| Idade do dado | horário atual − última atualização |
| Freshness de negócio | horário de referência − maior data de negócio válida |
| Execuções esperadas ausentes | `MAX(0; esperadas − identificadas)` |

Usar a data de negócio quando o timestamp técnico não representar a atualidade real do conteúdo.

### Volume

| Indicador | Cálculo |
|---|---|
| Variação | (volume atual − referência) ÷ referência × 100 |
| Desvio absoluto | `ABS(volume atual − referência)` ÷ referência × 100 |
| Reconciliação | registros no destino ÷ registros na origem × 100 |
| Rejeição | registros rejeitados ÷ registros avaliados × 100 |
| Duplicidade | registros excedentes por chave ÷ registros avaliados × 100 |

O baseline pode ser execução anterior, média, mediana ou faixa específica. Ativos sazonais exigem calendário apropriado. `sizeInBytes` e `numFiles` medem evolução física e devem ser rotulados dessa forma.

### Schema

| Indicador | Cálculo |
|---|---|
| Conformidade | mudanças compatíveis ou aprovadas ÷ mudanças detectadas × 100 |
| Mudanças não autorizadas | contagem de mudanças sem aprovação |
| Cobertura de contrato | ativos com estrutura formalizada ÷ ativos no escopo × 100 |
| Tempo de detecção | horário da detecção − horário da mudança |

Sem mudança no período, conformidade é “não aplicável”, não zero.

### Data Quality

| Indicador | Cálculo |
|---|---|
| Aprovação de registros | aprovados ÷ avaliados × 100 |
| Aprovação de regras | regras aprovadas ÷ executadas × 100 |
| Quality Score | soma dos pesos aprovados ÷ soma dos pesos executados × 100 |
| Cobertura | ativos com regras ativas ÷ ativos no escopo × 100 |
| Nulidade | valores nulos ÷ avaliados × 100 |
| Validade | valores válidos ÷ avaliados × 100 |

Pesos iniciais: crítica 5, alta 3, média 2 e baixa 1.

### Lineage e governança

| Indicador | Cálculo |
|---|---|
| Cobertura mínima | ativos com origem e destino conhecidos ÷ ativos no escopo × 100 |
| Cobertura nativa | ativos com lineage nativo ÷ ativos no escopo × 100 |
| Cobertura de origem | ativos com origem identificada ÷ ativos no escopo × 100 |
| Cobertura downstream | ativos com consumidor identificado ÷ ativos no escopo × 100 |
| Cobertura de ownership | ativos com Data Owner ÷ ativos no escopo × 100 |

Toda cobertura deve ser segmentada por evidência nativa, coletada/correlacionada, declarada ou importada.

### Operação

| Indicador | Cálculo |
|---|---|
| Taxa de sucesso | execuções com sucesso ÷ concluídas × 100 |
| Taxa de falha | execuções com falha ÷ concluídas × 100 |
| Cumprimento de SLA | execuções no SLA ÷ avaliadas × 100 |
| Duração P95 | percentil 95 da duração |
| MTTD | soma(detecção − início do problema) ÷ incidentes |
| MTTR | soma(recuperação − detecção) ÷ incidentes |
| Reincidência | incidentes reincidentes ÷ incidentes totais × 100 |
| Cobertura de monitoramento | itens monitorados ÷ itens no escopo × 100 |

Execuções canceladas são exibidas separadamente e os denominadores permanecem consistentes no tempo.

## Data Health Score

```text
Data Health Score =
    Freshness × 0,25
  + Volume × 0,20
  + Schema × 0,15
  + Data Quality × 0,30
  + Lineage × 0,10
```

Calcular primeiro por ativo e depois agregar por produto e domínio. Valores não avaliados são ausência de cobertura, não zero. Ausência de dado em ativo crítico, regra crítica violada, schema incompatível, SLA crítico rompido ou produto executivo indisponível devem permanecer destacados independentemente da média. Pesos e agregações são experimentais até validação no piloto.

## Critérios de aceite

### ADF e notificações

- `PipelineRun`, `ActivityRun` e `TriggerRun` chegam ao Log Analytics pelo período definido.
- Uma falha controlada gera mensagem no Teams com domínio, recurso, pipeline, horário e link operacional.
- Falhas ADF não dependem do ciclo Databricks para notificação.

### Databricks e persistência

- A biblioteca corporativa resolve corretamente ambiente, view e catálogo.
- Somente workspaces retornados pela view VC são coletados.
- Inclusão ou remoção do workspace altera o próximo ciclo sem mudança de código e sem apagar histórico.
- Indisponibilidade da view falha de forma controlada.
- Jobs, tasks, tabelas e resultados são idempotentes e respeitam nomenclatura e grants corporativos.

### Correlação

- O UUID final é extraído sem interpretar outros fragmentos de `run_name`.
- A correlação funciona entre diferentes ADFs, retries, reruns, jobs multitarefa e execuções independentes.
- A taxa de correspondência é de pelo menos 95% nos casos elegíveis.
- Uma falha correlacionada continua visível e não gera segunda notificação Databricks.

### YAML e CI/CD

- YAML inválido, alias desconhecido, severidade ou escopo incompatível bloqueiam o pipeline.
- `plan` apresenta diferenças; `apply` é idempotente; drift é reportado.
- Recursos não gerenciados não são apagados e remoções exigem aprovação.
- Todos os arquivos `alerts/**/*.yaml` são descobertos e o mesmo commit chega aos dois destinos.
- O workflow Databricks lê as regras sincronizadas na Git Folder.

### Data Quality

- Expectations, DQM e SQL customizado produzem o mesmo contrato canônico.
- Erro de execução e reprovação de dados são estados independentes.
- Reingestão gera o mesmo `quality_result_id`; nova avaliação gera outro ID.
- `MERGE` não duplica registros; timestamps originais permanecem em UTC.
- Campos obrigatórios ausentes geram falha técnica do adapter.
- Registros de negócio inválidos não são copiados para Observability Tables.

### Freshness, volume e schema

- Todos os ativos monitorados retornam `lastModified`, `sizeInBytes`, `numFiles` e `createdAt`.
- O teste de produção mede consulta, materialização/escrita e inicialização separadamente.
- Reexecução não duplica estados; evolução física fica disponível para tendência.
- Dashboards não confundem volume físico com número de linhas.
- Ativos com manutenção separada podem acionar verificação complementar por `DESCRIBE HISTORY`.
- A coleta compartilha o workflow operacional e não cria outra inicialização por ciclo.
- Mudanças de coluna e tag são detectadas, classificadas e vinculadas aos consumidores conhecidos.

### Lineage

- Uma origem SAP/BW/Datasphere é ligada ao ativo raw como evidência declarada.
- O fluxo raw → cleansed → curated apresenta lineage nativo quando suportado.
- Um consumidor Power BI acessível é ligado ao ativo curated quando as APIs permitirem.
- Reprocessamento não duplica objetos ou relações; remoção respeita carência e ownership da integração.
- Falha temporária de API ou permissão não remove objetos.
- O lineage atualiza uma ou duas vezes ao dia e mantém trilha de auditoria.

### Dashboards e encerramento

- Atualização analítica em até 60 minutos.
- Drill-down ADF → Activity → Job → Task.
- Ativos e violações críticas não são ocultados por score médio.
- Eventos com notificação suprimida permanecem visíveis.
- Custo real, cobertura, latência, taxa de correlação, esforço de manutenção e limitações são apresentados no encerramento.
