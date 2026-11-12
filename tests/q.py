"""开发辅助：在 MES 库上执行 SQL。python tests/q.py "select ..." """
import sys

import sqlalchemy as sa

from honeymom import db

sys.stdout.reconfigure(encoding='utf-8')
db.init()
with db.engine.connect() as c:
    for s in sys.argv[1:]:
        r = c.execute(sa.text(s))
        print(list(r.keys()))
        for row in r.fetchall()[:60]:
            print(row)
