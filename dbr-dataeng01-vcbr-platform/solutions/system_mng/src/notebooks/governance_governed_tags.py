# Databricks notebook source
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.iam import RuleSetUpdateRequest, GrantRule

w = WorkspaceClient()

ACCOUNT_ID = "f05f5725-04c9-4816-9d48-a847bd489baf"

tag_policy_keys = [
    "vc_company",
    "vc_refresh_frequency",
    "vc_data_classification",
    "vc_data_retention",
    "vc_lifecycle_status",
    "vc_medallion_layer",
    "vc_contains_pii",
    "vc_data_domain",
    "vc_source_system",
    "vc_data_quality_tier",
    "vc_criticality",
]

# Service principals (formato: servicePrincipals/<application_id>)
# Groups (formato: groups/<display_name>)
principals = [
    "servicePrincipals/efdccf52-e2cc-4f02-bd0c-48524718e71f",  # VC_OWNER_SERVICE
    "servicePrincipals/046737b1-1285-43f2-a71c-7618c91890d4",  # MI-ADF-DBR-CONECTOR-DEV-VCBR
    "servicePrincipals/c5fe0ec6-f8a8-4f89-a400-a443fc334b9e",  # MI-ADF-DBR-CONECTOR-PRD-VCBR
    "groups/Data Lake - Administrador de dados",
    "groups/Data Lake - Cientistas de dados VC",
    "groups/Data Lake - Cientistas de dados Externos",
    "groups/Data Lake - Engenheiros de dados VC",
    "groups/Data Lake - Engenheiros de dados Externos",
    "groups/Data Lake - Sustentacao",
]

ROLE = "roles/tagPolicy.assigner"  # equivale a ASSIGN

for tag_key in tag_policy_keys:
    try:
        # Obter o ID da tag policy
        tag_policy = w.tag_policies.get_tag_policy(tag_key=tag_key)
        tag_id = tag_policy.id
        rule_set_name = f"accounts/{ACCOUNT_ID}/tagPolicies/{tag_id}/ruleSets/default"

        # Ler rule set atual (com etag para controle de concorrencia)
        current = w.account_access_control_proxy.get_rule_set(name=rule_set_name, etag="")
        existing_rules = list(current.grant_rules) if current.grant_rules else []

        # Montar nova grant rule com os principals
        new_rule = GrantRule(role=ROLE, principals=principals)

        # Preservar regras existentes de outros roles e adicionar/substituir a de assigner
        updated_rules = [r for r in existing_rules if r.role != ROLE]
        updated_rules.append(new_rule)

        # Atualizar o rule set
        w.account_access_control_proxy.update_rule_set(
            name=rule_set_name,
            rule_set=RuleSetUpdateRequest(
                name=rule_set_name,
                etag=current.etag,
                grant_rules=updated_rules,
            ),
        )
        print(f"ASSIGN concedido em '{tag_key}' para {len(principals)} principals")
    except Exception as e:
        print(f"Falha em '{tag_key}': {e}")

