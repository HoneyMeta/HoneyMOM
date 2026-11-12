# AI features

HoneyMOM's AI features are part of the open-source code. They use an OpenAI-compatible model endpoint that you
configure. HoneyMOM ships with no model endpoint, model name or key: until base URL, model and key are all configured,
no model is called and the AI features use their rule-based versions. In addition, every AI feature is **off by
default** until an administrator turns it on. Everything else in HoneyMOM works without a model.

## 1. Configure a model

HoneyMOM calls `POST <base_url>/chat/completions` in the OpenAI format, with `tools` (function calling) for the
assistant and agents. Any service or self-hosted server with that API works, for example a hosted provider, vLLM,
Ollama (`http://host:11434/v1`) or LM Studio. Use a model that supports tool calling.

Configure all three values (base URL, model, key); the model counts as configured only when all three are set.
Sources, in increasing order of precedence:

1. `config/llm.local.json` (copy `config/llm.example.json`; the file is git-ignored):

   ```json
   {"base_url": "https://api.example.com/v1", "model": "your-model", "api_key": "..."}
   ```

2. environment variables `MOM_LLM_BASE_URL`, `MOM_LLM_MODEL`, `MOM_LLM_API_KEY` (the old names `MES_LLM_*` still
   work; the new name wins when both are set).

With Docker, put the variables in `.env` next to `docker-compose.yml` (see `docker/env.example`); they are passed to
the `mom` container. Never commit keys.

`GET /api/ai/status` reports whether a model is configured (`configured`), the model and base URL, and the current
run mode. Check the connection from the command line (needs data in the database):

```bash
python tests/llm_check.py "Which machine produced the fewest parts today?"
```

## 2. Turn features on

**System → AI features** (administrators): a master switch and one switch per feature. Stored in the `setting` table
(key `ai_switches`); API `GET/PUT /api/ai/switches`.

| Key | Feature |
|---|---|
| `ask` | AI assistant panel, ask-data page, "Ask AI" buttons, AI drafting of 8D reports |
| `daily` | daily production report with AI analysis |
| `watch` | AI watch: explains equipment anomalies, watches quality, equipment, schedule and material and drafts actions |
| `automation` | one-sentence automation rules; agents running on events, schedules and webhooks (also needs `agents`) |
| `tool_wear` | tool-wear estimation from spindle load (off: tool life by count only) |
| `charge_opt` | least-cost charge optimization for melting |
| `kb` | knowledge-base and SOP question answering |
| `agents` | agent editor, debug runs, approval of agent actions |
| `backtrace` | AI interpretation on the defect back-tracing page (the statistics work without AI) |
| `coplan` | AI suggestions for casting–machining co-scheduling (the scheduling itself works without AI) |
| `onboard` | onboarding assist: column mapping, tag meaning/unit/equipment/role drafts, tags from PLC programs |
| `impl` | import assist: suggests which field a customer's spreadsheet column maps to |
| `mcp` | MCP endpoint for external AI tools (`/mcp`) |
| `cycle` | AI explanations in action-level cycle analysis, cycle drift watch |
| `skills` | save a question as a skill and run it on parameters or a schedule |
| `quality_plus` | quality assistant: PFMEA item drafts from defect history, complaint impact, reaction plans |
| `erp` | ERP assistant: explains MRP suggestions, finds orders at risk, drafts purchase orders |

When a feature is off, its API returns HTTP 403 with `{"code": "ai_disabled", "feature": "<key>"}`, the menu entry is
greyed out and the status bar shows that AI is off.

### Run modes

On the same page you choose how AI calls are answered:

- **Live:** calls the configured model.
- **Demo:** replays recorded answers and makes no model calls (no tokens). Questions without a recording get the
  rule-based answer. Recordings are in `honeymom/fixtures/ai_demo/<feature>.json`; `MOM_AI_FIXTURES` points to another
  directory.
- **Record demo:** calls the model and records the answers for demo mode.

Without a configured model (base URL, model and key), or with the master switch off, all calls fall back to the
rule-based versions. `GET /api/ai/switches` reports the mode as `off`, `live`, `demo` or `record`.

## 3. What the features do

### Assistant panel

On every page, on the right edge. Each message carries the current page, the selected record and any elements you
added with **Ctrl+click** (or the right-click menu: ask AI, explain this number, why did it change, trace, report).

- Answers come from tools: read-only SQL and business queries, run with the user's permissions and data scope.
  Numbers in an answer must come from a query result.
- The assistant can take you to the relevant page (role-based allow-list).
- Write actions (repair order, NCR or 8D, andon, material request, schedule proposal) are shown as a confirmation
  card first. The server executes only when you confirm and the confirmation token still matches the content and
  the current data; roles without the permission do not get the write tools, and the server checks again on submit.
- Conversations are stored per user.

### Agents

**Intelligence → Agents**: flows that look at data, decide, draft, wait for approval and act.

- Triggers: chat, manual or form, schedule (cron or per shift), events (andon, machine state change, OEE below
  threshold, NCR, material below safety stock, work-order delay, machine alarm) and webhooks.
- Nodes: data queries, knowledge-base search, read-only SQL, HTTP; model calls (agent with tools, single call,
  classification, extraction); logic (branch, loop, variables, wait, reply); human steps (approval, form); actions
  (repair order, NCR/8D draft, schedule proposal, andon, material request, notifications, report).
- Safety: high-risk write actions must follow an approval node or have "approval before execution" set, otherwise
  the agent cannot run or be published. Each agent has an allow-list of actions; published agents act with the
  publisher's permissions; each node writes at most once per run; everything is written to the audit log.
- Debug runs are dry runs by default (write actions are recorded, not executed). Event triggers are debounced, have
  a cool-down per object and a limit on concurrent runs.
- Eight templates are included (shift handover report, OEE loss analysis, andon triage, quality issue → 8D draft,
  downtime → repair order, material shortage warning, work-order delay → reschedule proposal, SOP Q&A).
- The **Approval Center** (**Approvals** in the top bar) lists pending approvals from agents, AI drafts and approval flows. Rejecting with
  a reason sends the reason back to the AI step, which revises its draft.

### MCP endpoint

External AI tools can query HoneyMOM through the Model Context Protocol.

1. Turn on the master switch and **MCP data interface** (`mcp`).
2. Create a key under **System → MCP Access** (permission presets, line scope, expiry, IP allow-list; the key is
   shown once). Keys are managed under **System → API Keys**.
3. Connect:

   | Transport | Address / command | Authentication |
   |---|---|---|
   | Streamable HTTP | `POST http://<server>:8900/mcp` | header `X-API-Key: hmk_...` or `Authorization: Bearer hmk_...` |
   | stdio (on the server) | `python -m honeymom mcp --key hmk_...` (or env `HONEYMOM_API_KEY`, old name `HONEYMES_API_KEY`) | the same key |

Tools are listed only when the key has the permission: search and records, work orders, production, quality, OEE,
equipment, maintenance, energy, factory model and tags, time series (raw, rollups, latest, events), cycle analysis,
traceability, saved skills, read-only SQL, knowledge-base search. A `draft_action` tool (drafts that need approval)
is off by default. Calls are audited and rate-limited per key (setting `mcp_rate_per_min`, default 120); browser
requests are checked against `mcp_allowed_origins`. Client examples (Claude Desktop, Cursor, Dify, curl) are in
`docs/MCP接入说明.md`.

## 4. Data protection

- AI features send prompts to the endpoint you configure. Prompts contain plant data: query results, page context,
  selected records, knowledge-base excerpts. Choose a provider whose terms fit your data, or run a model on your own
  hardware.
- The model never gets database credentials. Queries run in HoneyMOM under the user's permissions and data scope.
- Every model call ends with an instruction to answer in the user's UI language.
- Model usage can be limited by turning individual features off.
