# Configuration

HoneyMOM is configured in three places:

1. **Environment variables**, read at start-up (this page).
2. **Command-line options** of `python -m honeymom`.
3. **Settings in the UI** (System menu), stored in the database: company, shifts, roles, AI switches, backup
   schedule, MCP limits and so on.

## Environment variables

Many variables were renamed for HoneyMOM. The old names (column "Old name") still work, so existing installations and
scripts need no change; when both are set, the new name wins. Empty values count as unset.

### Mode and language

| Variable | Old name | Default | Meaning |
|---|---|---|---|
| `MOM_DEMO` | – | off | `1` / `true` / `yes` / `on`: demo mode. Demo accounts (`admin`, `manager`, `planner`, `leader`, `operator`, `qc`, `maint`, `wh`, `engineer`, password `honey123`) and the demo plant are created when the database is first seeded, and the demo autopilot releases work orders. Off: production install without accounts or demo data; the first start asks for the administrator. |
| `MOM_DEMO_LANG` | – | `en` | Language of the demo dataset: `en` (fictional plant "Northfield Castings") or `zh`. Only used when the database is first seeded. |
| `MOM_LANG` | – | demo language in demo mode, else `en` | Server default language (`en` or `zh-CN`) for requests that do not send one and for texts created in the background (notifications, AI watch, reports). The web UI sends `X-Lang` (or `?lang=`). |
| `MOM_BLANK` | `MES_BLANK` | off | `1` forces a blank install even with `MOM_DEMO=1`. |
| `MOM_ADMIN_PASSWORD` | – | – | Password used by `python -m honeymom create-admin` instead of prompting. |

### Database and data directory

| Variable | Old name | Default | Meaning |
|---|---|---|---|
| `MOM_DB` | `MES_DB` | `sqlite:///<MOM_DATA>/mes.db` | SQLAlchemy URL. SQLite: `sqlite:///path/to/mes.db`. PostgreSQL: `postgresql+psycopg://user:password@host:5432/dbname` (URL-encode special characters in the password). `--db` on the command line overrides it. |
| `MOM_DATA` | `HM_DATA` | `<repository>/data` | Writable data directory: SQLite database, uploaded files, backups, caches. |
| `MOM_DB_POOL` | `MES_DB_POOL` | `10` | PostgreSQL connection pool size (plus up to 20 overflow connections). |
| `MOM_SQLITE_TIMEOUT` | `MES_SQLITE_TIMEOUT` | `30` | Seconds SQLite waits for a lock. |
| `MOM_TS_TIMESCALE` | `MES_TS_TIMESCALE` | `1` | `0`: do not use TimescaleDB even when the extension is available; time series then use native PostgreSQL partitioning. Read when the time-series migration runs. |

### Backups

| Variable | Old name | Default | Meaning |
|---|---|---|---|
| `MOM_BACKUP_DIR` | `MES_BACKUP_DIR` | `<MOM_DATA>/backups` | Directory for backup archives. |
| `MOM_PG_BIN` | `MES_PG_BIN` | search | Directory containing `pg_dump` and `pg_restore` for PostgreSQL backups and restores (otherwise the usual system locations are searched). Use the same major version as the server. |
| `MOM_PG_ADMIN_URL` | `MES_PG_ADMIN_URL` | – | PostgreSQL administrator URL on the same server, e.g. `postgresql+psycopg://postgres:password@host:5432`. Restores and restore drills use it to create the target database and restore with the original owners; without it they use the application's own database user. |

### AI

| Variable | Old name | Default | Meaning |
|---|---|---|---|
| `MOM_LLM_BASE_URL` | `MES_LLM_BASE_URL` | – | OpenAI-compatible endpoint, e.g. `https://api.example.com/v1`. |
| `MOM_LLM_MODEL` | `MES_LLM_MODEL` | – | Model name. |
| `MOM_LLM_API_KEY` | `MES_LLM_API_KEY` | – | API key for the endpoint. |
| `MOM_AI_FIXTURES` | `HM_AI_FIXTURES` | `honeymom/fixtures/ai_demo` | Directory of recorded answers for AI demo mode. |
| `HONEYMOM_API_KEY` | `HONEYMES_API_KEY` | – | API key for `python -m honeymom mcp` (stdio MCP) when `--key` is not given. |

There are no built-in model defaults. AI calls a model only when base URL, model and key are all set (here or in
`config/llm.local.json`; the variables win). See [ai.md](ai.md).

### Other

| Variable | Old name | Default | Meaning |
|---|---|---|---|
| `MOM_PACKS` | `HM_PACKS` | `honeymom/packs` | Directory of industry templates. |
| `MOM_PDF_BROWSER` | `HM_PDF_BROWSER` | auto-detect | Chrome, Chromium or Edge executable used in headless mode to print the AI daily report as PDF. Searched on `PATH` and in the usual Windows locations. Other PDFs (labels, report designer, commissioning report) are generated without a browser. The Docker image contains no browser, so the daily-report PDF export is not available there unless you add one. |
| `ERP_LOOP` | – | `1` | `0` turns off the background loop of the external ERP integration (webhook delivery and retries, staging-database exchange). |
| `ERP_WEBHOOK_TIMEOUT` | – | `10` | Seconds to wait for an external ERP webhook. |
| `ERP_STAGING_URL` | – | – | SQLAlchemy URL of a separate staging database for the external ERP integration. Takes precedence over the system parameter `erp_staging_url`; when neither is set, the staging tables `erp_stg_in` / `erp_stg_out` in the HoneyMOM database are used. |
| `TZ` | – | system | Time zone (Docker). Business times are local times; set the same time zone for HoneyMOM and the replayer. |

Variables such as `MES_URL`, `VF_*`, `REPLAYER_*`, `FC_PG` and `E2E_*` are used by the tests only.

## Command line

```
python -m honeymom serve [--http 8900] [--host 0.0.0.0] [--db URL]
                                          start the server; creates and migrates the database on first start
python -m honeymom create-admin [--username admin] [--name NAME]
                                          production install: create the first administrator
python -m honeymom seed                   complete the base data (idempotent); prints the demo accounts in demo mode
python -m honeymom db status              applied and pending migrations
python -m honeymom db upgrade [--dry-run] run pending migrations; --dry-run lists the planned actions
python -m honeymom db check               compare the database with the schema; exit code 1 on drift
python -m honeymom backup [--note TEXT]   back up the database and uploaded files now
python -m honeymom restore BACKUP.zip --to NEW_DATA_DIR [--to-db URL]
                                          restore into a new data directory (and a new PostgreSQL database)
python -m honeymom audit verify           verify the audit log hash chain
python -m honeymom mcp [--key KEY] [--user USER]
                                          MCP server over stdio
```

All commands use `MOM_DB` / `--db` and `MOM_DATA` the same way as the server.

The replayer has its own options; see [connecting-equipment.md](connecting-equipment.md#the-replayer) and
[replayer/README.md](../replayer/README.md).

## Configuration files

| File | Purpose |
|---|---|
| `config/llm.local.json` | Model endpoint, model and key (git-ignored). Template: `config/llm.example.json`. |
| `.env` (Docker) | Variables for `docker-compose.yml`. Template: `docker/env.example`. Do not commit it. |

## Settings in the UI

Under **System** (administrators; the exact pages depend on the role's permissions):

- **Roles & Permissions:** roles with page, button and API permission points and data scopes (plant, workshop, line).
- **Users**, **API Keys** (header `X-API-Key`; permissions and scope like roles, expiry, IP allow-list; only hashes are
  stored), **MCP Access**.
- **AI features:** master switch, feature switches, run mode (see [ai.md](ai.md)).
- **Backup & Restore:** schedule, manual backups, restore drills.
- **Audit Trail:** who changed what, before and after, source (UI, API, AI, agent, import, collector), IP; append-only.
- System parameters such as `backup_enabled`, `backup_time`, `mcp_rate_per_min`, `mcp_allowed_origins`.

The **Implementation** menu holds configuration without code: custom fields, form layouts, approval flows, the report
designer, industry templates, data import and the **Setup Wizard**.
