"""PostgreSQL 上建 SQLite 风格的兼容函数（问数 SQL / 用户手写 SQL 用），见 compat.py。SQLite 不需要（函数在连接时注册）。"""
TITLE = 'PostgreSQL 兼容函数（strftime / julianday / ifnull / group_concat …）'


def up(op):
    from ..compat import PG_COMPAT_SQL
    op.execute(PG_COMPAT_SQL, dialect='postgresql', desc='建兼容函数和运算符')
