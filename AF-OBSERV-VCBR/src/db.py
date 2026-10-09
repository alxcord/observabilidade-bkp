"""Acesso ao Azure SQL, autenticado por Managed Identity.

Sem usuário e senha em lugar nenhum: o token vem da identidade da Function
e entra na conexão pelo atributo do driver ODBC.
"""

from __future__ import annotations

import os
import struct
from contextlib import contextmanager
from typing import Any, Iterator

import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c

_credential = DefaultAzureCredential(
    managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
)


@contextmanager
def connection() -> Iterator[pyodbc.Connection]:
    """Abre e fecha uma conexão autenticada por Managed Identity."""
    token = _credential.get_token(c.SQL_SCOPE).token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)

    conn = pyodbc.connect(
        os.environ[c.VAR_SQL_CONNECTION],
        attrs_before={c.SQL_COPT_SS_ACCESS_TOKEN: token_struct},
    )
    try:
        yield conn
    finally:
        conn.close()


def query(conn: pyodbc.Connection, sql: str, *params: Any) -> list[dict]:
    """Executa um SELECT e devolve dicionários."""
    cursor = conn.cursor()
    cursor.execute(sql, *params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def execute(conn: pyodbc.Connection, sql: str, *params: Any) -> int:
    """Executa INSERT, UPDATE ou DELETE e devolve as linhas afetadas."""
    cursor = conn.cursor()
    cursor.execute(sql, *params)
    affected = cursor.rowcount
    conn.commit()
    return affected
