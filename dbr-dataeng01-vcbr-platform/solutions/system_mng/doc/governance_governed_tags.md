# Notebook: `governance_governed_tags`

**Objetivo:** Conceder a permissão **ASSIGN** em todas as governed tags da conta para um conjunto de service principals e grupos do workspace. Isso permite que esses principals apliquem governed tags (como `vc_company`, `vc_data_classification`, `vc_contains_pii`, etc.) a objetos do Unity Catalog (tabelas, schemas, catálogos, colunas).

**Por que existe:** A API padrão do Unity Catalog (`w.grants.update` / SQL `GRANT`) não suporta `TAG_POLICY` como securable type. As permissões de governed tags são gerenciadas via **Account Access Control Proxy API**, usando o conceito de **rule sets** — um mecanismo de controle de acesso a nível de conta.

**Como funciona:**
1. Itera sobre cada tag policy key (ex: `vc_company`, `vc_criticality`)
2. Obtém o UUID da tag policy via SDK (`w.tag_policies.get_tag_policy`)
3. Lê o rule set atual da tag policy (preservando regras existentes e o etag para controle de concorrência)
4. Adiciona ou substitui a regra do role `roles/tagPolicy.assigner` com a lista de principals
5. Atualiza o rule set via `w.account_access_control_proxy.update_rule_set`

**Tag policies afetadas (11):** `vc_company`, `vc_refresh_frequency`, `vc_data_classification`, `vc_data_retention`, `vc_lifecycle_status`, `vc_medallion_layer`, `vc_contains_pii`, `vc_data_domain`, `vc_source_system`, `vc_data_quality_tier`, `vc_criticality`

**Principals beneficiados (9):**
* 3 service principals: `VC_OWNER_SERVICE`, `MI-ADF-DBR-CONECTOR-DEV-VCBR`, `MI-ADF-DBR-CONECTOR-PRD-VCBR`
* 6 grupos: `Data Lake - Administrador de dados`, `Cientistas de dados VC/Externos`, `Engenheiros de dados VC/Externos`, `Sustentacao`

**Perfil necessário para executar:**
* O executor deve possuir a permissão **MANAGE** na governed tag (ou MANAGE a nível de conta). Atualmente, `thiago.morais.tm1@vcimentos.com` é o manager existente nas tag policies.
* Alternativamente, ser **Account Admin** (que possui MANAGE implícito).

**Dependências:**
* `databricks-sdk` >= 0.118.0 (módulos `iam.RuleSetUpdateRequest`, `iam.GrantRule`, `tags.TagPoliciesAPI`)
* Autenticação do workspace via `WorkspaceClient()` (token do notebook)
* Account ID hardcoded: `f05f5725-04c9-4816-9d48-a847bd489baf`

**Idempotência:** O script é idempotente — regras existentes de outros roles (ex: `tagPolicy.manager`) são preservadas, e a regra de `tagPolicy.assigner` é substituída integralmente a cada execução.

**Riscos e cuidados:**
* A propagação de permissões pode levar até 30 segundos após a execução
* Remover um principal da lista `principals` e reexecutar **revoga** o ASSIGN desse principal (o rule set é substituído por completo)
* O `ACCOUNT_ID` está fixo no código — se migrar para outra conta, deve ser atualizado