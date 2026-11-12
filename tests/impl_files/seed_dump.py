"""演示数据等价性：把 seed（新库）+ 导入设备后的演示数据转成"与 id 无关"的规范形式，和 4.7 定版代码的结果逐表逐行比。

    python tests/impl_files/seed_dump.py <代码目录> <输出.json>       # 用某个版本的代码生成（fixture 用 60eaf07 生成）
    from seed_dump import dump                                     # impl_check 里用当前代码生成再比

规范形式：每个实体 → 按 id 顺序的行；引用字段换成对方的编码 / 名称（自然键）；去掉 id、created_at、updated_at、密码；时间转字符串；
数据时钟固定在 2026-11-04 09:30（演示数据里的计划时间、到期日都相对它）。导入设备用一份固定的设备清单（和虚拟工厂示范工厂一样的编号和类型）。
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime

CLOCK = datetime(2026, 11, 4, 9, 30, 0)
# 示范工厂的设备（编号, 名称, 类型）：和虚拟工厂 foundry-housing 导入后的一致
EQUIP = [('FUR1', '1# 中频炉', 'furnace'), ('FUR2', '2# 中频炉', 'furnace'), ('TRT1', '球化站', 'treatment'), ('SAND1', '砂处理', 'sand'),
         ('MOLD1', '造型线', 'molding'), ('COOL1', '冷却线', 'cooling'), ('SHOT1', '抛丸机', 'shotblast'), ('RCV1', '收货台', 'receiving'),
         ('CNC1', '1# 加工中心', 'cnc'), ('CNC2', '2# 加工中心', 'cnc'), ('CNC3', '3# 加工中心', 'cnc'), ('CNC4', '4# 加工中心', 'cnc'),
         ('DEB1', '去毛刺单元', 'deburr'), ('INS1', '内窥检测站', 'inspection'), ('ASRS1', '立体仓库', 'warehouse'), ('AGV1', 'AGV 车队', 'agv'),
         ('CREW1', '甲班', 'crew')]
SKIP_COLS = {'id', 'created_at', 'updated_at', 'password_hash', 'ext'}
NATURAL = ('code', 'username', 'sn', 'title', 'name', 'key')


def _nat(r):
    for k in NATURAL:
        if r.get(k):
            return str(r[k])
    return None


def _v(v):
    if v == '':
        return None              # 空字符串和空值一样（4.7 的 seed 有的字段写 ''，导入框架统一存空）
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 6)
    return v


def dump(db, schema, entities=None) -> dict:
    """{实体: [行…]}；entities 只比这些实体（缺省 = 4.7 定版里的全部实体）。"""
    import sqlalchemy as sa
    ents = [e for e in schema.ENTITIES if entities is None or e.name in entities]
    out = {}
    with db.engine.connect() as c:
        nat: dict = {}
        for e in schema.ENTITIES:
            t = schema.TABLES[e.name]
            m = {}
            for i, r in enumerate(c.execute(sa.select(t).order_by(t.c.id)).mappings()):
                m[r['id']] = _nat(dict(r)) or f'#{i + 1}'
            nat[e.name] = m
        for e in ents:
            t = schema.TABLES[e.name]
            rows = []
            for r in c.execute(sa.select(t).order_by(t.c.id)).mappings():
                d = {}
                for f in e.fields:
                    if f.name in SKIP_COLS:
                        continue
                    v = r.get(f.name)
                    if f.t == 'ref' and v is not None:
                        v = nat.get(f.ref, {}).get(v, f'?{v}')
                    d[f.name] = _v(v)
                rows.append(d)
            out[e.name] = rows
    return out


def run(db, seed, schema, after_equipment=True) -> dict:
    """新库：seed → 快照 1；插入固定设备清单 → seed.after_import → 快照 2。"""
    db.clock.data_now = CLOCK
    seed.run()
    snap1 = dump(db, schema)
    if after_equipment:
        with db.engine.begin() as c:
            ln = db.get(c, 'line', code='L-TEST')
            for code, name, et in EQUIP:
                if not db.get(c, 'equipment', code=code):
                    db.insert(c, 'equipment', {'code': code, 'name': name, 'etype': et, 'status': 'offline', 'install_date': CLOCK.date(),
                                               'line': ln['id'] if ln else None}, raw=True)
            seed.after_import(c)
    snap2 = dump(db, schema)
    return {'seed': snap1, 'after_equipment': snap2}


def settings_of(snap) -> dict:
    return {r['key']: r['value'] for r in snap.get('setting', [])}


if __name__ == '__main__':
    code_dir, out_path = sys.argv[1], sys.argv[2]
    tmp = tempfile.mkdtemp(prefix='hm_seed_')
    os.environ['HM_DATA'] = tmp
    sys.path.insert(0, code_dir)
    sys.path = [p for p in sys.path if 'works' not in p]
    from honeymom import db as _db, schema as _schema, seed as _seed  # noqa: E402
    _db.init('sqlite:///' + os.path.join(tmp, 'mes.db').replace('\\', '/'))
    res = run(_db, _seed, _schema)
    res['entities'] = [e.name for e in _schema.ENTITIES]
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=0)
    print('entities', len(res['entities']), 'rows', sum(len(v) for v in res['after_equipment'].values()))
    _db.engine.dispose()
    shutil.rmtree(tmp, ignore_errors=True)
