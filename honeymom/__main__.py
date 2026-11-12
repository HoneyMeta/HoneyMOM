"""命令行：
    python -m honeymom create-admin [--username admin] [--name 名称]   production install: create the first administrator (asks for the password,
                                                    or env MOM_ADMIN_PASSWORD)
    python -m honeymom serve [--http 8900]          启动（首次自动建库、执行迁移、写入演示数据）
    python -m honeymom seed                         补全基础数据（可重复执行）
    python -m honeymom db status                    迁移状态（已执行 / 待执行）
    python -m honeymom db upgrade [--dry-run]       执行待执行的迁移（--dry-run 只列出要做的动作）
    python -m honeymom db check                     漂移检查（schema.py 和库是否一致）
    python -m honeymom backup [--note 说明]         立即备份（数据库 + 上传文件），打印备份文件
    python -m honeymom restore <备份.zip> --to <新数据目录> [--to-db <新库 URL>]   恢复到新目录（不覆盖现有数据）并核对
    python -m honeymom audit verify                 核对审计哈希链
数据库：--db 或环境变量 MOM_DB（旧名 MES_DB；默认 sqlite:///<数据目录>/mes.db；PostgreSQL 如 postgresql+psycopg://user:pw@host:5432/mes）。
"""
from .env import get as env_get
from .i18n import _tr
import argparse
import json
import logging
import sys

from . import db


def main(argv=None):
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(prog='honeymom')
    ap.add_argument('cmd', choices=['serve', 'seed', 'db', 'backup', 'restore', 'audit', 'mcp', 'create-admin'])
    ap.add_argument('--key', default=None, help=_tr('mcp：API 密钥（缺省读环境变量 HONEYMOM_API_KEY）'))
    ap.add_argument('--user', default=None, help=_tr('mcp：不用密钥、以本机账号身份运行（仅限服务器本机管理员）'))
    ap.add_argument('sub', nargs='?', default=None)
    ap.add_argument('--http', type=int, default=8900)
    ap.add_argument('--host', default=None, help=_tr('监听地址，默认 0.0.0.0'))
    ap.add_argument('--db', default=None, help=_tr('数据库地址，默认 sqlite:///data/mes.db；PostgreSQL 如 postgresql+psycopg://user:pw@host/mes'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--note', default='')
    ap.add_argument('--username', default='admin')
    ap.add_argument('--name', default='')
    ap.add_argument('--to', default=None, help=_tr('restore：新数据目录'))
    ap.add_argument('--to-db', default=None, help=_tr('restore（PostgreSQL 备份）：新库 URL'))
    a = ap.parse_args(argv)
    if a.cmd == 'mcp':          # stdio MCP：stdout 只给 JSON-RPC，日志全部走 stderr
        import os
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format='%(asctime)s %(name)s %(levelname)s %(message)s')
        db.init(a.db)
        from .services import ai50  # noqa: F401  （登记权限点、工具）
        from .services.mcp_server import serve_stdio
        serve_stdio(a.key or env_get('HONEYMOM_API_KEY'), a.user)
        return
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    for noisy in ('amqtt', 'asyncua', 'pymodbus', 'transitions'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if a.cmd == 'db':
        from . import migrations
        db.init(a.db, migrate_db=False)
        if a.sub == 'upgrade':
            rep = migrations.upgrade(db.engine, dry_run=a.dry_run)
            for r in rep:
                print(_tr('{rev} {title}：{v}', rev=r['rev'], title=r['title'], v={'applied': _tr('已执行'), 'done': _tr('本次执行'), 'planned': _tr('待执行（演练）')}[r['status']]))
                for x in r['actions']:
                    print('    ', x)
            if not a.dry_run:
                p = migrations.check(db.engine)
                print(_tr('漂移检查：') + (_tr('一致') if not p else '\n  ' + '\n  '.join(p)))
        elif a.sub == 'check':
            p = migrations.check(db.engine)
            print(_tr('一致') if not p else '\n'.join(p))
            sys.exit(1 if p else 0)
        else:
            print(json.dumps(migrations.status(db.engine), ensure_ascii=False, indent=1, default=str))
        return
    if a.cmd == 'restore':
        from . import backup
        if not a.sub or not a.to:
            ap.error(_tr('用法：restore <备份.zip> --to <新数据目录> [--to-db <新库 URL>]'))
        rep = backup.restore(a.sub, a.to, a.to_db)
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        sys.exit(0 if rep.get('ok') else 1)

    db.init(a.db)
    if a.cmd == 'backup':
        from . import backup
        print(json.dumps(backup.create(kind='manual', note=a.note, user='cli'), ensure_ascii=False, indent=1, default=str))
        return
    if a.cmd == 'audit':
        from . import audit
        print(json.dumps(audit.verify(), ensure_ascii=False, indent=1, default=str))
        return
    from . import i18n, seed
    seed.run()
    if a.cmd == 'create-admin':
        import getpass
        import os
        pw = os.environ.get('MOM_ADMIN_PASSWORD') or getpass.getpass('Password (min. 8 characters): ')
        try:
            with db.engine.begin() as c:
                seed.create_admin(c, a.username, pw, a.name)
        except ValueError as e:
            print(e)
            sys.exit(1)
        print('Administrator created:', a.username)
        return
    with db.engine.connect() as c:
        need_admin = seed.needs_admin(c)
    if a.cmd == 'seed':
        if i18n.demo_mode():
            print(_tr('基础数据已就绪。演示账号：admin / manager / planner / leader / operator / qc / maint / wh / engineer，密码均为'), seed.DEMO_PW)
        return
    import uvicorn
    from .app import build_app
    if i18n.demo_mode():
        print(_tr('蜂元智能 MOM：http://127.0.0.1:{http}   演示账号 admin，密码 {DEMO_PW}', http=a.http, DEMO_PW=seed.DEMO_PW), flush=True)
    else:
        print(f'HoneyMOM: http://127.0.0.1:{a.http}', flush=True)
        if need_admin:
            print('First start: create the administrator account in the browser, or run `python -m honeymom create-admin`.', flush=True)
    uvicorn.run(build_app(), host=a.host or '0.0.0.0', port=a.http, log_level='warning')


if __name__ == '__main__':
    main()
