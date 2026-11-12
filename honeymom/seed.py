"""初始化：系统账号 + 行业模板（演示 = 通用 + 铸造 + 机加 + 示范工厂数据，见 honeymom/packs/ 和 docs/dev/config.md「行业模板」）。
可重复执行：新库第一次应用行业模板；空白安装（MOM_BLANK=1，旧名 MES_BLANK）只建管理员、登录后走开局向导。"""
from .env import get as env_get
import os
from datetime import datetime, timedelta

from . import auth, db
from .db import clock

USERS = [('admin', '系统管理员', 'admin'), ('manager', '王厂长', 'manager'), ('planner', '计划员李娜', 'planner'), ('leader', '班长张强', 'leader'),
         ('operator', 'CNC 操作工赵磊', 'operator'), ('qc', '质检员孙敏', 'qc'), ('maint', '设备员周涛', 'maint'), ('wh', '仓管员吴芳', 'warehouse'),
         ('engineer', '工艺工程师郑凯', 'engineer')]
DEMO_PW = 'honey123'


def ensure(c, entity, key, row):
    r = db.get(c, entity, **{key: row[key]})
    if r:
        return r['id']
    return db.insert(c, entity, row, raw=True)


def ensure_users(c, only_admin=False):
    """系统账号：演示时 9 个角色各一个（关联同名员工）；空白安装（开局向导）只建 admin。密码 honey123，首次登录后请修改。"""
    link = {'leader': '张强', 'operator': '赵磊', 'qc': '孙敏', 'maint': '周涛', 'wh': '吴芳', 'engineer': '郑凯', 'planner': '李娜'}
    for u, name, roles in USERS[:1] if only_admin else USERS:
        if not db.get(c, 'sys_user', username=u):
            emp = db.get(c, 'employee', name=link[u]) if u in link else None
            db.insert(c, 'sys_user', {'username': u, 'name': name, 'roles': roles, 'active': True, 'password_hash': auth.hash_pw(DEMO_PW),
                                      'employee': emp['id'] if emp else None}, raw=True)


def blank_install(blank=None) -> bool:
    """Production install (the default): no demo data and no default accounts. The first start asks for the administrator account
    (login page set-up step, POST /api/setup/admin, or `python -m honeymom create-admin`), then the set-up wizard runs.
    Demo mode needs the explicit flag MOM_DEMO=1 (demo accounts, password honey123, demo dataset in MOM_DEMO_LANG=en|zh).
    MOM_BLANK=1 (legacy MES_BLANK) still forces a blank install; seed.run(blank=...) overrides both."""
    from . import i18n
    if blank is not None:
        return bool(blank)
    if env_get('MOM_BLANK', '').strip() in ('1', 'true', 'yes'):
        return True
    return not i18n.demo_mode()


def needs_admin(c) -> bool:
    """No active administrator yet (fresh production install)."""
    return not any('admin' in (u.get('roles') or '').split(',') and u.get('active') for u in db.rows(c, 'sys_user'))


def create_admin(c, username, password, name=''):
    """First administrator of a production install. Refuses when an active administrator already exists."""
    from .i18n import _tr
    username = (username or '').strip()
    if not needs_admin(c):
        raise ValueError(_tr('管理员账号已存在'))
    if not username or len(username) > 40:
        raise ValueError(_tr('请填写账号'))
    if len(password or '') < 8:
        raise ValueError(_tr('新密码至少 8 位'))
    u = db.get(c, 'sys_user', username=username)
    row = {'username': username, 'name': name or username, 'roles': 'admin', 'active': True, 'password_hash': auth.hash_pw(password)}
    if u:
        db.update(c, 'sys_user', u['id'], row, raw=True)
        return u['id']
    return db.insert(c, 'sys_user', row, raw=True)


def base(c, blank=None) -> bool:
    """基础数据 = 行业模板（5.0 M3，honeymom/packs/）：通用 + 铸造 + 机加，带示范工厂（某转向器壳体铸造厂）的演示数据——工序、物料、BOM、工艺路线、
    班组员工、检验模板、仓库与期初库存、演示工单、知识库、数据源（和 4.7 的演示数据一致，tests/impl_check.py 逐表核对）。
    只在新库第一次初始化时应用（系统参数 setup_state）；之后启动不再重复写。返回 True = 演示库（demo_seed=1，调用方接着补其它模块的演示数据）；
    空白安装 / 开局向导建的客户库返回 False（不写任何演示数据）。"""
    from .impl import packs
    state = db.setting(c, 'setup_state')
    if state is None:
        if blank_install(blank):
            db.set_setting(c, 'setup_state', 'pending', '开局向导：选行业模板 → 公司信息 → 导入主数据 → 设备接入')
            return False
        packs.apply(packs.DEMO_PACKS, demo=True, conn=c)
        ensure_users(c)
        db.set_setting(c, 'setup_state', 'done', '开局向导（行业模板 → 公司信息 → 导入主数据 → 设备接入）')
        db.set_setting(c, 'demo_seed', '1', '演示库：启动时补演示数据（ERP、现场执行、示范工厂交付包）；开局向导建的客户库没有这个参数')
        return True
    return db.setting(c, 'demo_seed') == '1'


def after_import(c):
    """导入设备后（虚拟工厂导入、交付包、设备导入）：行业模板按设备类型建点检保养计划，演示数据里依赖设备的记录（夹具、NC 程序、停机计划）。"""
    from .impl import packs
    packs.after_equipment(c)
    from . import seed_biz
    seed_biz.after_import(c)
    from . import seed_exec
    seed_exec.after_import(c)       # 备件（关联设备）


def run(blank=None):
    """Seed. In demo mode the dataset language is MOM_DEMO_LANG (default en): the Chinese demo data is the source and every
    value is written through locale/data-en.json (honeymom/i18n.py seed_translation)."""
    from . import i18n
    en = i18n.demo_lang() == 'en' if not blank_install(blank) else i18n.default_lang() == 'en'
    with i18n.seed_translation(en), i18n.use('en' if en else 'zh-CN'):
        _run(blank)
    if i18n.SEED_MISSES:
        import json
        with open(os.path.join(db.DATA, 'seed_i18n_missing.json'), 'w', encoding='utf-8') as f:
            json.dump(sorted(i18n.SEED_MISSES), f, ensure_ascii=False, indent=0)


def _run(blank=None):
    with db.engine.begin() as c:
        if not base(c, blank):          # 空白安装（开局向导）：只建管理员，不写演示数据
            return
        from . import seed_biz      # 排产约束、首检、模具、图纸等业务演示数据
        seed_biz.run(c)
        from . import seed_erp      # 5.0 M4 内置 ERP 演示数据：客户 / 供应商 / 价格 / 销售订单（按单生产连演示工单）/ 采购订单
        seed_erp.run(c)
    onboard_demo()
    # 旧演示库升级（已有设备，onboard_demo 不再导入）：现场执行与质量合规的演示数据只补一次（还没有量检具 = 没补过）
    with db.engine.begin() as c:
        if db.rows(c, 'equipment', limit=1) and not db.rows(c, 'gauge', limit=1):
            from . import seed_exec
            seed_exec.run(c)


# 示范工厂的协议端点（虚拟工厂默认端口）；测试副本用别的端口时，导入交付包时选"用交付包里的端点"或改数据源
DEMO_ENDPOINTS = {'mqtt': 'mqtt://127.0.0.1:1883', 'opcua': 'opc.tcp://127.0.0.1:4840/vfactory/', 'modbus': '127.0.0.1:5020'}


def onboard_demo():
    """5.0（M2）：示范工厂走标准接入路径——导入虚拟工厂的交付包（fixtures/delivery/foundry-housing.zip：点表 + 平面图 DXF + 三维），
    和真实工厂收到集成商的交付包一样（设备台账、产线 / 工位、采集点、平面图、三维模型都来自交付包）。库里已经有设备就不动。"""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'delivery', 'foundry-housing.zip')
    with db.engine.connect() as c:
        if db.rows(c, 'equipment', limit=1) or not os.path.exists(path):
            return
    from .factory import package
    with open(path, 'rb') as f:
        data = f.read()
    with db.engine.begin() as c:
        r = package.preview(c, data, 'foundry-housing-delivery.zip', {'ds_prefix': 'VF', 'endpoints': DEMO_ENDPOINTS, 'use_package_endpoints': True,
                                                                     'note': '示范工厂交付包（初始化）'}, 'system:seed')
        package.confirm(c, r['id'], {}, 'system:seed')
        from . import seed_exec     # 现场执行与质量合规：打印机/规则、包装规则、量检具、PFMEA→控制计划→检验模板、MSA、委外、ECN
        seed_exec.run(c)
