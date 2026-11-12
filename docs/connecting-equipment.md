# Connecting equipment

HoneyMOM collects machine data itself; no separate gateway product is required. This page explains the factory model,
the supported protocols, how to onboard a plant from a delivery package, and how to use the replayer.

## Concepts

```
plant ─ workshop ─ line ─ workstation ─ equipment ─ tag point ─ data source
```

- A **data source** is one connection: an MQTT broker, an OPC UA server, a Modbus TCP device, an HTTP push source or
  an SQL staging database.
- A **tag point** is one value: its address on the data source, the equipment it belongs to, meaning, unit, data type,
  limits, and its **role**, which decides where the value goes:

  | Role / semantic | Becomes |
  |---|---|
  | `state` | machine state (normalised to Executing, Idle, Setup, Stopped, Planned, Starved, Blocked) → state history and OEE |
  | `alarm` | alarm records (text as is; 1 = alarm; a non-zero number = alarm code) |
  | `energy` | difference of a cumulative energy meter → energy per hour |
  | `count` (semantic `output`, `good`, `bad`) | hourly output per machine (used for OEE where there are no part events) |
  | semantic `tool_life`, `tool_limit`, `tool_active` | tool register and tool-life tracking |
  | semantic `sample` | 10-second trend samples (used by AI monitoring, e.g. tool wear from spindle load) |
  | all | raw time series and the latest value |

- **Device templates** (25 built in: machining centre, molding line, induction furnace, die casting, injection molding,
  shot blasting, robot cell, conveyor, AGV, compressor, inspection station, ...) supply default points, roles and
  value maps when the point table does not.
- **Part events and the production log** from a line gateway create serialized parts, production reports, inspection
  results, NCRs, stock movements and heat records.

All business times follow the **data clock**, the timestamps of the collected data. If a source runs faster than real
time, reports, OEE and durations stay consistent.

## Protocols

| Protocol | Endpoint | Prefix / options | Tag address |
|---|---|---|---|
| `mqtt` | `mqtt://host:1883` | prefix = subscription root (`<prefix>#`) | full topic; payload JSON `{"value", "ts", "quality"}` or a plain number/text |
| `sparkplug` | `mqtt://host:1883` | prefix = Sparkplug group (empty = all groups) | `group/edge node/device:metric` (no device part for node metrics) |
| `opcua` | `opc.tcp://host:4840/path/` | option `mode`: `subscribe` (default) or `poll` | full NodeId, e.g. `ns=2;s=Plant/CNC1/Spindle/Load` |
| `modbus` | `host:502` | – | `unit:table:address`, table `hr`, `ir`, `coil` or `di`, addresses from 0; register `encoding` (`int16` default, `uint16`, `int32`, `uint32`, `float32`, `float64`, `int64`, word order `ABCD`/`CDAB`/`BADC`/`DCBA`), `scale`, `offset` |
| `http` | – | – | pushed by point code or address, see below |
| `sql` | SQLAlchemy URL (`postgresql+psycopg://…`, `mssql+pyodbc://…`, `sqlite:///…`) | `table` or `query` (with a `:wm` parameter), `tag_col`, `value_col`, `ts_col`, `watermark`, `layout` (`long` = one value per row, `wide` = one column per tag), `interval_s`, `batch` | long layout: value of the tag column; wide layout: column name |

MQTT topics that follow the HoneyMOM naming convention look like `<prefix><area>/<device>/<point path>`. A line
gateway can also publish part events on `<prefix>_mes/part` and the production log on `<prefix>_mes/event`.

ISO 8601 timestamps with a time zone (MQTT `ts`) and OPC UA SourceTimestamps are converted to the server's local time
(set `TZ` in Docker) and keep millisecond precision, which the action-level cycle analysis needs.

Protocol handling runs in the event loop; all database writes for collected values happen in one collector worker
thread in arrival order, so a slow or locked database does not block the web API. When the database is unavailable,
values are buffered locally and written later in the original order (duplicates are dropped).

### HTTP push

For sources that cannot be polled, push values with an API key (permission `onboard.push.write`; create keys under
**System → API Keys**):

```bash
curl -X POST http://mom:8900/api/onboard/push \
  -H "X-API-Key: hmk_..." -H "Content-Type: application/json" \
  -d '{"source": "LINE2-GW", "values": [{"code": "CNC7.Monitoring.Spindle.Load", "value": 42.5, "ts": "2026-11-11T08:00:00+01:00"}]}'
```

Each value is identified by `code` (point code) or `address`; `ts` and `q` (quality) are optional.

## Onboarding a plant

The intended path is the one a system integrator takes: deliver a **delivery package**, import it, check every tag,
publish the factory model.

### Delivery package

A zip file with:

| File | Content |
|---|---|
| `points.xlsx` | equipment list, tag list (point code, meaning, unit, data type, address, role, limits, ...) and a protocol sheet with the endpoints |
| `layout.dxf` | floor plan; equipment blocks placed by block attributes |
| `models/*.glb` | optional 3D models per equipment type (otherwise parametric models from the device template) |
| `README.md` | optional notes |

Column headers can use the customer's own wording; columns are matched by synonyms. A point-table template can be
downloaded from the connectivity page (`GET /api/onboard/points/template.xlsx`). The Chinese specification for
integrators, with a point-table template, is in `docs/接入规范/`.

### Steps in the UI

**Factory Model & Connectivity → Overview & Import**

1. **Upload Package (zip)**, or fetch it from a URL with an optional token. The import validates the package and shows
   a preview: equipment, lines, workstations, tags, data sources, floor plan, problems found. Nothing is written
   until you confirm. Re-importing a newer version of the package is idempotent and shows the differences.
2. **Tag Check:** each tag is coloured by what is actually received: green = values arriving and within the normal
   range; orange = values arriving but out of range or abnormal; red = no value yet, or none for longer than
   max(10 × the collection period, 10 minutes); grey = ignored. Yellow rows are addresses that send values but are not
   in the point table; they wait in a queue until you add or ignore them. Confirm tags one by one or all green tags at
   once.
3. **Discover & Browse:** MQTT discovery (listen for a few minutes and list topics), OPC UA address-space browsing and
   adding nodes as tags.
4. **Plant Model** and **Floor Plan & Digital Twin:** hierarchy, versions and differences, positions on the floor
   plan, 2D/3D view.
5. **AI Assist** (needs the AI feature "onboarding assist"): column mapping, drafts for tag meaning, unit, equipment and
   role, and new tags from PLC programs (PLCopen XML, PLC symbol tables). Drafts are applied only after approval.
6. Generate the **commissioning acceptance report** (Word or PDF) and publish the factory model version.

Without a package, **Import Tag List (Excel)** updates only the tag list, and data sources and tags can be edited
by hand under the same menu.

API equivalents: `POST /api/onboard/package` with `{"name": "plant.zip", "data": "<base64>"}` or
`{"url": "...", "token": "..."}`, then `POST /api/onboard/imports/{id}/confirm`; tag check `GET /api/onboard/check`;
see `docs/dev/factory-model.md` for the full list.

## The replayer

`replayer/` plays back a recorded 24-hour day of the demo foundry (`foundry-housing`) on the interfaces a plant
offers. Full reference: [replayer/README.md](../replayer/README.md).

| Interface | Default |
|---|---|
| MQTT (embedded broker) | `mqtt://<host>:1883`, topics `vfactory/<plant>/<area>/<device>/<point>`, gateway topics `.../_mes/part`, `.../_mes/event` |
| OPC UA | `opc.tcp://<host>:4840/vfactory/`, namespace `http://honeymeta.cn/vfactory`, nodes `ns=2;s=<plant>/<device>/<point>` |
| Modbus TCP | `<host>:5020`, one unit per device |

All timestamps, serial numbers and heat numbers are moved to "now"; every loop gets new dates.

```bash
python -m replayer                                   # MQTT :1883, OPC UA :4840/vfactory/, Modbus :5020, real time, loops
python -m replayer --speed 10                        # one recorded day in 2.4 hours
python -m replayer --mqtt-broker mqtt://broker:1883  # publish into an existing broker instead of the embedded one
python -m replayer info                              # what the recording contains
```

### Demo mode

With `MOM_DEMO=1` the demo plant is onboarded from the bundled delivery package at the first start, with data
sources `VF` (MQTT `mqtt://127.0.0.1:1883`), `VF-OPC` (`opc.tcp://127.0.0.1:4840/vfactory/`) and `VF-MB`
(`127.0.0.1:5020`). Started on the same machine with default ports, the replayer is picked up without configuration.
In `docker-compose.yml` the replayer shares the network namespace of the `mom` container for the same reason. If the
replayer runs elsewhere, change the endpoints of the three data sources under **Factory Model & Connectivity**.

### Onboarding a fresh install against the replayer

To practise the onboarding path on a production (non-demo) install, write a delivery package that points at the
replayer and import it:

```bash
python -m replayer package --host 127.0.0.1 --out foundry-housing-replayer.zip
```

`--host` is the address HoneyMOM uses to reach the replayer (`--mqtt-port`, `--opcua-port`, `--opcua-path` and
`--modbus-port` change the other parts). Then upload the zip under **Factory Model & Connectivity → Overview & Import
→ Upload Package (zip)**.

With Docker (production override plus the replayer profile):

```bash
docker compose -f docker-compose.yml -f docker/compose.production.yml --profile replayer up -d
docker compose exec mom python -m replayer package --host 127.0.0.1 --out /data/foundry-housing-replayer.zip
docker compose cp mom:/data/foundry-housing-replayer.zip .
```

### Recording your own source

Any source that follows a delivery-package point table can be recorded and compiled into a replay file:

```bash
python -m replayer record --package plant.zip --mqtt mqtt://host:1883 --opcua opc.tcp://host:4840/vfactory/ \
       --modbus host:5020 --hours 24 --out plant.raw.jsonl.gz
python -m replayer compile plant.raw.jsonl.gz --package plant.zip --out replayer/recordings/plant.jsonl.gz
python -m replayer --recording replayer/recordings/plant.jsonl.gz
```

Check a recording for real company, person and site names before you share it.
