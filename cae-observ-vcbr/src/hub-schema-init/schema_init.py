"""Cria o schema do hub (Azure SQL) a partir do DDL embutido em `schema_definition.py`.

Existe porque nem sempre é possível executar scripts SQL à mão (rede
privada, sem cliente SQL). O DDL fica dentro do próprio código, sem depender
de arquivo, e este job o executa por pyodbc. Pode ser disparado quantas vezes for preciso: antes de cada comando ele olha
o banco e só executa o que falta.

Para cada comando de `SCHEMA_BATCHES`, em ordem:

  - CREATE TABLE   a tabela existe? Se sim, confere se as colunas (nome e
                   tipo) são as do arquivo e não altera nada; se não, cria;
  - CREATE INDEX   o índice existe? Se sim, pula; se não, cria;
  - INSERT         só executa se a tabela foi criada AGORA (dados iniciais
                   de uma tabela que já existia nunca são reinseridos);
  - CREATE OR ALTER VIEW   sempre executa (é o que atualiza a view).

Tudo numa única transação: se qualquer comando falhar, ou se alguma tabela
existente tiver estrutura diferente da esperada, nada é gravado e o erro
lista o que está errado. O job nunca altera nem apaga tabela existente;
divergência de estrutura é para uma pessoa decidir.

Opções:

  --dry-run       só mostra o que faria, sem escrever nada;
  --grant-users   além do schema, cria os usuários de banco das identidades
                  gerenciadas e dos grupos de rede e concede as permissões
                  (especificação 01, seção 4).

Configuração por variável de ambiente (o Bicep provisiona):

    SQL_SERVER_FQDN   ex.: sqlsrv-observ-dev-vcbr.database.windows.net
    SQL_DATABASE      ex.: db-observ-dev-vcbr
    ENVIRONMENT       dev ou prod (define os nomes das identidades)
    AZURE_CLIENT_ID   identidade gerenciada, quando houver mais de uma
    SQL_ADMIN_GROUPS  grupos de rede, separados por vírgula (opcional)

Quem executa precisa de permissão de DDL no banco (db_ddladmin ou
db_owner) e, para --grant-users, ser administrador Microsoft Entra do
servidor, porque CREATE USER ... FROM EXTERNAL PROVIDER exige isso.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import struct
import sys

import pyodbc
from azure.identity import DefaultAzureCredential

import constants as c
from schema_definition import SCHEMA_BATCHES

logger = logging.getLogger("hub-schema-init")

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")

_CREATE_TABLE = re.compile(r"^CREATE\s+TABLE\s+(?:dbo\.)?(\w+)\s*\(", re.IGNORECASE)
_CREATE_INDEX = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+)?(?:(?:NONCLUSTERED|CLUSTERED)\s+)?INDEX\s+(\w+)\s+ON\s+(?:dbo\.)?(\w+)",
    re.IGNORECASE,
)
_INSERT = re.compile(r"^INSERT\s+INTO\s+(?:dbo\.)?(\w+)", re.IGNORECASE)
_CREATE_VIEW = re.compile(r"^CREATE\s+OR\s+ALTER\s+VIEW\b", re.IGNORECASE)

#: Itens de CREATE TABLE que não são coluna.
_NOT_A_COLUMN = ("CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "INDEX")
_COLUMN_TYPE = re.compile(r"^(\w+)\s*(?:\(\s*(\w+)(?:\s*,\s*\d+)?\s*\))?")
_TEXT_TYPES = ("char", "varchar", "nchar", "nvarchar", "binary", "varbinary")
_PRECISION_TYPES = ("datetimeoffset", "datetime2", "time")


class SchemaMismatch(Exception):
    """Uma tabela existente não tem a estrutura que os arquivos descrevem."""


# ---------------------------------------------------------------------------
# Classificação dos comandos
# ---------------------------------------------------------------------------

def strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", sql))


def classify(batch: str) -> tuple:
    """Tipo do lote: ('table', nome), ('index', nome, tabela), ('insert',
    tabela) ou ('view',). Qualquer outra coisa é erro: o job só sabe tornar
    idempotentes estes quatro tipos, e executar o resto às cegas repetiria
    comandos não idempotentes."""
    body = strip_comments(batch).strip()
    if m := _CREATE_TABLE.match(body):
        return ("table", m.group(1).lower())
    if m := _CREATE_INDEX.match(body):
        return ("index", m.group(1).lower(), m.group(2).lower())
    if m := _INSERT.match(body):
        return ("insert", m.group(1).lower())
    if _CREATE_VIEW.match(body):
        return ("view",)
    raise ValueError(
        "comando não suportado pelo inicializador (use CREATE TABLE, CREATE "
        f"INDEX, INSERT ou CREATE OR ALTER VIEW): {body[:120]!r}"
    )


# ---------------------------------------------------------------------------
# Estrutura esperada de uma tabela
# ---------------------------------------------------------------------------

def _normalize_type(base: str, param: str | None) -> str:
    """Forma comparável do tipo: 'varchar(30)', 'datetimeoffset(3)', 'int'."""
    base = base.lower()
    if base in _TEXT_TYPES:
        return f"{base}({'max' if (param or '').lower() == 'max' else param})"
    if base in _PRECISION_TYPES:
        return f"{base}({param if param is not None else 7})"
    return base


def expected_columns(create_table: str) -> dict[str, str]:
    """{coluna: tipo} lido do CREATE TABLE. Nome e tipo bastam; anulabilidade,
    padrão e restrições não são comparados."""
    body = strip_comments(create_table)
    start = body.index("(")
    depth, items, current = 0, [], []
    for char in body[start:]:
        if char == "(":
            depth += 1
            if depth == 1:
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
        if char == "," and depth == 1:
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    items.append("".join(current))

    columns = {}
    for item in (i.strip() for i in items):
        if not item or item.split()[0].upper() in _NOT_A_COLUMN:
            continue
        name, _, rest = item.partition(" ")
        m = _COLUMN_TYPE.match(rest.strip())
        if m:
            columns[name.strip("[]").lower()] = _normalize_type(m.group(1), m.group(2))
    return columns


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

def connect_sql(credential: DefaultAzureCredential) -> pyodbc.Connection:
    """Conexão por Entra ID, sem usuário e senha. Rodando na máquina de
    alguém, o DefaultAzureCredential usa o `az login`."""
    token = credential.get_token(c.SQL_SCOPE).token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{os.environ['SQL_SERVER_FQDN']},1433;"
        f"Database={os.environ['SQL_DATABASE']};Encrypt=yes;"
    )
    return pyodbc.connect(
        connection_string, attrs_before={c.SQL_COPT_SS_ACCESS_TOKEN: token_struct}
    )


def table_exists(cursor, table: str) -> bool:
    row = cursor.execute("SELECT OBJECT_ID(?, 'U') AS id", table).fetchone()
    return row is not None and row.id is not None


def index_exists(cursor, index: str, table: str) -> bool:
    return cursor.execute(
        "SELECT 1 FROM sys.indexes WHERE name = ? AND object_id = OBJECT_ID(?)",
        index, table,
    ).fetchone() is not None


def actual_columns(cursor, table: str) -> dict[str, str]:
    rows = cursor.execute(
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, DATETIME_PRECISION"
        " FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?", table,
    ).fetchall()
    columns = {}
    for row in rows:
        data_type = row.DATA_TYPE.lower()
        if data_type in _TEXT_TYPES:
            size = "max" if row.CHARACTER_MAXIMUM_LENGTH == -1 else row.CHARACTER_MAXIMUM_LENGTH
            columns[row.COLUMN_NAME.lower()] = f"{data_type}({size})"
        elif data_type in _PRECISION_TYPES:
            columns[row.COLUMN_NAME.lower()] = f"{data_type}({row.DATETIME_PRECISION})"
        else:
            columns[row.COLUMN_NAME.lower()] = data_type
    return columns


def compare_structure(table: str, expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    """Problemas encontrados. Coluna a mais no banco não é problema (alguém
    pode tê-la acrescentado de propósito); falta ou tipo diferente é."""
    problems = []
    for name, wanted in expected.items():
        if name not in actual:
            problems.append(f"{table}.{name}: coluna ausente (esperado {wanted})")
        elif actual[name] != wanted:
            problems.append(f"{table}.{name}: tipo {actual[name]}, esperado {wanted}")
    return problems


def run_schema(conn: pyodbc.Connection, *, dry_run: bool) -> dict[str, int]:
    """Aplica (ou simula) o schema. Devolve a contagem do que foi criado,
    conferido e pulado."""
    plan = [(batch, classify(batch)) for batch in SCHEMA_BATCHES]  # falha cedo
    cursor = conn.cursor()
    created: set[str] = set()
    problems: list[str] = []
    stats = {"tabelas_criadas": 0, "tabelas_conferidas": 0, "indices_criados": 0,
             "indices_existentes": 0, "dados_iniciais": 0, "views": 0}

    def execute(batch: str) -> None:
        if dry_run:
            return
        try:
            cursor.execute(batch)
        except pyodbc.Error as error:
            raise RuntimeError(f"{error}\n--- comando ---\n{batch[:500]}") from error

    try:
        for batch, kind in plan:
            if kind[0] == "table":
                table = kind[1]
                if table_exists(cursor, table):
                    found = compare_structure(
                        table, expected_columns(batch), actual_columns(cursor, table)
                    )
                    problems += found
                    stats["tabelas_conferidas"] += 1
                    logger.info("%s: já existe%s", table, f" — {len(found)} divergência(s)" if found else ", estrutura conforme")
                else:
                    logger.info("%s: %s", table, "seria criada" if dry_run else "criando")
                    execute(batch)
                    created.add(table)
                    stats["tabelas_criadas"] += 1
            elif kind[0] == "index":
                _, index, table = kind
                if table not in created and index_exists(cursor, index, table):
                    stats["indices_existentes"] += 1
                else:
                    logger.info("índice %s: %s", index, "seria criado" if dry_run else "criando")
                    execute(batch)
                    stats["indices_criados"] += 1
            elif kind[0] == "insert":
                if kind[1] in created:
                    execute(batch)
                    stats["dados_iniciais"] += 1
                else:
                    logger.info("dados iniciais de %s: tabela já existia, não reinseridos", kind[1])
            else:
                logger.info("view: %s", "seria criada/atualizada" if dry_run else "criando/atualizando")
                execute(batch)
                stats["views"] += 1

        if problems:
            raise SchemaMismatch(
                "estrutura diferente da esperada; nada foi gravado:\n  " + "\n  ".join(problems)
            )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return stats


# ---------------------------------------------------------------------------
# Usuários e permissões (--grant-users)
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"^[A-Za-z0-9 _.\-]+$")


def quote_name(name: str) -> str:
    """Identificador entre colchetes. Nome de usuário não pode ir como
    parâmetro, então é validado e escapado aqui; ele vem de configuração, não
    de entrada externa, mas a checagem existe do mesmo jeito."""
    if not _SAFE_NAME.match(name):
        raise ValueError(f"nome de usuário com caractere inválido: {name!r}")
    return "[" + name.replace("]", "]]") + "]"


def grant_statements(environment: str, admin_groups: list[str]) -> list[str]:
    suffix = "" if environment == "prod" else "-dev"
    collector = quote_name(c.COLLECTOR_IDENTITY.format(suffix=suffix))
    api = quote_name(c.API_IDENTITY.format(suffix=suffix))

    statements = [
        # Jobs (coletor, sync, publisher, replication)
        f"ALTER ROLE db_datareader ADD MEMBER {collector}",
        f"ALTER ROLE db_datawriter ADD MEMBER {collector}",
    ]
    # API: menor privilégio, sem acesso às tabelas de incidente
    statements += [f"GRANT SELECT ON dbo.{o} TO {api}" for o in c.API_SELECT_ONLY]
    statements += [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.{o} TO {api}" for o in c.API_CRUD
    ]
    for group in admin_groups:
        g = quote_name(group)
        statements += [
            f"ALTER ROLE db_datareader ADD MEMBER {g}",
            f"ALTER ROLE db_datawriter ADD MEMBER {g}",
            f"ALTER ROLE db_ddladmin ADD MEMBER {g}",
        ]
    return statements


def grant_users(conn: pyodbc.Connection, environment: str, admin_groups: list[str]) -> None:
    """Cria os usuários que ainda não existem e concede as permissões. Repetir
    é seguro: CREATE USER só roda se faltar, e ADD MEMBER/GRANT são idempotentes."""
    suffix = "" if environment == "prod" else "-dev"
    names = [
        c.COLLECTOR_IDENTITY.format(suffix=suffix),
        c.API_IDENTITY.format(suffix=suffix),
        *admin_groups,
    ]
    cursor = conn.cursor()
    try:
        for name in names:
            exists = cursor.execute(
                "SELECT 1 FROM sys.database_principals WHERE name = ?", name
            ).fetchone()
            if exists:
                logger.info("usuário %s já existe", name)
            else:
                cursor.execute(f"CREATE USER {quote_name(name)} FROM EXTERNAL PROVIDER")
                logger.info("usuário %s criado", name)
        for statement in grant_statements(environment, admin_groups):
            cursor.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info("permissões concedidas")


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--grant-users", action="store_true")
    args = parser.parse_args(argv)

    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
    )
    environment = os.environ.get("ENVIRONMENT", "dev")
    groups_env = os.environ.get("SQL_ADMIN_GROUPS")
    admin_groups = (
        [g.strip() for g in groups_env.split(",") if g.strip()]
        if groups_env is not None else list(c.DEFAULT_ADMIN_GROUPS)
    )

    with connect_sql(credential) as conn:
        stats = run_schema(conn, dry_run=args.dry_run)
        logger.info("resumo: %s", stats)
        if args.grant_users and not args.dry_run:
            grant_users(conn, environment, admin_groups)
    return 0


if __name__ == "__main__":
    sys.exit(main())
