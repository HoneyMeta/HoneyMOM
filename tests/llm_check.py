"""检查大模型配置：直接调用问数助手（需要库里已有数据）。python tests/llm_check.py "问题" """
import sys
import time

from honeymom import db
from honeymom.services import ai

sys.stdout.reconfigure(encoding='utf-8')
db.init()
print('配置：', {k: (v[:6] + '…' if k == 'api_key' and v else v) for k, v in ai.llm_cfg().items()})
t0 = time.time()
print('连通：', ai.chat_once('只回复两个字：收到')[:20], f'{time.time() - t0:.1f}s')
q = sys.argv[1] if len(sys.argv) > 1 else '各台 CNC 今天加工了多少件？哪台最少？'
t0 = time.time()
r = ai.ask({'q': q}, {'username': 'test'})
print(f'问：{q}（{time.time() - t0:.1f}s）')
print('执行的查询：', *r['sqls'], sep='\n  ')
print('答：', r['answer'])
