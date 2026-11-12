"""单号生成回归测试（不需要启动服务）：python tests/code_check.py

回归：线上日志里反复出现 UNIQUE constraint failed: ncr.code / stock_txn.code。原因是流水号超过位数后
（NCR 一天第 1000 条 → NCR2611041000，出入库第 10000 条 → CK26110410000）按字符串取"最大"会取回 999 / 9999，
之后每次都生成同一个号，插入失败、整批采集数据回滚。
检查：
  1. 一天里大量插入（NCR 1200 条、出入库 10050 条）：号码不重复、流水按数值递增、过了 999 / 9999 继续往上走；
  2. 模拟重启：换一个新的数据库连接（进程重启），数据时钟回到同一天更早的时间（虚拟工厂重启/重置），继续插入不撞号；
  3. 老库里已经有"撞号前"的状态（001…999 + 1000）：升级后从 1001 接着编；
  4. 手工录入占了下一个号：自动顺延；别的前缀、带尾巴的号不干扰。
"""
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix='hm_code_')
os.environ['HM_DATA'] = TMP
from honeymom import db  # noqa: E402

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, detail)


URL = 'sqlite:///' + os.path.join(TMP, 'codes.db').replace('\\', '/')
db.init(URL)
DAY = datetime(2026, 11, 4, 8, 0, 0)
db.clock.data_now = DAY


def add(entity, n, step_s=1, **row):
    codes = []
    with db.engine.begin() as c:
        for i in range(n):
            db.clock.data_now = db.clock.data_now + timedelta(seconds=step_s)
            rid = db.insert(c, entity, dict(row), raw=True)
            codes.append(db.get(c, entity, rid)['code'])
    return codes


def seqs(codes, prefix):
    return [int(re.match(re.escape(prefix) + r'(\d+)$', x).group(1)) for x in codes]


# 1. 一天里大量插入
nc = add('ncr', 1200, step_s=10, qty=1, status='open')
s = seqs(nc, 'NCR261104')
ok('NCR 一天 1200 条：不重复', len(set(nc)) == len(nc), f'{nc[0]} … {nc[998]}, {nc[999]} … {nc[-1]}')
ok('NCR 流水按数值递增、过 999 继续', s == list(range(1, 1201)) and nc[999] == 'NCR2611041000', nc[999])
ck = add('stock_txn', 10050, step_s=1, ttype='in', qty=1)
s = seqs(ck, 'CK261104')
ok('出入库一天 10050 条：不重复、过 9999 继续', len(set(ck)) == len(ck) and s == list(range(1, 10051)) and ck[9999] == 'CK26110410000', f'{ck[9998]}, {ck[9999]}, {ck[-1]}')

# 2. 模拟重启：新连接 + 数据时钟回到当天更早的时间
db.engine.dispose()
db.clock = db.Clock()            # 进程重启后时钟是空的
db._code_issued.clear()          # 进程重启后"已发出的号"也是空的
db.init(URL)
db.clock.data_now = DAY - timedelta(hours=1)     # 虚拟工厂重启，仿真时间回到这天早上
nc2 = add('ncr', 30, qty=1, status='open')
ck2 = add('stock_txn', 30, ttype='in', qty=1)
with db.engine.connect() as c:
    n_ncr = c.execute(db.sa.select(db.sa.func.count(db.sa.distinct(db.TABLES['ncr'].c.code)))).scalar()
    n_all = c.execute(db.sa.select(db.sa.func.count()).select_from(db.TABLES['ncr'])).scalar()
ok('重启 + 时钟回退：接着编号不撞', nc2[0] == 'NCR2611041201' and ck2[0] == 'CK26110410051' and n_ncr == n_all == 1230, f'{nc2[0]} / {ck2[0]}')

# 3. 老库撞号前的状态（另一天：001…999 + 1000，像线上那样）→ 1001
with db.engine.begin() as c:
    for i in range(1, 1001):
        c.execute(db.TABLES['ncr'].insert().values(code=f'NCR261103{i:03}', qty=1, status='closed'))
db.clock.data_now = datetime(2026, 11, 3, 23, 0, 0)
old = add('ncr', 3, qty=1, status='open')
ok('老库已有 999 和 1000：从 1001 接着编', old == ['NCR2611031001', 'NCR2611031002', 'NCR2611031003'], ', '.join(old))

# 4. 手工号占位 / 别的前缀 / 带尾巴的号
with db.engine.begin() as c:
    c.execute(db.TABLES['ncr'].insert().values(code='NCR2611031005', qty=1, status='open'))       # 手工录了一个后面的号
    c.execute(db.TABLES['ncr'].insert().values(code='NCR261103999-R1', qty=1, status='open'))     # 带尾巴的号不算流水
db.clock.data_now = datetime(2026, 11, 3, 23, 30, 0)
nxt = add('ncr', 1, qty=1, status='open')
ok('取数值最大（手工号 1005 之后）、带尾巴的号不干扰', nxt == ['NCR2611031006'], nxt[0])
db.clock.data_now = datetime(2026, 11, 5, 0, 1, 0)
ok('新的一天从 001 开始', add('ncr', 1, qty=1, status='open') == ['NCR261105001'])

# 5. 并发：8 个线程各自开事务插 40 条（接口线程 + 采集同时建 NCR），事务提交前别的线程也不能拿到同一个号
import threading  # noqa: E402
db.clock.data_now = datetime(2026, 11, 6, 9, 0, 0)
errs, got = [], []


def worker():
    for _ in range(40):
        try:
            with db.engine.begin() as c:
                rid = db.insert(c, 'ncr', {'qty': 1, 'status': 'open'}, raw=True)
                got.append(db.get(c, 'ncr', rid)['code'])
        except Exception as e:          # noqa: BLE001
            errs.append(repr(e)[:120])


ths = [threading.Thread(target=worker) for _ in range(8)]
[t.start() for t in ths]
[t.join() for t in ths]
ok('并发 8 线程 × 40 条：没有撞号、没有丢', not errs and len(got) == 320 and len(set(got)) == 320, (errs[:1] or [f'{min(got)} … {max(got)}'])[0])

db.engine.dispose()
shutil.rmtree(TMP, ignore_errors=True)
bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} 通过", ('；失败：' + '、'.join(bad)) if bad else '')
sys.exit(1 if bad else 0)
