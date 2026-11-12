"""采集断网续传（store-and-forward）：数据库或服务暂时不可用时，采集到的消息先按顺序写到本地磁盘，恢复后按原顺序补进库。

- 文件：<数据目录>/spool/spool-<序号>.jsonl（每行一条 {"k": 类型, "a": 参数}），cursor.json 记已补传到哪个文件的哪一行。
  每次追加都 flush + fsync（进程崩溃、断电不丢）；单个文件满 20 MB 换下一个；全部补完删除文件。
- 顺序：一旦开始缓存，后来的消息也排在缓存后面（即使库已经恢复），补完了才回到直接入库 —— 所以入库顺序 = 到达顺序。
- 去重：补传时某条可能在断开前已经入库了一半 —— 零件事件 / 生产日志靠 ingest_seen（同一事务里登记消息摘要），
  点位原始值靠 ts_raw 主键 (point, ts)，设备状态 / 报警 / 刀具计数本身是幂等的（同值不重复记）。
用法见 ingest.GATE。
"""
import json
import logging
import os
import threading

from . import db

log = logging.getLogger('mes.spool')
MAX_FILE = 20 * 1024 * 1024


class Spool:
    def __init__(self, folder: str | None = None):
        self.dir = folder or os.path.join(db.DATA, 'spool')
        self._lock = threading.Lock()
        self._fh = None
        self._cur_file = None
        self._pending = None          # 缓存里还没补传的条数（懒计算）
        self.stats = {'spooled': 0, 'replayed': 0, 'files': 0}

    # ---------- 文件 ----------
    def _files(self):
        if not os.path.isdir(self.dir):
            return []
        return sorted(f for f in os.listdir(self.dir) if f.startswith('spool-') and f.endswith('.jsonl'))

    def _cursor(self):
        p = os.path.join(self.dir, 'cursor.json')
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {'file': None, 'line': 0}

    def _save_cursor(self, cur):
        p = os.path.join(self.dir, 'cursor.json')
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cur, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def _open_tail(self):
        files = self._files()
        if files:
            last = os.path.join(self.dir, files[-1])
            if os.path.getsize(last) < MAX_FILE:
                return last
        n = int(files[-1][6:-6]) + 1 if files else 1
        self.stats['files'] += 1
        return os.path.join(self.dir, f'spool-{n:08d}.jsonl')

    # ---------- 写 ----------
    def append(self, kind: str, args: list):
        line = json.dumps({'k': kind, 'a': args}, ensure_ascii=False, default=lambda x: db.jsonable(x) if hasattr(x, 'isoformat') else str(x))
        with self._lock:
            os.makedirs(self.dir, exist_ok=True)
            if self._fh is None or self._fh.tell() >= MAX_FILE:
                if self._fh:
                    self._fh.close()
                self._cur_file = self._open_tail()
                self._fh = open(self._cur_file, 'a', encoding='utf-8')
            self._fh.write(line + '\n')
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._pending = (self.pending() if self._pending is None else self._pending) + 1
            self.stats['spooled'] += 1

    def close(self):
        with self._lock:
            if self._fh:
                self._fh.close()
            self._fh, self._cur_file = None, None

    # ---------- 读 / 补传 ----------
    def pending(self) -> int:
        if self._pending is not None:
            return self._pending
        files = self._files()
        if not files:
            self._pending = 0
            return 0
        cur = self._cursor()
        n = 0
        for f in files:
            if cur['file'] and f < cur['file']:
                continue
            with open(os.path.join(self.dir, f), encoding='utf-8') as fh:
                k = sum(1 for _ in fh)
            n += k - (cur['line'] if f == cur['file'] else 0)
        self._pending = max(0, n)
        return self._pending

    def drain(self, handler, limit: int = 2000) -> int:
        """按顺序取最多 limit 条交给 handler(kind, args)。handler 抛异常就停在这一条（下次从它开始）。返回本次处理的条数。"""
        done = 0
        with self._lock:
            files = self._files()
            cur = self._cursor()
            for f in files:
                if cur['file'] and f < cur['file']:
                    continue
                start = cur['line'] if f == cur['file'] else 0
                path = os.path.join(self.dir, f)
                with open(path, encoding='utf-8') as fh:
                    lines = fh.readlines()
                i = start
                try:
                    while i < len(lines) and done < limit:
                        raw = lines[i].strip()
                        if raw:
                            try:
                                m = json.loads(raw)
                            except ValueError:            # 断电时写了半行：跳过
                                m = None
                            if m:
                                handler(m['k'], m['a'])
                        i += 1
                        done += 1
                        self.stats['replayed'] += 1
                finally:
                    self._save_cursor({'file': f, 'line': i})
                    self._pending = None
                if done >= limit:
                    return done
                if i >= len(lines) and path != self._cur_file:
                    os.remove(path)                       # 这个文件补完了
                elif i >= len(lines) and path == self._cur_file:
                    # 正在写的文件也补完了：关掉，删除，从头开始
                    self._fh.close()
                    self._fh, self._cur_file = None, None
                    os.remove(path)
            if not self._files():
                try:
                    os.remove(os.path.join(self.dir, 'cursor.json'))
                except OSError:
                    pass
                self._pending = 0
        return done


SPOOL: Spool | None = None


def get() -> Spool:
    global SPOOL
    if SPOOL is None:
        SPOOL = Spool()
    return SPOOL
