"""Resolve um evento observado — decide abrir, incrementar ou fechar um
incidente (documento §4.3).

**Não é stored procedure.** Não há `CREATE PROCEDURE` em lugar nenhum: cada
chamada envia um punhado de comandos SQL parametrizados dentro de uma
transação controlada por esta função Python, e a decisão de abrir,
incrementar ou fechar mora aqui, não em T-SQL. O que continua em SQL — o
`SELECT ... WITH (UPDLOCK, HOLDLOCK)` e o índice único filtrado que a
violação em `_retry_after_lost_race` trata — são coisas que só fazem
sentido dentro do banco (travar uma linha, arbitrar uma corrida por
constraint); não há como nem por que reescrever isso em Python puro sem
reintroduzir o *check-then-act* que o índice único existe para eliminar.

**Compartilhado entre os projetos deste repositório** que decidem estado
de incidente (hoje o coletor, `src/observability-collector`, e o
publicador, `src/hub-publisher`) — de propósito, e não por economia de
linhas: a lógica não pode ser duplicada entre eles, porque duplicação é o
caminho conhecido para as implementações divergirem em silêncio (o mesmo
raciocínio que já motivava a versão em stored procedure, só que agora a
peça compartilhada é este arquivo, não um objeto do catálogo do banco).
Cada projeto importa este módulo copiando-o para dentro de si no momento
do build (Dockerfile) — nunca duplicando o texto. A Azure Function da API
vive em outro repositório e não consome este módulo.

`correlation_ref` é o identificador da execução observada quando ele existe
(hoje, o `ActivityRunId` do ADF). Ele NÃO decide estado nem correlação: só é
gravado no evento filho, para ligar depois o evento do ADF e o do Databricks
da mesma execução.

Contrato: o chamador informa o que OBSERVOU (`result = 'FAIL'` ou
`'PASS'`), nunca `incident_status`. Quem decide abertura e fechamento é
esta função — inclusive para chamadas vindas de fora da esteira, de modo que um
token comprometido consiga mentir sobre um ativo, mas não manipular a
máquina de estado.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pyodbc

VALID_RESULTS = ("FAIL", "PASS")

ACTION_ABERTO = "ABERTO"
ACTION_INCREMENTADO = "INCREMENTADO"
ACTION_RESOLVIDO = "RESOLVIDO"
ACTION_IGNORADO = "IGNORADO"
ACTION_DUPLICADO = "DUPLICADO"

#: Violação de índice/constraint único no driver ODBC do SQL Server — os
#: mesmos códigos que `repository.py` já trata em `create()` para as
#: tabelas `tbl_cfg_*`. Aqui é o índice único filtrado `ux_problems_open`
#: que dispara: dois ciclos abrindo o mesmo incidente entre o SELECT e o
#: INSERT.
_DUPLICATE_KEY_ERROR_CODES = ("2627", "2601")


def resolve_event(
    conn: pyodbc.Connection,
    *,
    source: str,
    result: str,
    natural_key: str,
    key_rule: str = "JOB_ID",
    correlation_ref: str | None = None,
    scope: str | None = None,
    workspace_id: str | None = None,
    severity: str = "MEDIUM",
    error_code: str | None = None,
    error_description: str | None = None,
    run_id: str | None = None,
    source_event_id: str | None = None,
    observed_at: datetime | None = None,
) -> tuple[str | None, str]:
    """Devolve `(problem_id, action)` — `action` é um dos `ACTION_*` acima."""
    if result not in VALID_RESULTS:
        raise ValueError(f"result deve ser um de {VALID_RESULTS}, veio {result!r}")

    cursor = conn.cursor()

    if cursor.execute(
        "SELECT 1 FROM tbl_ref_source WHERE source = ? AND active = 1", source
    ).fetchone() is None:
        raise ValueError(f"origem desconhecida ou inativa em tbl_ref_source: {source}")

    if observed_at is None:
        observed_at = datetime.now(timezone.utc)

    # -----------------------------------------------------------------
    # 1. Idempotência — o mesmo evento reenviado não produz efeito novo.
    #    Necessário porque o push do hub_publisher (§5.6) é at-least-once:
    #    um timeout de rede após o commit faz o produtor reenviar a mesma
    #    linha no ciclo seguinte.
    # -----------------------------------------------------------------
    if source_event_id is not None:
        row = cursor.execute(
            "SELECT problem_id FROM tbl_technical_events"
            " WHERE source = ? AND source_event_id = ?",
            source, source_event_id,
        ).fetchone()
        if row is not None:
            return str(row.problem_id), ACTION_DUPLICADO

    # -----------------------------------------------------------------
    # 2. Chave de correlação — identifica o INCIDENTE, não a origem do
    #    sinal, e é SEMPRE derivada da `natural_key`, que é estável entre
    #    execuções do mesmo processo. É essa estabilidade que permite ao
    #    `PASS` da execução seguinte fechar o incidente.
    #
    #    Até 2026-10 a correlação de um evento de ADF era o `ActivityRunId`
    #    da execução, para que o lado ADF e o lado Databricks da mesma
    #    execução caíssem num incidente só. O efeito colateral era grave: o
    #    ADF gera um `ActivityRunId` novo a cada execução, então o `PASS`
    #    seguinte caía em outra correlação, era ignorado, e TODO incidente
    #    originado no ADF ficava aberto até alguém fechar à mão. Um
    #    incidente que não fecha sozinho é pior que um alerta a mais: ele
    #    envenena o painel inteiro. O identificador da execução passou a ser
    #    gravado em `correlation_ref` no evento filho, que é o que liga as
    #    duas origens depois, sem decidir estado.
    # -----------------------------------------------------------------
    if source == "ADF":
        correlation_key = f"adf:{natural_key}"
    else:
        correlation_key = f"{source}:{natural_key}"
    correlation_key = correlation_key.lower()

    # -----------------------------------------------------------------
    # 3. Domínio — resolvido na resolução, não na notificação (§4). Sem
    #    correspondência, cai em 'DEFAULT', que tem rota garantida em
    #    tbl_cfg_alert_routing.
    # -----------------------------------------------------------------
    domain_row = cursor.execute(
        "SELECT TOP 1 domain FROM tbl_cfg_domain_scope"
        " WHERE active = 1 AND source = ? AND scope = ?"
        " ORDER BY scope",
        source, scope or workspace_id or "",
    ).fetchone()
    domain = domain_row.domain if domain_row else "DEFAULT"

    try:
        problem_id, action = _apply(
            cursor,
            correlation_key=correlation_key, source=source, result=result,
            natural_key=natural_key, key_rule=key_rule, workspace_id=workspace_id,
            domain=domain, severity=severity, error_code=error_code,
            error_description=error_description, run_id=run_id,
            source_event_id=source_event_id, correlation_ref=correlation_ref,
            observed_at=observed_at,
        )
    except pyodbc.IntegrityError as error:
        conn.rollback()
        if not any(code in str(error) for code in _DUPLICATE_KEY_ERROR_CODES):
            raise
        # Outro ciclo abriu o mesmo incidente entre o nosso SELECT e o
        # INSERT — corrida perdida, não erro (§4.3). Sem este tratamento, o
        # índice único apenas converteria alerta duplicado em exceção não
        # tratada, o que não é melhor.
        return _retry_after_lost_race(
            cursor, conn,
            correlation_key=correlation_key, source=source, error_code=error_code,
            error_description=error_description, run_id=run_id,
            natural_key=natural_key, source_event_id=source_event_id,
            correlation_ref=correlation_ref, observed_at=observed_at,
        )
    else:
        conn.commit()
        return problem_id, action


def _apply(
    cursor: pyodbc.Cursor,
    *,
    correlation_key: str,
    source: str,
    result: str,
    natural_key: str,
    key_rule: str,
    workspace_id: str | None,
    domain: str,
    severity: str,
    error_code: str | None,
    error_description: str | None,
    run_id: str | None,
    source_event_id: str | None,
    correlation_ref: str | None,
    observed_at: datetime,
) -> tuple[str | None, str]:
    """A transição de estado propriamente dita — dentro da transação aberta
    por `resolve_event`. `WITH (UPDLOCK, HOLDLOCK)` serializa ciclos
    concorrentes na mesma correlation_key; o índice único filtrado
    (`ux_problems_open`, `schema_definition.py` do hub-schema-init) continua sendo a
    garantia final, tratada em `resolve_event` no CATCH. Trava e índice se
    cobrem: a trava evita o trabalho perdido, o índice evita o dado
    errado."""
    existing = cursor.execute(
        "SELECT problem_id FROM tbl_problems WITH (UPDLOCK, HOLDLOCK)"
        " WHERE correlation_key = ? AND incident_status = 'OPEN'",
        correlation_key,
    ).fetchone()
    existing_id = str(existing.problem_id) if existing else None

    if result == "FAIL":
        if existing_id is not None:
            # Recorrência: mesmo incidente, mais uma ocorrência. Não
            # notifica de novo — é o que evita o alerta repetido a cada
            # ciclo enquanto o job segue falhando. A severidade só sobe,
            # nunca desce, para não mascarar agravamento.
            cursor.execute(
                "UPDATE tbl_problems"
                "   SET occurrence_count = occurrence_count + 1,"
                "       severity = CASE WHEN ? = 'HIGH' THEN 'HIGH' ELSE severity END,"
                "       error_code = COALESCE(?, error_code),"
                "       error_description = COALESCE(?, error_description)"
                " WHERE problem_id = ?",
                severity, error_code, error_description, existing_id,
            )
            problem_id, action = existing_id, ACTION_INCREMENTADO
        else:
            row = cursor.execute(
                "INSERT INTO tbl_problems (source, correlation_key, natural_key,"
                "    key_rule, workspace_id, domain, incident_status, severity,"
                "    error_code, error_description, opening_run_id, opened_at,"
                "    occurrence_count)"
                " OUTPUT inserted.problem_id"
                " VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, 1)",
                source, correlation_key, natural_key, key_rule, workspace_id,
                domain, severity, error_code, error_description, run_id,
                observed_at,
            ).fetchone()
            problem_id = str(row.problem_id)

            # Intenção de notificar gravada na mesma transação (§9 —
            # outbox): a entrega é de uma etapa separada.
            cursor.execute(
                "INSERT INTO tbl_notification_outbox (problem_id, type)"
                " VALUES (?, 'OPEN')",
                problem_id,
            )
            action = ACTION_ABERTO

        cursor.execute(
            "INSERT INTO tbl_technical_events (problem_id, source, error_code,"
            "    error_description, run_id, natural_key, source_event_id,"
            "    correlation_ref, registered_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            problem_id, source, error_code, error_description, run_id,
            natural_key, source_event_id, correlation_ref, observed_at,
        )
        return problem_id, action

    # result == "PASS": sucesso sem incidente aberto é o caso normal —
    # nada a fazer.
    if existing_id is None:
        return None, ACTION_IGNORADO

    cursor.execute(
        "UPDATE tbl_problems"
        "   SET incident_status = 'RESOLVED', closed_at = ?, closing_run_id = ?"
        " WHERE problem_id = ?",
        observed_at, run_id, existing_id,
    )
    cursor.execute(
        "INSERT INTO tbl_notification_outbox (problem_id, type) VALUES (?, 'CLOSE')",
        existing_id,
    )
    cursor.execute(
        "INSERT INTO tbl_technical_events (problem_id, source, error_code,"
        "    error_description, run_id, natural_key, source_event_id,"
        "    correlation_ref, registered_at)"
        " VALUES (?, ?, 'RECUPERADO', ?, ?, ?, ?, ?, ?)",
        existing_id, source, error_description, run_id, natural_key,
        source_event_id, correlation_ref, observed_at,
    )
    return existing_id, ACTION_RESOLVIDO


def _retry_after_lost_race(
    cursor: pyodbc.Cursor,
    conn: pyodbc.Connection,
    *,
    correlation_key: str,
    source: str,
    error_code: str | None,
    error_description: str | None,
    run_id: str | None,
    natural_key: str,
    source_event_id: str | None,
    correlation_ref: str | None,
    observed_at: datetime,
) -> tuple[str, str]:
    """Reentra pelo caminho de incremento depois de perder a corrida do
    índice único — é este bloco que substitui de fato o *check-then-act*:
    sem ele, o índice só transformaria alerta duplicado em exceção não
    tratada, o que não é melhor."""
    winner = cursor.execute(
        "SELECT problem_id FROM tbl_problems"
        " WHERE correlation_key = ? AND incident_status = 'OPEN'",
        correlation_key,
    ).fetchone()
    if winner is None:
        # Não deveria acontecer — a violação veio do mesmo índice que filtra
        # por incident_status = 'OPEN' —, mas subir um erro claro é melhor
        # que fingir sucesso se algum dia acontecer.
        raise RuntimeError(
            "violação de índice único em tbl_problems sem incidente OPEN "
            f"correspondente para correlation_key={correlation_key!r}"
        )
    winner_id = str(winner.problem_id)

    cursor.execute(
        "UPDATE tbl_problems SET occurrence_count = occurrence_count + 1"
        " WHERE problem_id = ?",
        winner_id,
    )
    cursor.execute(
        "INSERT INTO tbl_technical_events (problem_id, source, error_code,"
        "    error_description, run_id, natural_key, source_event_id,"
        "    correlation_ref, registered_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        winner_id, source, error_code, error_description, run_id,
        natural_key, source_event_id, correlation_ref, observed_at,
    )
    conn.commit()
    return winner_id, ACTION_INCREMENTADO
