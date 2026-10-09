-- prd_governance
CREATE SCHEMA IF NOT EXISTS prd_governance.corp_raw
MANAGED LOCATION 'abfss://raw@sadatalakevcbr.dfs.core.windows.net/Governance';

CREATE SCHEMA IF NOT EXISTS prd_governance.corp_cleansed
MANAGED LOCATION 'abfss://cleansed@sadatalakevcbr.dfs.core.windows.net/Governance';

CREATE SCHEMA IF NOT EXISTS prd_governance.corp_curated
MANAGED LOCATION 'abfss://curated@sadatalakevcbr.dfs.core.windows.net/Governance';
