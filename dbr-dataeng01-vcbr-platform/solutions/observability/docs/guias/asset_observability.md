# Guia de uso - asset_observability

Este guia mostra como colocar uma tabela sob monitoramento de **atualização** (freshness) e de **volume**, como o job decide se há problema e onde o alerta aparece. As regras completas estão na [especificação 20](../Especs/20-Databricks-asset_observability.md).

## 1. O que o job faz

O job `vc-observability-asset-observability` varre as tabelas Delta dos catálogos do ambiente (`dev_*` em desenvolvimento, `prd_*` em produção). Para cada tabela que declara uma frequência de atualização, ele responde duas perguntas no momento em que roda:

1. **A tabela foi atualizada dentro do prazo?** (freshness)
2. **O número de registros está dentro do esperado?** (volume)

Cada resposta vira um evento `PASS` ou `FAIL`. Um `FAIL` abre um incidente no hub e uma mensagem no Teams; o `PASS` seguinte fecha o incidente.

## 2. Frequência de atualização e SLA

São duas tags diferentes, e a diferença importa:

| Tag | O que diz | Exemplo |
|---|---|---|
| `vc_refresh_frequency` | De quanto em quanto tempo o dado **é** atualizado. É uma descrição do dado | `1d`: a carga roda uma vez por dia |
| `vc_freshness_sla` | Quanto atraso o negócio **aceita** antes de virar problema. É uma tolerância | `2d`: se perder uma carga, tudo bem; se perder duas, é incidente |

**Qual prazo o job usa.** Se a tabela tem `vc_freshness_sla`, o prazo é o SLA. Se não tem, o prazo é a própria frequência. A frequência continua sendo obrigatória: sem `vc_refresh_frequency`, o job só coleta a tabela e não avalia nada, mesmo que exista SLA.

**Por que separar.** Sem o SLA, quem quisesse tolerar um atraso acabaria mudando a frequência para um valor que não é verdade ("atualiza a cada 2 dias"), e a tag deixaria de descrever o dado. O SLA deixa a frequência correta e põe a tolerância no lugar certo.

**Cuidado com um SLA menor que a frequência.** Uma tabela diária (`1d`) com SLA de `1h` fica em `FAIL` quase o dia inteiro. O SLA deve ser igual ou maior que a frequência.

### Formato dos valores

O formato é `<número><unidade>`:

| Unidade | Significado | Exemplo |
|---|---|---|
| `min` | minutos | `5min`, `30min` |
| `h` | horas | `1h`, `6h` |
| `d` | dias | `1d`, `2d` |
| `w` | semanas | `1w` |
| `m` | **mês** (30 dias), não minuto | `1m` |
| `y` | ano (365 dias) | `1y` |

A taxonomia corporativa lista os valores `realtime`, `5min`, `1h`, `1d`, `1w` e `1m` ([modelo de tags](../conceitual/Tags.md)). O job não entende `realtime`, porque não há prazo para calcular: a tabela fica sem avaliação e o log da execução registra o erro. Um valor como `2d` é aceito pelo job; se a política da tag governada não aceitar o valor, o próprio `ALTER TABLE` recusa.

## 3. Como o atraso é calculado

```
atraso = instante da execução do job  −  última atualização da tabela
```

- **Última atualização** é o `lastModified` do `DESCRIBE DETAIL` da tabela, o instante do último commit Delta.
- **Instante da execução** é quando o job leu a tabela, em UTC.
- **`FAIL`** se o atraso for **maior** que o prazo. Atraso igual ao prazo ainda é `PASS`.

### Exemplo com a tabela do dólar

A tabela `prd_public.corp_cleansed.tbl_bacen_dolar_comercial` é carregada uma vez por dia, e o negócio aceita perder uma carga:

```sql
ALTER TABLE prd_public.corp_cleansed.tbl_bacen_dolar_comercial
SET TAGS ('vc_refresh_frequency' = '1d', 'vc_freshness_sla' = '2d');
```

Cada tag aparece uma vez no `SET TAGS`. Repetir a mesma chave (`'vc_refresh_frequency' = '2d', 'vc_refresh_frequency' = '1d'`) não declara SLA nenhum.

Suponha que a última carga foi na **segunda às 06:00** e o job roda às 10:00 todo dia:

| Execução do job | Atraso | Prazo (SLA `2d` = 48 h) | Resultado | Mensagem |
|---|---|---|---|---|
| Terça 10:00 | 28 h | 48 h | `PASS` | — |
| Quarta 10:00 | 52 h | 48 h | `FAIL` (`ASSET_STALE`) | "Atualizado há 2.2d; o prazo declarado é de 2.0d." |
| Quarta 14:00 (carga voltou às 12:00) | 2 h | 48 h | `PASS`, fecha o incidente | — |

Sem o SLA, o prazo seria a frequência (`1d` = 24 h), e a execução de **terça** já daria `FAIL`: "Atualizado há 28.0h; o prazo declarado é de 24.0h."

As durações na mensagem saem em minutos abaixo de 90 min, em horas com uma casa abaixo de 48 h, e em dias acima disso.

### O que a verificação pontual não vê

O job enxerga a tabela só quando roda. Isso vale para qualquer frequência:

- Um atraso que começou e terminou entre duas execuções não aparece.
- O incidente só fecha na execução seguinte, mesmo que a carga volte antes.
- Se o job de carga parou, o coletor também avisa a ausência de execução. São dois alertas verdadeiros para a mesma causa.

Para uma tabela horária, rodar o asset_observability com mais frequência (o ADF pode dispará-lo várias vezes ao dia) encurta o tempo até a detecção.

## 4. Como colocar uma tabela sob monitoramento

### Passo 1: tags na tabela

```sql
ALTER TABLE prd_public.corp_cleansed.tbl_bacen_dolar_comercial
SET TAGS (
  'vc_refresh_frequency' = '1d',
  'vc_freshness_sla'     = '2d',
  'vc_criticality'       = 'high'
);
```

- `vc_criticality` define a severidade do incidente: `critical` e `high` viram `HIGH`, `medium` vira `MEDIUM`, `low` vira `LOW`. Sem a tag, `MEDIUM`.

### Passo 2: domínio no schema

O domínio classifica o incidente nos relatórios e na mensagem. Ele costuma ficar no **schema**, e todas as tabelas herdam:

```sql
ALTER SCHEMA prd_public.corp_cleansed SET TAGS ('vc_data_domain' = 'finance');
```

Tag na tabela vence tag no schema, porque o mais específico manda. Sem domínio em lugar nenhum, o incidente fica com o domínio `DEFAULT`. Para onde o alerta vai não depende do domínio, e sim das regras de alerta (seção 6.3). A frequência e o SLA também podem ficar no schema, se valem para todas as tabelas dele.

### Passo 3: conferir o que o job vai ler

```sql
SELECT table_name, tag_name, tag_value
  FROM prd_public.information_schema.table_tags
 WHERE schema_name = 'corp_cleansed'
   AND table_name  = 'tbl_bacen_dolar_comercial';

SELECT tag_name, tag_value
  FROM prd_public.information_schema.schema_tags
 WHERE schema_name = 'corp_cleansed';
```

Para tirar o SLA e voltar a usar só a frequência:

```sql
ALTER TABLE prd_public.corp_cleansed.tbl_bacen_dolar_comercial UNSET TAGS ('vc_freshness_sla');
```

### Passo 4 (opcional): sobrescrever pelo YAML de ativos

Quando a tag ainda não pode ser aplicada (a tabela é de outra equipe, ou está em onboarding), o mesmo efeito vem do arquivo `solutions/observability/config/shared/assets/monitored-assets.yaml`, por pull request. O YAML vence a tag, campo a campo:

```yaml
assets:
  - name: prd_public.corp_cleansed.tbl_bacen_dolar_comercial
    vc_refresh_frequency: 1d
    vc_freshness_sla: 2d
    vc_criticality: high
    vc_data_domain: finance
```

O arquivo vai para o workspace no deploy do bundle. Depois do merge, a mudança só vale quando a esteira implantar.

## 5. Volume

Toda tabela com frequência declarada também tem o volume avaliado, com estas regras padrão:

| Regra | Padrão | Viola quando |
|---|---|---|
| `row_count_min` | 1 | A tabela tem menos linhas que o mínimo. Zero linhas gera `EMPTY_LOAD` |
| `row_count_max` | sem limite | A tabela tem mais linhas que o máximo |
| `variation_min_pct` | −40 | Caiu mais de 40% em relação à leitura anterior |
| `variation_max_pct` | +40 | Cresceu mais de 40% em relação à leitura anterior |

As regras valem juntas: basta uma estourar para dar `FAIL` (`VOLUME_OUT_OF_RANGE`). Na primeira leitura de uma tabela não há variação a calcular, e só os limites absolutos valem.

Para mudar os limites, declare o bloco `volume` no YAML de ativos:

```yaml
assets:
  - name: prd_public.corp_cleansed.tbl_bacen_dolar_comercial
    volume:
      variation_min_pct: -5      # cotação diária: a tabela só cresce
      variation_max_pct: 10
      row_count_min: 1000
      exceptions:
        - reason: expurgo mensal de histórico
          days_of_month: [1]
          variation_min_pct: -30  # no dia 1, aceita queda de até 30%
```

A exceção **afrouxa** a regra nos dias listados, nunca a desliga. Exemplos de mensagem: "A tabela está vazia.", "variação de -50.0% abaixo do mínimo de -5.0%".

## 6. Onde o alerta aparece

```
asset_observability ──▶ staging Delta ──▶ hub-publisher ──▶ hub (Azure SQL) ──▶ coletor ──▶ Teams
   (quando roda)        (na hora)        (de hora em hora,   (incidente         (a cada      (mensagem)
                                          no minuto 0)        OPEN)              15 min)
                                                                │
                                                                └──▶ Power BI e API
```

Do fim da execução do job até a mensagem no Teams, o tempo máximo é de cerca de 1 h 15 min: a espera até o próximo minuto 0 do hub-publisher, mais até 15 minutos do ciclo do coletor.

### 6.1 Na staging do Databricks (imediato)

Ao fim de cada schema varrido, o evento é gravado em `<gov>.corp_cleansed.tbl_dbr_it_observability_events` (`prd_governance` em produção). `sent_at` nulo significa que o hub-publisher ainda não enviou.

```sql
SELECT source, result, error_code, description, observed_at, sent_at
  FROM prd_governance.corp_cleansed.tbl_dbr_it_observability_events
 WHERE natural_key = 'prd_public.corp_cleansed.tbl_bacen_dolar_comercial'
 ORDER BY observed_at DESC;
```

A leitura que gerou o evento fica em `tbl_dbr_it_asset_observability`:

```sql
SELECT updated_at, observed_at,
       timestampdiff(MINUTE, updated_at, observed_at) / 60.0 AS atraso_horas,
       row_count
  FROM prd_governance.corp_cleansed.tbl_dbr_it_asset_observability
 WHERE asset = 'prd_public.corp_cleansed.tbl_bacen_dolar_comercial'
 ORDER BY observed_at DESC;
```

As colunas das duas tabelas estão em [Tabelas-Databricks.md](../Tabelas-Databricks.md).

### 6.2 No hub (em até 1 hora)

O hub-publisher roda de hora em hora, no minuto 0, e entrega o evento ao hub. Um `FAIL` sem incidente aberto **abre** um incidente com a chave `freshness:<tabela>` (ou `volume:<tabela>`). Os `FAIL` seguintes do mesmo problema só somam ocorrências, sem nova mensagem. O `PASS` seguinte **fecha** o incidente. Um `PASS` sem incidente aberto não faz nada.

### 6.3 No Teams (em até 15 minutos depois)

O coletor escolhe o destino pela regra de alerta mais específica que casa com a tabela, o tipo de falha (`asset`) e a severidade. As regras ficam em `tbl_cfg_alert_rules` e são mantidas pela `observability-api`: por exemplo, uma regra para `prd_public` manda tudo daquele catálogo para um destino, e uma regra para `prd_public.corp_cleansed.tbl_bacen_dolar_comercial` vence a do catálogo para essa tabela. A regra pode ter vários destinos ([especificação 01](../Especs/01-Hub-Azure-SQL.md), seção 2.6). Para conferir antes de mudar, use `GET /api/config/alert-rules/resolve?object=<tabela>&failure_type=asset&severity=<severidade>`. O título segue o formato `[ABERTO] <origem> — <domínio> — <chave>`, por exemplo `[ABERTO] FRESHNESS — finance — ...tbl_bacen_dolar_comercial`, com a cor da severidade e a descrição do evento ("Atualizado há 2.2d; o prazo declarado é de 2.0d."). Quando o incidente fecha, chega a mensagem `[RESOLVIDO]`.

### 6.4 No Power BI e na API

O painel do Power BI lê os incidentes abertos (`vw_open_problems`) e os fechados hoje e ontem (`vw_recently_closed_problems`). A `observability-api` devolve os mesmos dados em `GET /api/problems` e `GET /api/problems/closed`, com filtro opcional `domain` ([especificação 30](../Especs/30-Azure-Functions-observability-api.md)).

## 7. Quando a tabela não gera evento

| Sintoma | Causa provável | Onde conferir |
|---|---|---|
| Nenhum evento da tabela | Sem `vc_refresh_frequency`, ou com valor fora do formato (como `realtime`) | Passo 3 da seção 4; o log da execução mostra "frequencia ... nao reconhecida" |
| A tabela nem aparece em `tbl_dbr_it_asset_observability` | Não é Delta `MANAGED`/`EXTERNAL` (view, streaming table), é tabela interna de pipeline, ou o catálogo não começa com o prefixo do ambiente | `information_schema.tables` |
| Linha com `updated_at` nulo | O job não conseguiu ler a tabela (caminho de storage inexistente, sem permissão) | Aviso de cobertura no log da execução |
| Evento na staging com `sent_at` nulo há mais de 1 hora | O hub-publisher não está enviando | Execuções do Container Apps Job `hub-publisher` |
| Incidente aberto e nenhuma mensagem no Teams | Regra sem destino válido, destino inativo ou webhook errado no Key Vault | `GET /api/config/alert-rules/resolve`, `tbl_notification_deliveries` (`last_error`) |

A última linha do log de cada execução resume a cobertura: quantas tabelas foram coletadas, lidas e avaliadas.
