"""Descreve as tabelas expostas pela API como recursos CRUD.

Cada tabela de configuração vira um recurso REST com o mesmo comportamento.
Descrever a tabela em dados, em vez de escrever quatro handlers para cada
uma, é o que mantém a API pequena: acrescentar um recurso novo é
acrescentar uma entrada aqui.

Só tabelas de **configuração** entram. `tbl_problems`, `tbl_executions` e
`tbl_technical_events` são estado operacional: quem escreve nelas é o
`hub-publisher` e o coletor, e expor um PUT nelas permitiria alterar um
incidente por fora da máquina de estado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import constants as c

#: Validação de uma coluna. Devolve a mensagem do problema, ou None se o
#: valor serve. A mensagem vai para quem fez a chamada, então diz o que o
#: valor deveria ser — não só que está errado.
Validator = Callable[[Any], "str | None"]


def preenchido(value: Any) -> str | None:
    """Texto obrigatório. Espaço em branco não é nome."""
    if not isinstance(value, str) or not value.strip():
        return "precisa ser um texto não vazio"
    return None


def minimo(minimum: int) -> Validator:
    """Inteiro a partir de `minimum`.

    `True` é `int` em Python, mas não é quantidade: aceitar deixaria
    `expected_nodes: true` virar 1 no banco, sem o cliente perceber.
    """

    def validate(value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return f"precisa ser um número inteiro maior ou igual a {minimum}"
        if value < minimum:
            return f"precisa ser maior ou igual a {minimum} (recebido {value})"
        return None

    return validate


@dataclass(frozen=True)
class Resource:
    """Uma tabela exposta como CRUD."""

    name: str
    table: str
    key: tuple[str, ...]
    columns: tuple[str, ...]
    #: Colunas que o banco preenche sozinho e o cliente não envia.
    read_only: tuple[str, ...] = ()
    #: Colunas obrigatórias na criação.
    required: tuple[str, ...] = field(default=())
    #: Colunas que a própria API preenche na criação, com este valor. O
    #: cliente não envia, nem na criação nem na atualização: o valor é
    #: decisão da API, não do chamador.
    fixed: Mapping[str, Any] = field(default_factory=dict)
    #: Validação de valor, por coluna, além da presença exigida em
    #: `required`.
    checks: Mapping[str, Validator] = field(default_factory=dict)

    @property
    def writable_columns(self) -> tuple[str, ...]:
        return tuple(c for c in self.columns if c not in self.read_only)

    def validate(self, body: dict, creating: bool) -> list[str]:
        """Devolve os problemas encontrados no corpo da requisição.

        Rejeitar campo desconhecido é deliberado: um typo em nome de coluna
        passaria silenciosamente como "não atualizou nada", e o cliente não
        teria como saber.
        """
        errors = []

        unknown = set(body) - set(self.columns)
        if unknown:
            errors.append(f"campos desconhecidos: {sorted(unknown)}")

        read_only_sent = set(body) & set(self.read_only)
        if read_only_sent:
            errors.append(f"campos somente leitura: {sorted(read_only_sent)}")

        for column in sorted(set(body) & set(self.fixed)):
            errors.append(
                f"{column}: definido pela API como '{self.fixed[column]}', "
                f"não envie"
            )

        if creating:
            missing = set(self.required) - set(body)
            if missing:
                errors.append(f"campos obrigatórios ausentes: {sorted(missing)}")

        for column, check in self.checks.items():
            if column in body and (problem := check(body[column])) is not None:
                errors.append(f"{column}: {problem}")

        return errors


RESOURCES: dict[str, Resource] = {
    "workspaces": Resource(
        name="workspaces",
        table="tbl_cfg_workspaces",
        key=("workspace_id",),
        columns=(
            "workspace_id",
            "workspace_name",
            "workspace_url",
            "region",
            "environment",
            "active",
            "synced_at",
        ),
        read_only=("synced_at",),
        required=("workspace_id", "workspace_name", "workspace_url"),
    ),
    "alert-routes": Resource(
        name="alert-routes",
        table="tbl_cfg_alert_routing",
        key=("domain", "severity", "channel"),
        columns=("domain", "severity", "channel", "destination", "active"),
        required=("domain", "severity", "channel", "destination"),
    ),
    "expectations": Resource(
        name="expectations",
        table="tbl_cfg_execution_expectations",
        key=("source", "natural_key"),
        columns=(
            "source",
            "natural_key",
            "description",
            "schedule_kind",
            "interval_unit",
            "interval_value",
            "time_of_day",
            "days_of_week",
            "day_of_month",
            "last_day_of_month",
            "observes_national_holidays",
            "timezone",
            "tolerance_minutes",
            "max_expected_duration_min",
            "written_by",
            "source_expression",
            "config_source",
            "active",
            "updated_at",
        ),
        read_only=("updated_at",),
        required=("source", "natural_key", "schedule_kind", "written_by"),
        # A coluna é obrigatória no banco e o cliente não a envia: sem este
        # valor a criação falharia. E deixar o cliente escolher seria pior —
        # com `AUTO` ou `YAML` a linha passaria a ser gerenciada pelo
        # sincronizador de expectativas, que a sobrescreveria na execução
        # seguinte, sem aviso para quem cadastrou.
        fixed={"config_source": c.EXPECTATION_CONFIG_SOURCE},
    ),
    "domains": Resource(
        name="domains",
        table="tbl_cfg_domain_scope",
        key=("source", "scope"),
        columns=("source", "scope", "domain", "active"),
        required=("source", "scope", "domain"),
    ),
    # Self-Hosted Integration Runtime cuja disponibilidade de nós o coletor
    # acompanha. Só entra o ADF **dono** do SHIR: um Linked IR não se
    # cadastra, porque o sinal vem do ADF dono. IR fora desta tabela não é
    # monitorado, e `active = 0` tira do monitoramento sem perder o
    # cadastro.
    "integration-runtimes": Resource(
        name="integration-runtimes",
        table="tbl_cfg_integration_runtimes",
        key=("data_factory", "integration_runtime"),
        columns=(
            "data_factory",
            "integration_runtime",
            "expected_nodes",
            "active",
        ),
        required=("data_factory", "integration_runtime", "expected_nodes"),
        checks={
            "data_factory": preenchido,
            "integration_runtime": preenchido,
            "expected_nodes": minimo(c.MIN_EXPECTED_NODES),
        },
    ),
}
