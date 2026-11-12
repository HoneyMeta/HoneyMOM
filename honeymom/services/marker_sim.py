"""模拟激光打码机（测试 / 演示用）：TCP 文本协议，一条命令一行。

  MARK <任务号> <内容>      → 打标（模拟 0.2 s），回 "OK <任务号>"；内容含 FAIL 回 "ERR <任务号> 打标失败"
  STATUS                    → "OK READY"
  其它                      → "ERR UNKNOWN"

服务内：打码机 mode=sim 时 exec_label.send_mark 调 ensure() 在本机随机端口起一个（线程），最近 500 条打标记录在 LOG。
单独运行（给别的系统联调）：python -m honeymom.services.marker_sim --port 9100
"""
from ..i18n import _tr
import collections
import socketserver
import threading
import time

LOG = collections.deque(maxlen=500)
PORT: dict = {}
_lock = threading.Lock()


class _H(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            line = raw.decode('utf-8', errors='replace').strip()
            if not line:
                continue
            parts = line.split(' ', 2)
            cmd = parts[0].upper()
            if cmd == 'MARK' and len(parts) >= 2:
                job, data = parts[1], parts[2] if len(parts) > 2 else ''
                time.sleep(0.2)
                ok = 'FAIL' not in data
                LOG.append({'t': time.strftime('%Y-%m-%d %H:%M:%S'), 'job': job, 'data': data, 'ok': ok})
                self.wfile.write((f'OK {job}\r\n' if ok else _tr('ERR {job} 打标失败\r\n', job=job)).encode('utf-8'))
            elif cmd == 'STATUS':
                self.wfile.write(b'OK READY\r\n')
            else:
                self.wfile.write(b'ERR UNKNOWN\r\n')
            self.wfile.flush()


class _S(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port=0, host='127.0.0.1'):
    s = _S((host, port), _H)
    th = threading.Thread(target=s.serve_forever, name='marker-sim', daemon=True)
    th.start()
    return s


def ensure() -> int:
    """本进程的模拟打码机端口（没起就起一个）。"""
    with _lock:
        if not PORT.get('port'):
            s = serve(0)
            PORT['port'] = s.server_address[1]
            PORT['server'] = s
        return PORT['port']


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=9000)
    ap.add_argument('--host', default='127.0.0.1')
    a = ap.parse_args()
    srv = serve(a.port, a.host)
    print(f'模拟打码机 {a.host}:{srv.server_address[1]}（Ctrl+C 退出）')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()
