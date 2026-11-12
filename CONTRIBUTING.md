# Contributing to HoneyMOM

Thank you for helping. This page explains how to set up a development environment, which checks to run and how
pull requests are handled.

## Before you start

- **Bugs:** open an issue with steps to reproduce (the bug template asks for version, deployment and database).
- **Features and larger changes:** open an issue first and describe the shop-floor problem. Agreeing on the approach
  before you write code saves both sides time.
- **Security problems:** do not open a public issue; see [SECURITY.md](SECURITY.md).
- **Code of conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) applies to all project spaces.

## Contributor License Agreement

HoneyMOM is dual-licensed (AGPL-3.0-or-later and commercial, see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)).
Before we can merge your first pull request you sign the [Individual CLA](CLA.md) once. The CLA bot posts a comment on
your pull request; reply with the sentence it shows. You keep the copyright in your contribution; the CLA allows
HoneyMeta to distribute it under the AGPL and under commercial terms.

## Development setup

Requirements: Python 3.12, Node.js 20 or later, Git. PostgreSQL 16 or 17 is optional (SQLite is the default).

```bash
git clone https://github.com/HoneyMeta/HoneyMOM.git
cd HoneyMOM
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r replayer/requirements.txt
cd web && npm ci && npm run build && cd ..

python -m replayer                       # terminal 1: plant data on MQTT :1883, OPC UA :4840, Modbus :5020
MOM_DEMO=1 python -m honeymom serve      # terminal 2: http://localhost:8900, admin / honey123
cd web && npm run dev                    # optional, terminal 3: Vite dev server on :5190 with hot reload
```

`MOM_DATA=<dir>` keeps a separate data directory per experiment; delete it to start from a fresh database.

## Project layout

| Path | Content |
|---|---|
| `honeymom/schema.py`, `schema_*.py` | Entity definitions: one definition gives the table, the generic API `/api/e/<entity>` and the list/form pages |
| `honeymom/migrations/` | Versioned migrations `vNNNN_*.py`, applied at start-up |
| `honeymom/services/` | Production, quality, equipment, warehouse, foundry, machining, AI and other services |
| `honeymom/erp/`, `honeymom/factory/`, `honeymom/impl/`, `honeymom/agent/` | Built-in ERP, factory model and onboarding, configuration tools, agent engine |
| `honeymom/collector.py`, `ingest.py`, `ts.py` | Protocol connectors, tag-to-record mapping, time series |
| `honeymom/locale/` | Backend translations (`en.json`) and English demo data (`data-en.json`) |
| `honeymom/packs/` | Industry templates (data, not code) |
| `web/src/` | Vue 3 front end; translations in `web/src/locales/` |
| `replayer/` | Plant data replayer |
| `tests/` | Checks (`*_check.py`), API tests, browser walk-throughs (`*_ui.js`) |
| `docs/` | User documentation; `docs/dev/` holds developer notes |

## Rules that the checks enforce

- **Schema changes need a migration.** If you change `schema*.py`, add `honeymom/migrations/vNNNN_<name>.py`. At
  start-up the server compares the database with the schema and refuses to start on drift.
  `python -m honeymom db upgrade --dry-run` shows what will run, `python -m honeymom db check` reports drift. See
  `docs/dev/foundation.md` for the migration API.
- **Both databases.** Code runs on SQLite and PostgreSQL. Use SQLAlchemy Core expressions; when you need dialect SQL,
  test both.
- **Translations.** Backend message ids are the original Chinese texts wrapped in `_tr(...)`, with English in
  `honeymom/locale/en.json`; demo and seed data go to `honeymom/locale/data-en.json`. The front end has no CJK
  characters in `web/src` outside `web/src/locales/zh-CN.json`; `en.json` and `zh-CN.json` must have the same keys.
  Run `python tests/i18n_check.py` and `python tests/i18n_web_check.py`.
- **Write operations go through the business layer.** Use `db.insert / db.update / db.delete` (they write the audit
  log) and the existing services in `honeymom/services/ops.py` for actions that AI or agents can trigger; AI-triggered
  writes always need a confirmation or approval step.
- **Permissions.** New endpoints check permission points and data scopes like the existing ones (`honeymom/perms.py`).
- **No secrets or personal data.** No API keys, passwords, tokens, real customer or person names in code, fixtures,
  recordings or screenshots. Demo data uses fictional names.

## Tests

Checks that run without a browser and without other services (these run in CI):

```bash
python tests/i18n_check.py
python tests/i18n_web_check.py
python tests/code_check.py
python tests/foundation_check.py          # --db pg / --db pgplain with FC_PG=postgresql+psycopg://user:pw@host:port
python tests/impl_check.py
python tests/erp_check.py                 # --db pg with FC_PG
MOM_DEMO=1 python -m honeymom seed && python tests/agents_check.py
```

End to end against the replayer (start `python -m replayer` and `MOM_DEMO=1 python -m honeymom serve` first):

```bash
MES_URL=http://127.0.0.1:8900 REPLAYER_MQTT=mqtt://127.0.0.1:1883 \
REPLAYER_OPCUA=opc.tcp://127.0.0.1:4840/vfactory/ REPLAYER_MODBUS=127.0.0.1:5020 \
python tests/replayer_check.py 90
python tests/exec_check.py
```

Browser walk-throughs use Playwright (`node tests/shell_ui.js`, `pages_ui.js`, ...; set `MES_URL`). Run the ones that
cover the pages you changed. The web build must pass: `cd web && npm run build`.

## Pull requests

- Branch from `main`, keep a pull request to one topic, and describe what changed and how you tested it.
- Keep commits focused; we squash or rebase when merging.
- Update the documentation in `docs/` and the README when behaviour, configuration or commands change.
- New dependencies: explain why, and check that the license is compatible with the AGPL and with commercial
  distribution (MIT, BSD, Apache-2.0, ISC, PSF, LGPL are fine; GPL-only and non-commercial licenses are not). Add
  runtime dependencies to [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
- A maintainer reviews the change. CI must be green and the CLA signed before merging.

## Translations, templates and recordings

- Translations: add or correct entries in `honeymom/locale/*.json` and `web/src/locales/*.json`.
- Device and industry templates are data (`honeymom/packs/<code>/pack.json`, device templates in the factory model);
  see `docs/dev/config.md` and `docs/dev/factory-model.md`.
- Replayer recordings: follow [replayer/README.md](replayer/README.md) and make sure a recording contains no real
  company, person or site names before you submit it.
