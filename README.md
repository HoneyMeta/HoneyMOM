# HoneyMOM

**English** | [简体中文](README.zh-CN.md)

[![CI](https://github.com/HoneyMeta/HoneyMOM/actions/workflows/ci.yml/badge.svg)](https://github.com/HoneyMeta/HoneyMOM/actions/workflows/ci.yml)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)

HoneyMOM is an open-source manufacturing operations management (MOM/MES) system for discrete plants, built first
for foundries, machine shops and plants that do both. It covers the four ISA-95 Level 3 areas (production, quality,
maintenance and inventory operations), adds a built-in ERP for sales, purchasing, MRP and costing, and collects machine
data over MQTT, Sparkplug B, OPC UA, Modbus TCP, HTTP push and SQL staging tables. AI features (an assistant that
queries plant data, daily reports, anomaly watch, agents with human approval, an MCP endpoint for external AI tools)
work with any OpenAI-compatible model and stay off until an administrator enables them. The backend is Python
(FastAPI, SQLAlchemy) on SQLite or PostgreSQL, the web UI is Vue 3. A data replayer plays back a recorded 24-hour day
of a demo foundry over MQTT, OPC UA and Modbus, so you can try everything without a plant.

- [Features](#features)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [AI configuration](#ai-configuration)
- [Architecture](#architecture)
- [Roadmap](#roadmap)
- [License](#license)
- [Contributing](#contributing)

## Features

Grouped by the ISA-95 (IEC 62264) activity areas. The left-hand menu of the web UI follows the same grouping.

### Production operations

- Master data: plants, workshops, lines, workstations, materials, BOMs, routings, shifts and teams.
- Work orders, finite-capacity scheduling by priority and due date with constraint markers (material shortage,
  equipment downtime, operator skills, molds and fixtures, changeover times), what-if schedule change proposals,
  dispatching.
- Production reporting from the desktop, a workstation terminal (`/station`) and a mobile PWA (`/m`, barcode scanning).
  Reporting gates reject duplicate, over-quantity and out-of-sequence reports, require first-article release, and
  block reports on held lots.
- Serialized parts and lots, traceability from part to process data and back, traceability reconciliation.
- Andon, clock-in/out, labor hours, operator daily reports with approval, production daily report.
- Cycle-time analysis down to individual machine actions (for example clamp, spindle, door), with baselines and drift.
- Foundry: heat records (chemistry, temperature, power), nodularization records, least-cost charge optimization
  (linear programming).
- Machining: NC program approval and release, tool register and tool life, tool-wear estimation from spindle load.
- Casting–machining: defects found in machining traced back to heat, ladle, pouring temperature, residual magnesium and
  sand parameters (chi-square test), coordinated scheduling of both sections.
- Labels (ZPL, TSPL, SVG, PDF) and laser marking, packing, palletizing, shipping notices (JSON, Excel, flat file,
  EDIFACT), subcontracting.

### Quality operations

- Inspection templates; incoming, in-process, first-article and final inspection.
- Nonconformance reports with disposition, rework and re-inspection, 8D reports, SPC and process capability.
- PFMEA with action priority (AIAG-VDA 2019) → control plan → inspection templates.
- Gauges and calibration with release by electronic signature; MSA (GR&R by average-and-range and ANOVA, bias,
  linearity).
- Controlled documents and engineering change notices (ECN) with impact analysis and electronic signatures.
- Customer complaints, containment of affected stock, recall simulation.

### Maintenance operations

- Equipment register and live monitor, alarms, OEE computed from machine states and part events (no manual entry).
- Inspection and preventive maintenance plans, repair orders, spare parts linked to repairs, energy per hour.

### Inventory operations

- Stock, receipts and issues, stocktaking, lot hold and release, material requests and kitting, AGV transport tasks.

### Planning and business (built-in ERP)

- Sales: price lists, orders with delivery schedules, credit checks, quotations, shipping, returns, statements.
- Purchasing: approved suppliers, requisitions, purchase orders, receiving with incoming inspection, supplier ratings.
- MRP: multi-level BOM explosion netted against stock, open orders and WIP, lead-time offsetting, pegging to the
  demand, rough-cut capacity; suggestions released as work orders or purchase orders.
- Costing (moving average, actual work-order cost, standard cost roll-up, variances, period close), receivables and
  payables, accounting vouchers with configurable export formats.
- External ERP mode: when another ERP is the system of record, HoneyMOM exchanges data through a REST API, signed
  webhooks with retries and a staging database.

### Equipment connectivity

- Data sources: MQTT (unified namespace topics), Sparkplug B, OPC UA (subscribe or poll, address-space browsing),
  Modbus TCP, HTTP push and SQL staging tables.
- Onboarding from a delivery package (point table `points.xlsx`, floor plan `layout.dxf`, 3D models `*.glb`):
  validate, preview, confirm, then check and confirm tag by tag; commissioning acceptance report.
- Time series for every tag (raw values, 1-minute and 1-hour rollups, retention; TimescaleDB when available),
  store-and-forward when the database is unavailable, 2D/3D plant map with live states.

### AI (off by default)

- Assistant panel on every page: questions answered from read-only SQL and business queries under the user's
  permissions; write actions (repair order, NCR, andon, material request, schedule proposal) only after the user
  confirms a draft.
- Daily report with AI commentary, AI watch over quality, equipment, schedule and material, one-sentence automations,
  knowledge-base Q&A, AI-assisted onboarding (column mapping, tag meaning, reading PLC programs).
- Agents: a visual flow editor with chat, schedule, event and webhook triggers, human approval steps, step-by-step
  debugging, versions and an action allow-list.
- MCP endpoint (`/mcp`) so external AI tools can query plant data with an API key.
- Demo mode replays recorded answers without calling a model.

### Platform

- Roles with page, button and API permissions plus data scopes (plant, workshop, line); audit log with hash chain;
  API keys with expiry and IP allow-list.
- Versioned database migrations, daily and manual backups with restore drills.
- Configuration without code: custom fields, form layouts, approval flows, report designer, industry templates, an
  Excel import framework and a set-up wizard.
- Industry templates for foundry, machining, injection molding and assembly.
- English and Simplified Chinese UI and demo data.

## Screenshots

Demo plant "Northfield Castings" fed by the replayer.

| | |
|---|---|
| ![Home: production cockpit with live plant map](docs/images/home.webp) | ![Scheduling and dispatch](docs/images/scheduling.webp) |
| Home: KPIs per operations area, live plant map, alerts | Finite-capacity scheduling with constraint markers |
| ![Live equipment monitor](docs/images/equipment-monitor.webp) | ![OEE by machine](docs/images/oee.webp) |
| Live monitor of all machines | OEE by machine, computed from machine states and part events |
| ![Quality overview](docs/images/quality.webp) | ![Connectivity: delivery package import](docs/images/connectivity.webp) |
| Quality overview: defect Pareto, NCR disposition | Connectivity: import a delivery package, check tags |

## Quick start

### Docker (demo)

Requires Docker with Compose v2.

```bash
git clone https://github.com/HoneyMeta/HoneyMOM.git
cd HoneyMOM
docker compose up
```

The first run builds the image (a few minutes). Then open http://localhost:8900 and log in as `admin` with password
`honey123`. Other demo accounts: `manager`, `planner`, `leader`, `operator`, `qc`, `maint`, `wh`, `engineer` (same
password). The cockpit, plant map and OEE fill within about a minute as the replayer sends data.

`docker compose up` starts three containers: `mom` (HoneyMOM and the web UI), `db` (PostgreSQL 17) and `replayer`.

For production (no demo data, the first start asks you to create the administrator):

```bash
cp docker/env.example .env        # set POSTGRES_PASSWORD, TZ, ...
docker compose -f docker-compose.yml -f docker/compose.production.yml up -d
```

See [docs/deployment.md](docs/deployment.md) for production, backups and upgrades.

### Manual setup (development)

Requires Python 3.12 and Node.js 20 or later.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r replayer/requirements.txt
cd web && npm ci && npm run build && cd ..

# terminal 1: plant data
python -m replayer

# terminal 2: HoneyMOM in demo mode, SQLite database in data/mes.db
MOM_DEMO=1 python -m honeymom serve  # Windows: set MOM_DEMO=1 && python -m honeymom serve
```

Open http://localhost:8900. Without `MOM_DEMO=1` you get a production install: no accounts and no demo data; create
the administrator in the browser or with `python -m honeymom create-admin --username admin`.

For front-end work, run `npm run dev` in `web/` (Vite on port 5190, `/api` is proxied to port 8900).

More in [docs/getting-started.md](docs/getting-started.md).

## Configuration

HoneyMOM reads environment variables at start-up. The most used ones:

| Variable | Default | Meaning |
|---|---|---|
| `MOM_DEMO` | off | `1`: demo accounts (password `honey123`) and the demo plant. Off: production install. |
| `MOM_DEMO_LANG` | `en` | Demo dataset language, `en` or `zh`. Used when the database is first seeded. |
| `MOM_LANG` | demo language, else `en` | Default language for requests without a language and for background texts. |
| `MOM_DB` | `sqlite:///<data dir>/mes.db` | Database URL, e.g. `postgresql+psycopg://user:password@host:5432/honeymom`. |
| `MOM_DATA` | `./data` | Data directory: SQLite database, uploaded files, backups. |
| `MOM_BACKUP_DIR` | `<data dir>/backups` | Where backups are written. |
| `MOM_LLM_BASE_URL`, `MOM_LLM_API_KEY`, `MOM_LLM_MODEL` | – | OpenAI-compatible model endpoint for the AI features. |
| `MOM_ADMIN_PASSWORD` | – | Password for `python -m honeymom create-admin` without a prompt. |

Variables from earlier versions (`MES_DB`, `HM_DATA`, `MES_LLM_*`, other `MES_*` / `HM_*`) still work; the `MOM_*`
name wins when both are set.

`python -m honeymom serve` also takes `--http <port>` (default 8900), `--host` (default `0.0.0.0`) and `--db <url>`.
The full list is in [docs/configuration.md](docs/configuration.md). Plant-specific settings (company, shifts,
backup time, AI switches, MCP limits) are changed in the UI under **System**.

## AI configuration

The AI features use the OpenAI chat-completions API format (`POST <base_url>/chat/completions`, with tool calling),
so they work with hosted services and with self-hosted servers such as vLLM, Ollama or LM Studio that offer an
OpenAI-compatible endpoint.

1. Configure the endpoint, either with environment variables

   ```bash
   MOM_LLM_BASE_URL=https://api.example.com/v1
   MOM_LLM_API_KEY=...
   MOM_LLM_MODEL=your-model-name
   ```

   or by copying `config/llm.example.json` to `config/llm.local.json` (git-ignored) and filling it in. Environment
   variables take precedence over the file; the old names `MES_LLM_*` still work.
2. Log in as an administrator, open **System → AI features**, turn on the master switch and the features you want.

HoneyMOM ships with no model endpoint, model name or key, and no keys are stored in the repository. AI stays off until
all three values are configured: until then the AI features use rule-based results, and demo mode replays recorded
answers without calling a model. When a model is configured, prompts that contain
plant data (query results, page context) are sent to that endpoint. Details: [docs/ai.md](docs/ai.md).

## Architecture

```
  Browser: desktop UI, workstation terminal,      External AI tools      External ERP
  mobile PWA, shop-floor display (Vue 3)          (MCP, /mcp)            (REST, webhooks, staging DB)
                 │ HTTP /api                            │                      │
 ┌───────────────┴──────────────────────────────────────┴──────────────────────┴───────┐
 │ HoneyMOM: one Python process (FastAPI)                                               │
 │   production · quality · maintenance · inventory · built-in ERP · scheduling         │
 │   AI assistant, agents, watch · permissions · audit · backup · migrations            │
 │   collector (MQTT, Sparkplug B, OPC UA, Modbus TCP, HTTP push, SQL)                  │
 │     → ingest worker → business records + time series                                 │
 └───────────────┬─────────────────────────────────────────────┬────────────────────────┘
                 │ SQLAlchemy                                  │ MQTT / OPC UA / Modbus TCP
      SQLite or PostgreSQL (+ TimescaleDB)          PLCs, gateways, brokers, or the replayer
```

- `honeymom/schema*.py`: entity definitions. One definition creates the database table, the generic REST API
  (`/api/e/<entity>`) and the list and form pages in the UI.
- `honeymom/services/`, `honeymom/erp/`, `honeymom/factory/`, `honeymom/impl/`, `honeymom/agent/`: business
  services, built-in ERP, factory model and onboarding, configuration tools, agent engine.
- `honeymom/collector.py`, `honeymom/ingest.py`, `honeymom/ts.py`: protocol connectors, mapping of tag values to
  business records (machine states, alarms, energy, counters, tool life, part events), time series.
- `honeymom/migrations/`: versioned migrations, run automatically at start-up.
- `web/`: Vue 3 single-page app, built into `web/dist` and served by the backend.
- `replayer/`: plays back recordings over the same interfaces a plant offers; see [replayer/README.md](replayer/README.md).
- `tests/`: API and in-process checks (`*_check.py`), browser walk-throughs (`*_ui.js`).

Business time follows the data clock (timestamps of the collected data), so reports stay correct when a source runs
faster than real time.

## Roadmap

Possible next steps, not commitments; discussion and contributions are welcome in the issues:

- more industry templates and device templates;
- more UI and demo-data languages;
- a Helm chart for Kubernetes and an official image with TimescaleDB;
- replaying OPC UA history and alarm events in the replayer;
- running the browser walk-throughs (Playwright) in CI.

## License

Copyright (C) 2026 蜂元科技（郑州）有限责任公司 (HoneyMeta Technology (Zhengzhou) Co., Ltd.)

HoneyMOM is licensed under the **GNU Affero General Public License v3.0 or later** ([LICENSE](LICENSE)). If you run a
modified version for users over a network, the AGPL requires you to offer them its source code.

A **commercial license** is available for organisations that want to use HoneyMOM without the AGPL obligations, for
example in a proprietary product or a hosted service, and it can include support with an SLA. See
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) or contact dev@honeymeta.com, https://www.honeymeta.com.

Third-party components and acknowledgements: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Contributing

Bug reports, fixes, translations, device templates and industry templates are welcome. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first. Contributors sign the [CLA](CLA.md) once; the CLA bot asks on your first pull
request. Please follow the [code of conduct](CODE_OF_CONDUCT.md) and report security problems privately as described in
[SECURITY.md](SECURITY.md).

Main repository: https://github.com/HoneyMeta/HoneyMOM. Read-only mirrors on Gitee and GitCode.
