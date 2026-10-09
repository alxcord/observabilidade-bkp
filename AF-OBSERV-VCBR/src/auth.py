"""Autorização por Bearer Token, com escopos separados.

Dois tokens, não um: quem lê não escreve. Um token de leitura vazado
expõe a lista de incidentes; um token de escrita vazado permite injetar
incidente falso ou fechar incidente real — são riscos de tamanhos
diferentes e não devem compartilhar credencial.

O que este módulo NÃO protege, e precisa de rede: a rota de escrita deve
estar restrita por allowlist da rede corporativa. Bearer estático não
expira sozinho.
"""

from __future__ import annotations

import hmac
import logging
import os

import constants as c

logger = logging.getLogger("api.auth")


class Unauthorized(Exception):
    """Credencial ausente, malformada ou inválida."""


def _expected_token(scope: str) -> str:
    variable = c.VAR_TOKEN_WRITE if scope == c.SCOPE_WRITE else c.VAR_TOKEN_READ
    token = os.environ.get(variable, "")
    if not token:
        # Falha fechada: sem token configurado, ninguém entra. O contrário
        # transformaria um erro de deploy em API aberta.
        raise Unauthorized(f"{variable} não configurada")

    if token.startswith(c.KEY_VAULT_REFERENCE_PREFIX):
        # A referência do Key Vault não resolveu e o App Setting ficou com o
        # texto literal. Aceitá-lo deixaria a API com um token previsível,
        # que qualquer um que conheça o formato escreve — pior do que não ter
        # token. Mesma falha fechada do caso acima.
        logger.error(
            "%s não foi resolvida pelo Key Vault: o valor continua sendo a "
            "referência, não o segredo",
            variable,
        )
        raise Unauthorized(f"{variable} não foi resolvida pelo Key Vault")

    return token


def require(authorization_header: str | None, scope: str) -> None:
    """Valida o header Authorization. Levanta Unauthorized se não passar.

    A comparação usa `compare_digest` para não vazar o tamanho do prefixo
    correto pelo tempo de resposta.
    """
    if not authorization_header:
        raise Unauthorized("header Authorization ausente")

    parts = authorization_header.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Unauthorized("formato esperado: Bearer <token>")

    if not hmac.compare_digest(parts[1], _expected_token(scope)):
        logger.warning("token inválido para escopo %s", scope)
        raise Unauthorized("token inválido")
