"""Testes da resolução de chave natural.

Os casos vêm de execuções reais do ambiente: nomes, tipos e proporções
foram extraídos de 62.016 execuções coletadas para dimensionamento.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

import natural_key as nk  # noqa: E402
import constants as c  # noqa: E402

# Nome real, encurtado. O sufixo é o Activity Run ID do ADF.
ADF_NAME = (
    "ADF_DF-COMERCIAL2-VCBR_PIP_ING_AGENT_REPORT_raw2cleansed"
    "_388b5d11-ad2b-41c6-a075-a5b1e2c3d4f5"
)


class TestSubmitRun(unittest.TestCase):
    """65,1% do volume. Depende inteiramente do run_name."""

    def test_separates_prefix_and_activity_run_id(self):
        ident = nk.resolve(
            {"run_type": c.RUN_TYPE_SUBMIT, "run_name": ADF_NAME, "job_id": "123"}
        )
        self.assertEqual(
            ident.natural_key,
            "adf_df-comercial2-vcbr_pip_ing_agent_report_raw2cleansed",
        )
        self.assertEqual(ident.activity_run_id, "388b5d11-ad2b-41c6-a075-a5b1e2c3d4f5")
        self.assertEqual(ident.rule, c.KEY_RULE_RUN_NAME)
        self.assertTrue(ident.correlatable)

    def test_ignores_job_id_even_when_filled(self):
        # A armadilha central: job_id vem preenchido em 100% das execuções,
        # mas no SUBMIT_RUN muda a cada uma. Usá-lo faria toda falha abrir
        # incidente novo.
        a = nk.resolve(
            {"run_type": c.RUN_TYPE_SUBMIT, "run_name": ADF_NAME, "job_id": "111"}
        )
        b = nk.resolve(
            {"run_type": c.RUN_TYPE_SUBMIT, "run_name": ADF_NAME, "job_id": "999"}
        )
        self.assertEqual(a.natural_key, b.natural_key)

    def test_runs_of_the_same_activity_share_the_key(self):
        base = "ADF_DF-LOGISTICS-VCBR_five9_nps_cleansed"
        ids = [
            "388b5d11-ad2b-41c6-a075-a5b1e2c3d4f5",
            "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        ]
        keys = {
            nk.resolve(
                {
                    "run_type": c.RUN_TYPE_SUBMIT,
                    "run_name": f"{base}_{i}",
                    "job_id": "1",
                }
            ).natural_key
            for i in ids
        }
        self.assertEqual(len(keys), 1)

    def test_name_without_activity_run_id(self):
        # 0,6% dos casos, como "cleansed2curated_gestao_almoxarifado".
        # Mantém identidade; perde correlação.
        ident = nk.resolve(
            {
                "run_type": c.RUN_TYPE_SUBMIT,
                "run_name": "cleansed2curated_gestao_almoxarifado",
                "job_id": "1",
            }
        )
        self.assertEqual(ident.natural_key, "cleansed2curated_gestao_almoxarifado")
        self.assertEqual(ident.rule, c.KEY_RULE_RUN_NAME_NO_ID)
        self.assertFalse(ident.correlatable)

    def test_uuid_in_the_middle_of_the_name_does_not_count(self):
        # A âncora é o fim da string: só o sufixo é Activity Run ID.
        name = "job_388b5d11-ad2b-41c6-a075-a5b1e2c3d4f5_etapa_final"
        ident = nk.resolve(
            {"run_type": c.RUN_TYPE_SUBMIT, "run_name": name, "job_id": "1"}
        )
        self.assertEqual(ident.rule, c.KEY_RULE_RUN_NAME_NO_ID)

    def test_no_name_does_not_identify(self):
        self.assertIsNone(
            nk.resolve({"run_type": c.RUN_TYPE_SUBMIT, "run_name": "", "job_id": "1"})
        )


class TestJobRun(unittest.TestCase):
    """7,2% do volume. Aqui o job_id é estável — 55,5 execuções por id."""

    def test_uses_job_id_scoped_by_workspace(self):
        ident = nk.resolve(
            {"run_type": c.RUN_TYPE_JOB, "job_id": "812", "workspace_id": "ws1"}
        )
        self.assertEqual(ident.natural_key, "ws1:812")
        self.assertEqual(ident.rule, c.KEY_RULE_JOB_ID)

    def test_without_run_name_does_not_correlate(self):
        # A base medida vem das system tables, onde o nome de um job
        # persistente é normalizado em outra tabela e por isso aparece
        # vazio aqui. Se a Jobs API devolver run_name para este tipo, a
        # correlação passa a funcionar e este teste muda — ele descreve o
        # caso sem nome, não uma propriedade do JOB_RUN.
        ident = nk.resolve(
            {"run_type": c.RUN_TYPE_JOB, "job_id": "812", "workspace_id": "ws1"}
        )
        self.assertFalse(ident.correlatable)

    def test_same_job_id_in_different_workspaces_does_not_collide(self):
        a = nk.resolve(
            {"run_type": c.RUN_TYPE_JOB, "job_id": "812", "workspace_id": "ws1"}
        )
        b = nk.resolve(
            {"run_type": c.RUN_TYPE_JOB, "job_id": "812", "workspace_id": "ws2"}
        )
        self.assertNotEqual(a.natural_key, b.natural_key)


class TestWorkflowRun(unittest.TestCase):
    """27,7% do volume, e nenhum identificador estável."""

    def test_does_not_identify(self):
        # job_id efêmero, run_name e job_name vazios. Devolver None faz o
        # coletor anexar ao incidente do pai em vez de abrir um órfão que
        # nunca fecharia.
        self.assertIsNone(
            nk.resolve(
                {"run_type": c.RUN_TYPE_WORKFLOW, "job_id": "77", "run_name": None}
            )
        )


class TestUnknownType(unittest.TestCase):
    def test_with_name_is_treated_as_submit(self):
        ident = nk.resolve({"run_type": "TIPO_NOVO", "run_name": ADF_NAME})
        self.assertIsNotNone(ident)

    def test_without_name_does_not_identify(self):
        self.assertIsNone(nk.resolve({"run_type": "TIPO_NOVO", "run_name": None}))


class TestNameDecomposition(unittest.TestCase):
    """A gramática ADF_<factory>_<processo>_<uuid>.

    Só o campo do meio admite underscore, então o primeiro, o segundo e o
    último separador bastam para decompor sem ambiguidade.
    """

    def test_extracts_data_factory_and_process(self):
        ident = nk.resolve({"run_type": c.RUN_TYPE_SUBMIT, "run_name": ADF_NAME})
        self.assertEqual(ident.data_factory, "DF-COMERCIAL2-VCBR")
        self.assertEqual(
            ident.process, "PIP_ING_AGENT_REPORT_raw2cleansed"
        )

    def test_process_with_many_underscores(self):
        name = (
            "ADF_DF-LOGISTICS-VCBR_five9_centrais_remotas_nps_cleansed"
            "_388b5d11-ad2b-41c6-a075-a5b1e2c3d4f5"
        )
        ident = nk.resolve({"run_type": c.RUN_TYPE_SUBMIT, "run_name": name})
        self.assertEqual(ident.data_factory, "DF-LOGISTICS-VCBR")
        self.assertEqual(
            ident.process, "five9_centrais_remotas_nps_cleansed"
        )

    def test_factory_name_uses_hyphen_not_underscore(self):
        # É o que torna a decomposição possível: o segundo componente
        # nunca contém underscore.
        ident = nk.resolve({"run_type": c.RUN_TYPE_SUBMIT, "run_name": ADF_NAME})
        self.assertNotIn("_", ident.data_factory)

    def test_name_outside_the_pattern_does_not_decompose(self):
        ident = nk.resolve(
            {"run_type": c.RUN_TYPE_SUBMIT, "run_name": "raw2cleansed_estoque_v3"}
        )
        self.assertIsNone(ident.data_factory)
        self.assertEqual(ident.rule, c.KEY_RULE_RUN_NAME_NO_ID)


class TestDomainResolution(unittest.TestCase):
    def test_known_factory_resolves_the_area(self):
        ident = nk.resolve({"run_type": c.RUN_TYPE_SUBMIT, "run_name": ADF_NAME})
        self.assertEqual(nk.resolve_domain(ident), "Comercial")

    def test_dev_environment_inherits_the_production_pairs_domain(self):
        name = "ADF_DF-PIOSI-DEV-VCBR_carga_388b5d11-ad2b-41c6-a075-a5b1e2c3d4f5"
        ident = nk.resolve({"run_type": c.RUN_TYPE_SUBMIT, "run_name": name})
        self.assertEqual(nk.resolve_domain(ident), "Industrial")

    def test_unknown_factory_falls_back_to_default(self):
        name = "ADF_DF-NOVO-VCBR_carga_388b5d11-ad2b-41c6-a075-a5b1e2c3d4f5"
        ident = nk.resolve({"run_type": c.RUN_TYPE_SUBMIT, "run_name": name})
        self.assertEqual(nk.resolve_domain(ident), c.DEFAULT_DOMAIN)

    def test_no_identity_falls_back_to_default(self):
        self.assertEqual(nk.resolve_domain(None), c.DEFAULT_DOMAIN)

    def test_job_run_has_no_factory_and_falls_back_to_default(self):
        ident = nk.resolve(
            {"run_type": c.RUN_TYPE_JOB, "job_id": "812", "workspace_id": "ws1"}
        )
        self.assertEqual(nk.resolve_domain(ident), c.DEFAULT_DOMAIN)


if __name__ == "__main__":
    unittest.main()
