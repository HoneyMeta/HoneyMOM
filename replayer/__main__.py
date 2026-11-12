# HoneyMOM plant data replayer - SPDX-License-Identifier: AGPL-3.0-or-later
"""python -m replayer [run] [options]      replay a recording (default)
python -m replayer package --out x.zip   write the delivery package with endpoints pointing at the replayer
python -m replayer info                  show what a recording contains
python -m replayer record ...            capture a live source into a raw capture file (maintainers)
python -m replayer compile ...           raw capture -> compact recording + manifest (maintainers)
"""
import argparse
import asyncio
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REC = os.path.join(HERE, 'recordings', 'foundry-housing.jsonl.gz')


def default_package(plant='foundry-housing'):
    for p in (os.path.join(HERE, 'recordings', f'{plant}.package.zip'),
              os.path.join(os.path.dirname(HERE), 'honeymom', 'fixtures', 'delivery', f'{plant}.zip')):
        if os.path.exists(p):
            return p
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmds = ('run', 'record', 'compile', 'package', 'info')
    cmd = argv[0] if argv and argv[0] in cmds else 'run'
    if argv and argv[0] in cmds:
        argv = argv[1:]
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog=f'python -m replayer {cmd}')
    ap.add_argument('-v', '--verbose', action='store_true')
    if cmd == 'run':
        ap.add_argument('--recording', default=DEFAULT_REC, help='recording (.jsonl.gz)')
        ap.add_argument('--speed', type=float, default=1.0, help='playback speed factor (1 = real time; 10 = one recorded day in 2.4 h)')
        ap.add_argument('--loop', dest='loop', action='store_true', default=True, help='start over at the end (default)')
        ap.add_argument('--once', dest='loop', action='store_false', help='stop at the end of the recording')
        ap.add_argument('--start', type=float, default=0.0, help='start this many hours into the recording')
        ap.add_argument('--host', default='0.0.0.0', help='address the servers listen on')
        ap.add_argument('--mqtt-port', type=int, default=1883)
        ap.add_argument('--mqtt-broker', default='', help='publish to this external broker (mqtt://host:port) instead of the embedded one')
        ap.add_argument('--opcua-port', type=int, default=4840)
        ap.add_argument('--opcua-path', default='/vfactory/', help='OPC UA endpoint path')
        ap.add_argument('--modbus-port', type=int, default=5020, help='Modbus TCP port (0 = off)')
        ap.add_argument('--no-mqtt', action='store_true')
        ap.add_argument('--no-opcua', action='store_true')
        ap.add_argument('--timestamps', choices=('sim', 'wall'), default='sim',
                        help='sim: ts advance at --speed x real time like an accelerated simulator (cycle times stay true); '
                             'wall: ts = the wall clock at publish (durations shrink by --speed)')
    elif cmd == 'record':
        ap.add_argument('--package', default=default_package(), help='delivery package of the source (point table)')
        ap.add_argument('--plant', default=None)
        ap.add_argument('--mqtt', default='mqtt://127.0.0.1:1883')
        ap.add_argument('--opcua', default='opc.tcp://127.0.0.1:4840/vfactory/')
        ap.add_argument('--modbus', default='127.0.0.1:5020', help="host:port, '' = off")
        ap.add_argument('--modbus-period', type=float, default=0.5)
        ap.add_argument('--hours', type=float, default=24, help='stop after this much source (simulated) time')
        ap.add_argument('--wall-limit', type=float, default=0, help='stop after this many wall-clock seconds (0 = no limit)')
        ap.add_argument('--set-speed', type=float, default=0, help='write <plant>/Plant/Speed on the source first (simulators only)')
        ap.add_argument('--out', required=True, help='raw capture file (.raw.jsonl.gz)')
    elif cmd == 'compile':
        ap.add_argument('raw', help='raw capture from "record"')
        ap.add_argument('--package', default=default_package())
        ap.add_argument('--out', default=DEFAULT_REC)
        ap.add_argument('--hours', type=float, default=24, help='keep this much recorded time')
        ap.add_argument('--skip', type=float, default=0, help='drop the first N minutes of the capture (settling)')
        ap.add_argument('--source', default='simulated')
    elif cmd == 'package':
        ap.add_argument('--package', default=default_package())
        ap.add_argument('--out', required=True)
        ap.add_argument('--host', default='127.0.0.1', help='host name the MES uses to reach the replayer')
        ap.add_argument('--mqtt-port', type=int, default=1883)
        ap.add_argument('--opcua-port', type=int, default=4840)
        ap.add_argument('--opcua-path', default='/vfactory/')
        ap.add_argument('--modbus-port', type=int, default=5020, help='0 = replayer runs without Modbus')
    elif cmd == 'info':
        ap.add_argument('--recording', default=DEFAULT_REC)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    for noisy in ('asyncua', 'amqtt', 'transitions', 'pymodbus'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if cmd == 'run':
        from .play import run
        try:
            asyncio.run(run(a))
        except KeyboardInterrupt:
            pass
    elif cmd == 'record':
        from .record import capture
        asyncio.run(capture(a))
    elif cmd == 'compile':
        from .compile import compile_raw
        compile_raw(a)
    elif cmd == 'package':
        from .pointtable import rewrite_endpoints
        if not a.package:
            sys.exit('no delivery package found; pass --package')
        data = rewrite_endpoints(open(a.package, 'rb').read(), a.host, a.mqtt_port, a.opcua_port, a.modbus_port, a.opcua_path)
        with open(a.out, 'wb') as f:
            f.write(data)
        print(f'wrote {a.out} ({len(data)} bytes): MQTT {a.host}:{a.mqtt_port}, OPC UA opc.tcp://{a.host}:{a.opcua_port}{a.opcua_path}'
              + (f', Modbus {a.host}:{a.modbus_port}' if a.modbus_port else ''))
    elif cmd == 'info':
        from .fmt import Recording
        r = Recording(a.recording)
        import json
        print(json.dumps(r.summary(), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
