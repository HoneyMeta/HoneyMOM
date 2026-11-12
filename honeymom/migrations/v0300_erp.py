"""5.0 内置 ERP（M4）：销售 / 采购 / MRP / 成本 / 应收应付 / 凭证的实体表，外部 ERP 集成用的表；生产工单加"销售订单"；
客户 / 供应商（M3 建的轻量往来单位）补商务字段；内置角色补 ERP 权限、新增销售员 / 采购员 / 财务角色；默认科目映射、费率、ERP 模式。"""
from datetime import datetime

import sqlalchemy as sa

TITLE = '内置 ERP：销售 / 采购 / MRP / 成本 / 应收应付 / 凭证 / 外部 ERP 集成'

# 默认科目（《企业会计准则》一级科目 + 常用二级；客户在金蝶 / 用友里的科目编码不同的，在"财务 › 凭证 › 科目映射"里改）
ACCOUNTS = [
    ('ar', None, '1122', '应收账款', 'customer'), ('revenue', None, '6001', '主营业务收入', 'none'), ('tax_out', None, '22210102', '应交税费-应交增值税-销项税额', 'none'),
    ('cogs', None, '6401', '主营业务成本', 'none'), ('inventory', 'fg', '1405', '库存商品', 'material'), ('inventory', 'semi', '1405', '库存商品', 'material'),
    ('inventory', 'raw', '1403', '原材料', 'material'), ('inventory', 'aux', '1403', '原材料', 'material'), ('inventory', 'consumable', '1411', '周转材料', 'material'),
    ('tax_in', None, '22210101', '应交税费-应交增值税-进项税额', 'none'), ('ap', None, '2202', '应付账款', 'supplier'), ('bank', None, '1002', '银行存款', 'none'),
    ('bill_recv', None, '1121', '应收票据', 'customer'), ('wip_mat', None, '500101', '生产成本-直接材料', 'none'), ('wip_lab', None, '500102', '生产成本-直接人工', 'none'),
    ('wip_ovh', None, '500103', '生产成本-制造费用', 'none'), ('payroll', None, '2211', '应付职工薪酬', 'none'), ('mfg_ovh', None, '5101', '制造费用', 'none'),
    ('inv_loss', None, '1901', '待处理财产损溢', 'none'), ('sales_return', None, '6001', '主营业务收入', 'none'),
]
RATES = [('LAB-STD', '人工（标准）', 'labour', 'hour', 45), ('MCH-STD', '机器（标准）', 'machine', 'hour', 60), ('ENG-KWH', '电价', 'energy', 'kwh', 0.75),
         ('OVH-MH', '制造费用（按机器工时）', 'overhead', 'machine_hour', 25)]
SETTINGS = [('erp_mode', 'builtin', 'ERP 模式：builtin 内置 / external 外部 ERP（财务 › ERP 集成）'),
            ('erp_cost_method', 'moving_avg', '材料计价：moving_avg 移动平均 / standard 标准成本')]


def up(op):
    from ..schema_erp import ENTITIES_ERP, T_
    if not op.has_table('customer'):        # M3 的迁移没先跑（编号顺序变了）时自己建
        op.create_table('customer')
    if not op.has_table('supplier'):
        op.create_table('supplier')
    for e in ENTITIES_ERP:
        op.create_table(e.name)
    for t in T_:
        op.create_table(t)
    for c in ('sales_order', 'so_line'):
        op.add_column('work_order', c)
    for c in ('invoice_title', 'bank', 'bank_account', 'email', 'credit_limit', 'pay_type', 'terms_days', 'tax_rate', 'currency', 'salesman'):
        op.add_column('customer', c)
    for c in ('bank', 'bank_account', 'email', 'pay_type', 'terms_days', 'tax_rate', 'qual', 'rating'):
        op.add_column('supplier', c)
    op.create_index('ix_so_line_order', 'so_line', ['sales_order', 'seq'])
    op.create_index('ix_po_line_order', 'po_line', ['purchase_order', 'seq'])
    op.create_index('ix_mrp_sugg_run', 'mrp_suggestion', ['run', 'status'])
    op.run(_roles, '内置角色补 ERP 权限；新增销售员 / 采购员 / 财务')
    op.run(_defaults, '默认科目映射、费率、ERP 模式（已有的不动）')


def _roles(conn):
    from ..schema import role_t
    from ..schema_erp import ERP_ROLES, ROLE_ADD
    now = datetime.now().replace(microsecond=0)
    rows = {r['code']: dict(r) for r in conn.execute(sa.select(role_t)).mappings()}
    for code, add in ROLE_ADD.items():
        r = rows.get(code)
        if r and r.get('builtin'):
            cur = list(r['perms'] or [])
            new = cur + [p for p in add if p not in cur]
            if new != cur:
                conn.execute(role_t.update().where(role_t.c.code == code).values(perms=new, updated_at=now))
    for code, (name, pats) in ERP_ROLES.items():
        if code not in rows:
            conn.execute(role_t.insert().values(code=code, name=name, perms=pats, scope=None, builtin=True, remark='内置 ERP（5.0）', created_at=now, updated_at=now))


def _defaults(conn):
    from ..schema import TABLES
    now = datetime.now().replace(microsecond=0)
    am = TABLES['acct_map']
    if conn.execute(sa.select(am.c.id).limit(1)).first() is None:
        for ev, mt, code, name, aux in ACCOUNTS:
            conn.execute(am.insert().values(event=ev, mtype=mt, account_code=code, account_name=name, aux=aux, created_at=now, updated_at=now))
    cr = TABLES['cost_rate']
    if conn.execute(sa.select(cr.c.id).limit(1)).first() is None:
        for code, name, kind, basis, rate in RATES:
            conn.execute(cr.insert().values(code=code, name=name, kind=kind, basis=basis, rate=rate, created_at=now, updated_at=now))
    st = TABLES['setting']
    for k, v, remark in SETTINGS:
        if conn.execute(sa.select(st.c.id).where(st.c.key == k)).first() is None:
            conn.execute(st.insert().values(key=k, value=v, remark=remark, created_at=now, updated_at=now))
