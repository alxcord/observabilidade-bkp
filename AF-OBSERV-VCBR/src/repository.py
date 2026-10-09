"""Traduz um recurso CRUD em SQL.

Um repositório serve todos os recursos: o que muda entre eles é a
descrição em `resources.py`, não o código. Isso mantém o SQL em um lugar só
e faz um recurso novo custar uma entrada de dicionário.

**Nenhum valor entra por interpolação.** Nomes de tabela e coluna vêm da
descrição do recurso — que é código nosso, não entrada de usuário — e os
valores vão sempre como parâmetro. É o que impede injeção mesmo com o
corpo da requisição vindo de fora.
"""

from __future__ import annotations

from typing import Any

import pyodbc

import constants as c
import db
from resources import Resource

# Esta API só faz CRUD de configuração e leitura de views. A ingestão de
# eventos (POST /events) foi removida em favor do hub-publisher escrever no
# hub direto por pyodbc, sem API HTTP no meio.


class NotFound(Exception):
    """A linha pedida não existe."""


class AlreadyExists(Exception):
    """Já há linha com essa chave."""


def list_rows(
    conn: pyodbc.Connection,
    resource: Resource,
    filters: dict[str, Any] | None = None,
    limit: int = c.DEFAULT_LIMIT,
) -> list[dict]:
    """Lista linhas, opcionalmente filtradas por igualdade."""
    filters = {k: v for k, v in (filters or {}).items() if k in resource.columns}

    columns = ", ".join(resource.columns)
    sql = f"SELECT TOP (?) {columns} FROM {resource.table}"
    params: list[Any] = [min(limit, c.MAX_LIMIT)]

    if filters:
        conditions = " AND ".join(f"{column} = ?" for column in filters)
        sql += f" WHERE {conditions}"
        params.extend(filters.values())

    sql += f" ORDER BY {', '.join(resource.key)}"
    return db.query(conn, sql, *params)


def get(conn: pyodbc.Connection, resource: Resource, key: dict) -> dict:
    """Uma linha pela chave. Levanta NotFound se não houver."""
    conditions = " AND ".join(f"{column} = ?" for column in resource.key)
    sql = (
        f"SELECT {', '.join(resource.columns)} FROM {resource.table} "
        f"WHERE {conditions}"
    )
    rows = db.query(conn, sql, *[key[column] for column in resource.key])

    if not rows:
        raise NotFound(f"{resource.name}: {key}")
    return rows[0]


def create(conn: pyodbc.Connection, resource: Resource, body: dict) -> dict:
    """Insere e devolve a linha criada.

    As colunas que a API define sozinha (`resource.fixed`) entram aqui, não
    pelo corpo da requisição: é o que garante o valor mesmo que o cliente
    tente mandar outro.
    """
    body = {**body, **resource.fixed}
    fields = [column for column in resource.writable_columns if column in body]
    placeholders = ", ".join("?" for _ in fields)
    sql = (
        f"INSERT INTO {resource.table} ({', '.join(fields)}) "
        f"VALUES ({placeholders})"
    )

    try:
        db.execute(conn, sql, *[body[field] for field in fields])
    except pyodbc.IntegrityError as error:
        # 2627 e 2601 são violação de chave; qualquer outra integridade
        # (chave estrangeira, CHECK) é erro do cliente e sobe como está.
        if "2627" in str(error) or "2601" in str(error):
            raise AlreadyExists(f"{resource.name}: chave já existe") from error
        raise

    return get(conn, resource, {column: body[column] for column in resource.key})


def update(
    conn: pyodbc.Connection, resource: Resource, key: dict, body: dict
) -> dict:
    """Atualiza os campos enviados e devolve a linha resultante.

    Atualização parcial: o que não veio no corpo não é tocado. Isso evita
    que um cliente que leu a linha antes de outra alteração sobrescreva
    campo que nem pretendia mexer.
    """
    fields = [
        column
        for column in resource.writable_columns
        if column in body and column not in resource.key
    ]
    if not fields:
        return get(conn, resource, key)

    assignments = ", ".join(f"{column} = ?" for column in fields)
    conditions = " AND ".join(f"{column} = ?" for column in resource.key)
    sql = f"UPDATE {resource.table} SET {assignments} WHERE {conditions}"

    params = [body[field] for field in fields]
    params.extend(key[column] for column in resource.key)

    if db.execute(conn, sql, *params) == 0:
        raise NotFound(f"{resource.name}: {key}")

    return get(conn, resource, key)


def delete(conn: pyodbc.Connection, resource: Resource, key: dict) -> None:
    """Apaga a linha. Levanta NotFound se não havia o que apagar."""
    conditions = " AND ".join(f"{column} = ?" for column in resource.key)
    sql = f"DELETE FROM {resource.table} WHERE {conditions}"

    affected = db.execute(
        conn, sql, *[key[column] for column in resource.key]
    )
    if affected == 0:
        raise NotFound(f"{resource.name}: {key}")


# ---------------------------------------------------------------------------
# Operações que não são CRUD
# ---------------------------------------------------------------------------


def open_problems(
    conn: pyodbc.Connection, domain: str | None = None, limit: int = c.DEFAULT_LIMIT
) -> list[dict]:
    """O que está aberto agora, pela view que é o contrato de consumo."""
    sql = "SELECT TOP (?) * FROM vw_open_problems"
    params: list[Any] = [min(limit, c.MAX_LIMIT)]

    if domain:
        sql += " WHERE domain = ?"
        params.append(domain)

    sql += " ORDER BY severity, opened_at"
    return db.query(conn, sql, *params)


def recently_closed_problems(
    conn: pyodbc.Connection, domain: str | None = None, limit: int = c.DEFAULT_LIMIT
) -> list[dict]:
    """O que fechou hoje e ontem, pela view de retrospectiva."""
    sql = "SELECT TOP (?) * FROM vw_recently_closed_problems"
    params: list[Any] = [min(limit, c.MAX_LIMIT)]

    if domain:
        sql += " WHERE domain = ?"
        params.append(domain)

    sql += " ORDER BY closed_at DESC"
    return db.query(conn, sql, *params)
