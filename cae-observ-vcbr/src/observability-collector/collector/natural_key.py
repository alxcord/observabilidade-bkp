"""Resolve a identidade estável de uma execução do Databricks.

O hub raciocina em "esse processo está falhando até segunda ordem", não em
execuções isoladas. Para isso precisa de uma chave que sobreviva entre
execuções — sem ela, cada falha abre incidente novo e nenhum sucesso fecha
o anterior.

O ponto não óbvio: **`job_id` está preenchido em toda execução, mas só é
estável em uma delas.** Medido sobre 62.016 execuções reais do ambiente:

    tipo           volume   job_id                chave que serve
    SUBMIT_RUN     65,1%    efêmero (1,0 exec)    prefixo do run_name
    JOB_RUN         7,2%    estável (55,5 exec)   job_id
    WORKFLOW_RUN   27,7%    efêmero (1,0 exec)    nenhuma (ver abaixo)

Por isso o despacho é por `run_type`, nunca por "o campo job_id veio
preenchido". Testar a presença do campo faria o SUBMIT_RUN usar um id que
muda a cada execução — exatamente o defeito que esta cascata evita.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import constants as c

#: O nome que o ADF gera tem gramática fixa:
#:
#:     ADF_<data-factory>_<processo>_<activity-run-id>
#:      │        │             │              │
#:      │        │             │              └─ UUID, nunca tem underscore
#:      │        │             └─ pipeline + activity; PODE ter underscore
#:      │        └─ nome do Data Factory; nunca tem underscore (usa hífen)
#:      └─ prefixo fixo
#:
#: Como só o campo do meio admite underscore, olhar o primeiro, o segundo e
#: o último separador decompõe o nome inteiro sem ambiguidade. Validado em
#: 40.356 execuções reais: 99,4% seguem a gramática, e os 18 Data Factories
#: extraídos não têm underscore em nenhum caso.
BARE_UUID = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass(frozen=True)
class Identity:
    """Como uma execução é identificada pelo hub."""

    natural_key: str
    rule: str
    activity_run_id: str | None = None
    data_factory: str | None = None
    process: str | None = None

    @property
    def correlatable(self) -> bool:
        """Dá para juntar esta execução com o evento do ADF?"""
        return self.activity_run_id is not None


def resolve(run: dict) -> Identity | None:
    """Devolve a identidade da execução, ou None se ela não tiver uma.

    None não é erro: significa que a execução não deve abrir incidente
    próprio. Ver `_resolve_workflow`.
    """
    run_type = (run.get("run_type") or "").upper()

    if run_type == c.RUN_TYPE_SUBMIT:
        return _resolve_submit(run)
    if run_type == c.RUN_TYPE_JOB:
        return _resolve_job(run)
    if run_type == c.RUN_TYPE_WORKFLOW:
        return _resolve_workflow(run)

    # Tipo novo na API: usa o run_name se houver, senão não identifica.
    # Preferir não identificar a inventar uma chave instável.
    return _resolve_submit(run) if run.get("run_name") else None


def _resolve_submit(run: dict) -> Identity | None:
    """Run efêmero, tipicamente disparado pelo ADF.

    Decompõe o nome pela gramática documentada acima. A chave natural é o
    nome sem o Activity Run ID — estável entre execuções da mesma
    atividade: 782 chaves distintas para 40.356 execuções.

    O Data Factory extraído aqui é o que resolve o domínio de negócio: há
    um ADF por área, e o nome carrega a área.
    """
    name = (run.get("run_name") or "").strip()
    if not name:
        return None

    parts = name.split("_")

    # Gramática completa: prefixo ADF, factory, processo e UUID no fim.
    if len(parts) >= 4 and parts[0] == c.ADF_PREFIX and BARE_UUID.match(parts[-1]):
        return Identity(
            natural_key="_".join(parts[:-1]).lower(),
            rule=c.KEY_RULE_RUN_NAME,
            activity_run_id=parts[-1].lower(),
            data_factory=parts[1],
            # Pipeline e activity, juntos: os dois admitem underscore, e não
            # há separador que os distinga com segurança. O par identifica o
            # processo, que é o que interessa.
            process="_".join(parts[2:-1]),
        )

    # 0,6% dos casos: nome que não veio do ADF — execução manual ou disparo
    # por outra via. O nome inteiro serve como chave; o que se perde é a
    # correlação, não a identidade.
    return Identity(natural_key=name.lower(), rule=c.KEY_RULE_RUN_NAME_NO_ID)


def _resolve_job(run: dict) -> Identity | None:
    """Job persistente. Aqui o job_id é de fato estável.

    A correlação com o ADF fica em aberto: a base usada para medir vem das
    system tables, onde o nome do job é normalizado em outra tabela e o
    run_name aparece vazio. Se a Jobs API devolver run_name com o Activity
    Run ID para este tipo, basta aplicar a mesma decomposição do
    _resolve_submit — a dedup não muda, porque já vem do job_id.
    """
    job_id = str(run.get("job_id") or "").strip()
    if not job_id or job_id == "0":
        return None

    return Identity(
        natural_key=f"{run.get('workspace_id', '')}:{job_id}".lower(),
        rule=c.KEY_RULE_JOB_ID,
    )


def _resolve_workflow(run: dict) -> Identity | None:
    """Execução aninhada, criada por um notebook que chama outro.

    Não tem identificador estável nenhum: job_id é efêmero, run_name e
    job_name vêm vazios. São 27,7% do volume.

    A saída não é inventar uma chave — é reconhecer que esta execução é
    **filha** de outra. Quando o notebook aninhado falha, quem falha para o
    negócio é a execução que o chamou, e essa tem chave. Devolver None faz
    o coletor anexar o evento ao incidente do pai em vez de abrir um novo.

    Sem o pai identificado, o evento fica registrado em `tbl_executions` para
    investigação, mas não alerta. É a escolha certa entre alerta órfão
    (sem chave para fechar, acumulando para sempre) e evidência sem alerta.
    """
    return None


def resolve_domain(identity: Identity | None) -> str:
    """Domínio de negócio da execução, deduzido do Data Factory.

    Há um ADF por área, e o nome do Data Factory vem embutido no run_name.
    Isso resolve o domínio de 65% do volume sem cadastro manual.

    O sufixo de ambiente é descartado: DF-PIOSI-DEV-VCBR e DF-PIOSI-VCBR
    são a mesma área. Factory desconhecido cai no domínio padrão, que tem
    rota garantida — alerta sem domínio é alerta atrasado, nunca perdido.
    """
    if identity is None or not identity.data_factory:
        return c.DEFAULT_DOMAIN

    factory = identity.data_factory.upper()
    if factory in c.DOMAIN_BY_DATA_FACTORY:
        return c.DOMAIN_BY_DATA_FACTORY[factory]

    # Tenta de novo sem o sufixo de ambiente, para que um ADF de
    # desenvolvimento herde o domínio do seu par produtivo.
    for suffix in c.ENVIRONMENT_SUFFIXES:
        if factory.endswith(suffix):
            base = factory[: -len(suffix)]
            for known, domain in c.DOMAIN_BY_DATA_FACTORY.items():
                if known.startswith(base + "-"):
                    return domain

    return c.DEFAULT_DOMAIN
