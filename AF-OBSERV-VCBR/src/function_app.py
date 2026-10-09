"""API do hub operacional.

Encapsula o Azure SQL: nenhum consumidor abre conexão com o banco. Isso dá
um ponto único para autorização, validação e evolução de schema — e evita
que uma alteração de tabela quebre quem consome.

**Não faz mais ingestão.** Até 2026-09 esta API tinha uma rota `POST
/events`, chamada pelo job Databricks `hub_publisher` para escrever no hub.
Esse caminho foi substituído pelo `hub-publisher`, que roda fora do
Databricks e escreve no Azure SQL direto por `pyodbc`, sem API HTTP no meio.

Rotas:

    GET    /api/problems                   incidentes abertos (leitura)
    GET    /api/problems/closed            fechados hoje e ontem (leitura)

    GET    /api/config/{resource}          lista        (leitura)
    GET    /api/config/{resource}/{key}    uma linha    (leitura)
    POST   /api/config/{resource}          cria         (escrita)
    PATCH  /api/config/{resource}/{key}    atualiza     (escrita)
    DELETE /api/config/{resource}/{key}    remove       (escrita)

Recursos de configuração: workspaces, alert-routes, expectations, domains,
integration-runtimes.

Chave composta vai na URL separada por `~`, na ordem declarada em
`resources.py`. Ex.: `/api/config/alert-routes/Logistics~HIGH~teams`.

`tbl_problems`, `tbl_executions` e `tbl_technical_events` **não** têm CRUD. São estado
operacional: quem escreve neles é o `hub-publisher` e o coletor, e um PUT ali
permitiria alterar incidente por fora da máquina de estado.
"""

from __future__ import annotations

import json
import logging

import azure.functions as func

import auth
import constants as c
import db
import repository as repo
from resources import RESOURCES

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
logger = logging.getLogger("api")

KEY_SEPARATOR = "~"


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def respond(body: object, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, ensure_ascii=False, default=str),
        status_code=status,
        mimetype="application/json",
    )


def error(message: str, status: int) -> func.HttpResponse:
    return respond({"erro": message}, status)


def _authorize(req: func.HttpRequest, scope: str) -> func.HttpResponse | None:
    try:
        auth.require(req.headers.get("Authorization"), scope)
    except auth.Unauthorized as failure:
        return error(str(failure), 401)
    return None


def _resolve_resource(name: str):
    resource = RESOURCES.get(name)
    if resource is None:
        raise KeyError(
            f"recurso '{name}' não existe. Disponíveis: "
            f"{', '.join(sorted(RESOURCES))}"
        )
    return resource


def _build_key(resource, value: str) -> dict:
    parts = value.split(KEY_SEPARATOR)
    if len(parts) != len(resource.key):
        raise ValueError(
            f"a chave de '{resource.name}' tem {len(resource.key)} partes "
            f"({KEY_SEPARATOR.join(resource.key)}), recebidas {len(parts)}"
        )
    return dict(zip(resource.key, parts))


def _json_body(req: func.HttpRequest) -> dict:
    try:
        body = req.get_json()
    except ValueError as failure:
        raise ValueError("corpo não é JSON válido") from failure
    if not isinstance(body, dict):
        raise ValueError("o corpo precisa ser um objeto JSON")
    return body


# ---------------------------------------------------------------------------
# Leitura operacional
# ---------------------------------------------------------------------------


@app.route(route="problems", methods=["GET"])
def list_open_problems(req: func.HttpRequest) -> func.HttpResponse:
    """Incidentes abertos agora."""
    if (denied := _authorize(req, c.SCOPE_READ)) is not None:
        return denied

    try:
        limit = int(req.params.get("limit", c.DEFAULT_LIMIT))
    except ValueError:
        return error("limit precisa ser um número", 400)

    try:
        with db.connection() as conn:
            rows = repo.open_problems(conn, req.params.get("domain"), limit)
        return respond({"total": len(rows), "itens": rows})
    except Exception:
        logger.exception("falha ao listar problemas abertos")
        return error("erro ao consultar incidentes", 500)


@app.route(route="problems/closed", methods=["GET"])
def list_recently_closed_problems(req: func.HttpRequest) -> func.HttpResponse:
    """Incidentes fechados hoje e ontem — retrospectiva de turno.

    Serve o mesmo papel de `/problems` para quem pergunta "o que fechamos
    nas últimas horas", que a view `vw_open_problems` nunca respondeu por
    conter só o que está `OPEN`. Ver `vw_recently_closed_problems`
    para a janela exata.
    """
    if (denied := _authorize(req, c.SCOPE_READ)) is not None:
        return denied

    try:
        limit = int(req.params.get("limit", c.DEFAULT_LIMIT))
    except ValueError:
        return error("limit precisa ser um número", 400)

    try:
        with db.connection() as conn:
            rows = repo.recently_closed_problems(
                conn, req.params.get("domain"), limit
            )
        return respond({"total": len(rows), "itens": rows})
    except Exception:
        logger.exception("falha ao listar problemas fechados")
        return error("erro ao consultar incidentes fechados", 500)


# ---------------------------------------------------------------------------
# CRUD de configuração
# ---------------------------------------------------------------------------


@app.route(route="config/{resource}", methods=["GET"])
def list_config(req: func.HttpRequest) -> func.HttpResponse:
    if (denied := _authorize(req, c.SCOPE_READ)) is not None:
        return denied

    try:
        resource = _resolve_resource(req.route_params["resource"])
        limit = int(req.params.get("limit", c.DEFAULT_LIMIT))
    except KeyError as failure:
        return error(str(failure), 404)
    except ValueError:
        return error("limit precisa ser um número", 400)

    filters = {k: v for k, v in req.params.items() if k != "limit"}

    try:
        with db.connection() as conn:
            rows = repo.list_rows(conn, resource, filters, limit)
        return respond({"total": len(rows), "itens": rows})
    except Exception:
        logger.exception("falha ao listar %s", resource.name)
        return error("erro ao consultar", 500)


@app.route(route="config/{resource}/{key}", methods=["GET"])
def get_config(req: func.HttpRequest) -> func.HttpResponse:
    if (denied := _authorize(req, c.SCOPE_READ)) is not None:
        return denied

    try:
        resource = _resolve_resource(req.route_params["resource"])
        key = _build_key(resource, req.route_params["key"])
    except KeyError as failure:
        return error(str(failure), 404)
    except ValueError as failure:
        return error(str(failure), 400)

    try:
        with db.connection() as conn:
            return respond(repo.get(conn, resource, key))
    except repo.NotFound as failure:
        return error(str(failure), 404)
    except Exception:
        logger.exception("falha ao obter %s", resource.name)
        return error("erro ao consultar", 500)


@app.route(route="config/{resource}", methods=["POST"])
def create_config(req: func.HttpRequest) -> func.HttpResponse:
    if (denied := _authorize(req, c.SCOPE_WRITE)) is not None:
        return denied

    try:
        resource = _resolve_resource(req.route_params["resource"])
        body = _json_body(req)
    except KeyError as failure:
        return error(str(failure), 404)
    except ValueError as failure:
        return error(str(failure), 400)

    if problems := resource.validate(body, creating=True):
        return error("; ".join(problems), 400)

    try:
        with db.connection() as conn:
            return respond(repo.create(conn, resource, body), 201)
    except repo.AlreadyExists as failure:
        return error(str(failure), 409)
    except Exception:
        logger.exception("falha ao criar %s", resource.name)
        return error("erro ao criar", 500)


@app.route(route="config/{resource}/{key}", methods=["PATCH", "PUT"])
def update_config(req: func.HttpRequest) -> func.HttpResponse:
    if (denied := _authorize(req, c.SCOPE_WRITE)) is not None:
        return denied

    try:
        resource = _resolve_resource(req.route_params["resource"])
        key = _build_key(resource, req.route_params["key"])
        body = _json_body(req)
    except KeyError as failure:
        return error(str(failure), 404)
    except ValueError as failure:
        return error(str(failure), 400)

    if problems := resource.validate(body, creating=False):
        return error("; ".join(problems), 400)

    try:
        with db.connection() as conn:
            return respond(repo.update(conn, resource, key, body))
    except repo.NotFound as failure:
        return error(str(failure), 404)
    except Exception:
        logger.exception("falha ao atualizar %s", resource.name)
        return error("erro ao atualizar", 500)


@app.route(route="config/{resource}/{key}", methods=["DELETE"])
def delete_config(req: func.HttpRequest) -> func.HttpResponse:
    if (denied := _authorize(req, c.SCOPE_WRITE)) is not None:
        return denied

    try:
        resource = _resolve_resource(req.route_params["resource"])
        key = _build_key(resource, req.route_params["key"])
    except KeyError as failure:
        return error(str(failure), 404)
    except ValueError as failure:
        return error(str(failure), 400)

    try:
        with db.connection() as conn:
            repo.delete(conn, resource, key)
        return func.HttpResponse(status_code=204)
    except repo.NotFound as failure:
        return error(str(failure), 404)
    except Exception:
        logger.exception("falha ao remover %s", resource.name)
        return error("erro ao remover", 500)
