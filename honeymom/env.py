"""Environment variables, with the pre-HoneyMOM names still honoured.

New names use the MOM_ prefix (HONEYMOM_API_KEY for the stdio MCP key). The 5.x names (HM_*, MES_*, HONEYMES_API_KEY)
keep working so existing installs, services and scripts need no change. When both are set, the new name wins.
Defaults (data directory <root>/data, SQLite file mes.db) are unchanged.
"""
import os

LEGACY = {
    'MOM_DATA': 'HM_DATA',
    'MOM_DB': 'MES_DB',
    'MOM_DB_POOL': 'MES_DB_POOL',
    'MOM_SQLITE_TIMEOUT': 'MES_SQLITE_TIMEOUT',
    'MOM_BLANK': 'MES_BLANK',
    'MOM_BACKUP_DIR': 'MES_BACKUP_DIR',
    'MOM_PG_BIN': 'MES_PG_BIN',
    'MOM_PG_ADMIN_URL': 'MES_PG_ADMIN_URL',
    'MOM_TS_TIMESCALE': 'MES_TS_TIMESCALE',
    'MOM_PACKS': 'HM_PACKS',
    'MOM_AI_FIXTURES': 'HM_AI_FIXTURES',
    'MOM_PDF_BROWSER': 'HM_PDF_BROWSER',
    'MOM_LLM_BASE_URL': 'MES_LLM_BASE_URL',
    'MOM_LLM_API_KEY': 'MES_LLM_API_KEY',
    'MOM_LLM_MODEL': 'MES_LLM_MODEL',
    'HONEYMOM_API_KEY': 'HONEYMES_API_KEY',
}


def get(name: str, default=None):
    """Value of `name`, else of its legacy name (LEGACY), else `default`. Empty strings count as unset."""
    for n in (name, LEGACY.get(name)):
        if n:
            v = os.environ.get(n)
            if v not in (None, ''):
                return v
    return default
