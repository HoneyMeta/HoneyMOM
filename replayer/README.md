# Plant data replayer

Run HoneyMOM against realistic live shop-floor data without a simulator or a real plant.

The replayer plays back a recorded day of a demo foundry (`foundry-housing`: melting, molding, shot blasting,
six machining centres with action-level signals, deburring robot, inspection, AS/RS, AGVs) and serves it on the same
interfaces a plant would:

| Interface | What you get |
|---|---|
| MQTT (embedded broker) | `vfactory/<plant>/<area>/<device>/<point>` point topics (JSON, retained, report-by-exception), gateway topics `vfactory/<plant>/GW1/...`, the MES gateway topics `vfactory/<plant>/_mes/part` (part events) and `vfactory/<plant>/_mes/event` (production log), `vfactory/<plant>/_status` heartbeat, AGV states `vda5050/v2/<plant>/<AGV>/state` |
| OPC UA | endpoint `opc.tcp://<host>:4840/vfactory/`, namespace `http://honeymeta.cn/vfactory` (ns=2), the point-table nodes `ns=2;s=<plant>/<device>/<point>` with the same folders, object types, data types, EngineeringUnits and EURange, plus `<plant>/Plant/*` (SimTime, Speed, counters) |
| Modbus TCP | one unit per device, holding registers / coils exactly as recorded (input registers / discrete inputs mirror them) |

Topics, node ids, payloads and register layout are those of the delivery package point table
(`honeymom/fixtures/delivery/foundry-housing.zip`), so the MES onboarding (`接入中心`) works unchanged.

All timestamps are moved to "now": message `ts` / `sim_ts`, part-event times, OPC UA SourceTimestamps, and the production
date inside serial numbers (`SH<yymmdd>nnnnn`) and heat numbers (`L<yymmdd>-nnn`). The recording loops; every loop gets
new dates, so serial numbers never repeat.

## Quick start

```bash
pip install -r replayer/requirements.txt
python -m replayer                        # MQTT :1883, OPC UA :4840/vfactory/, Modbus :5020, real time, loops forever
```

Then start the MES in demo mode (`MOM_DEMO=1`). Its demo datasources (`VF` MQTT `mqtt://127.0.0.1:1883`, `VF-OPC`
`opc.tcp://127.0.0.1:4840/vfactory/`, `VF-MB` `127.0.0.1:5020`) already point at these defaults; the cockpit, map, OEE,
reports and cycle analysis fill within a minute.

For a fresh (non-demo) installation, onboard the plant like a customer would: write the delivery package with the
replayer's address and import it in 接入中心 → 导入交付包 (or `POST /api/onboard/package`):

```bash
python -m replayer package --host 127.0.0.1 --out foundry-housing-replayer.zip
```

The package (point table `points.xlsx`, layout `layout.dxf`, 3D models `models/*.glb`, `README.md`) is the one the MES
ships in `honeymom/fixtures/delivery/`; only the endpoints in its 协议 sheet and README are rewritten.

## Options

```
python -m replayer [run] [--speed 1] [--once] [--start HOURS] [--host 0.0.0.0]
                   [--mqtt-port 1883] [--mqtt-broker mqtt://host:port] [--no-mqtt]
                   [--opcua-port 4840] [--opcua-path /vfactory/] [--no-opcua]
                   [--modbus-port 5020 | --modbus-port 0]
                   [--timestamps sim|wall] [--recording replayer/recordings/foundry-housing.jsonl.gz]
```

- `--speed 10` plays a recorded day in 2.4 hours. With `--timestamps sim` (default) the timestamps advance at the same
  rate, like an accelerated simulator: cycle times, durations and OEE stay true, the MES data clock runs ahead of the wall
  clock. `--timestamps wall` stamps everything with the wall clock instead (durations shrink by the speed factor).
- `--start 6` begins 6 hours into the recording (the state at that point is published first).
- `--mqtt-broker` publishes into an existing broker (Mosquitto, EMQX, ...) instead of the embedded one.
- `--once` stops at the end of the recording instead of looping.
- MQTT writes (`<point topic>/set`) are acknowledged on `/set/ack` and echoed on the point topic; OPC UA writes to
  writable nodes are accepted. Both are overwritten by the next recorded value. OPC UA HistoryRead and alarm events are
  not replayed.

`python -m replayer info` prints what the recording contains.

## The recording

`recordings/foundry-housing.jsonl.gz` (+ `.manifest.json`): 24 hours of the de-identified demo foundry, recorded from
the plant simulator (`source: simulated`; all names are fictitious). Format `honeymom-replay/1`, documented in
`fmt.py`: a JSON header (topics, units, OPC UA address-space metadata, Modbus blocks) followed by one compact event per
line with millisecond deltas; only value changes are stored.

Recording your own source (any plant or simulator that follows a delivery-package point table):

```bash
python -m replayer record --package plant.zip --mqtt mqtt://host:1883 --opcua opc.tcp://host:4840/vfactory/ \
       --modbus host:5020 --hours 24 --out plant.raw.jsonl.gz
python -m replayer compile plant.raw.jsonl.gz --package plant.zip --out replayer/recordings/plant.jsonl.gz
```

The source must expose `<plant>/Plant/SimTime` over OPC UA (used as the clock when the source runs faster than real
time; `--set-speed` writes `<plant>/Plant/Speed` first). Check a new recording for real names before publishing it.

## Check

`tests/replayer_check.py` verifies the MES end to end against a running replayer (collector connected, values
advancing, parts and events arriving, map / cockpit / OEE / reports / cycle analysis populated):

```bash
MES_URL=http://127.0.0.1:8900 REPLAYER_MQTT=mqtt://127.0.0.1:1883 REPLAYER_OPCUA=opc.tcp://127.0.0.1:4840/vfactory/ \
REPLAYER_MODBUS=127.0.0.1:5020 python tests/replayer_check.py 90
```

## License

AGPL-3.0-or-later, like the rest of HoneyMOM.
