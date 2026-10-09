-- dev_governance
CREATE SCHEMA IF NOT EXISTS dev_governance.corp_raw
MANAGED LOCATION 'abfss://raw@sadatalakedevvcbr.dfs.core.windows.net/Governance';

CREATE SCHEMA IF NOT EXISTS dev_governance.corp_cleansed
MANAGED LOCATION 'abfss://cleansed@sadatalakedevvcbr.dfs.core.windows.net/Governance';

CREATE SCHEMA IF NOT EXISTS dev_governance.corp_curated
MANAGED LOCATION 'abfss://curated@sadatalakedevvcbr.dfs.core.windows.net/Governance';
