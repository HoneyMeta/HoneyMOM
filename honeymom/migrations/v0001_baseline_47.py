"""基线：把任何库带到 4.7（2d9ac87）的结构。

- 空库：按 baseline_47 建出 83 张表（只建 4.7 当时的列）。
- 4.7 线上库（含刚升级过的）：表都在，列也都在 → 0 个动作，直接记为已执行。
- 更老的库（4.6 及以前）：补齐缺的表和列（等同 4.7 的 db.migrate()：只加列、不删不改；唯一列补唯一索引）。
"""
from .baseline_47 import BASELINE_47

TITLE = '基线：4.7 表结构'


def up(op):
    for name, cols in BASELINE_47.items():
        if not op.has_table(name):
            op.create_table(name, columns=cols)
            continue
        for col in cols:
            op.add_column(name, col)
