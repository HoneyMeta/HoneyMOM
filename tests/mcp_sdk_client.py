"""用官方 MCP Python SDK（pip 包 mcp）当客户端核对蜂元智能 MOM 的 MCP 服务（tests/ai50_check.py 调用；需要单独装 mcp，不进 requirements）。

python tests/mcp_sdk_client.py http <MES 地址>/mcp <API 密钥>
python tests/mcp_sdk_client.py stdio <python.exe> <启动脚本或 -m> <API 密钥> [工作目录]
输出一行 JSON：{ok, protocol, server, tools, resources, prompts, call, resource, error}
"""
import asyncio
import json
import os
import sys

if os.environ.get("MCP_SDK_PATH"):          # 嵌入式 Python 不认 PYTHONPATH：SDK 装在单独目录时用这个环境变量指过去
    _p = os.environ["MCP_SDK_PATH"]
    sys.path[:0] = [_p, os.path.join(_p, "win32"), os.path.join(_p, "win32", "lib")]        # pywin32 用 --target 装时的目录
    if os.path.isdir(os.path.join(_p, "pywin32_system32")):
        os.add_dll_directory(os.path.join(_p, "pywin32_system32"))


async def session_check(read, write):
    from mcp import ClientSession

    def g(o, *names):                       # SDK 1.x 用 camelCase、2.x 用 snake_case
        for n in names:
            if hasattr(o, n):
                return getattr(o, n)
        return None
    out = {}
    async with ClientSession(read, write) as s:
        init = await s.initialize()
        out["protocol"] = g(init, "protocol_version", "protocolVersion")
        out["server"] = g(g(init, "server_info", "serverInfo"), "name")
        out["tools"] = [t.name for t in (await s.list_tools()).tools]
        out["resources"] = [str(r.uri) for r in (await s.list_resources()).resources]
        out["prompts"] = [p.name for p in (await s.list_prompts()).prompts]
        r = await s.call_tool("factory_model", {})
        out["call"] = {"isError": g(r, "is_error", "isError"), "text": (r.content[0].text if r.content else "")[:200], "structured": bool(g(r, "structured_content", "structuredContent"))}
        try:
            r2 = await s.call_tool("no_such_tool", {})
            out["unknown_tool"] = {"isError": g(r2, "is_error", "isError"), "text": (r2.content[0].text if r2.content else "")[:120]}
        except Exception as ex:                # JSON-RPC 错误（-32602）在 SDK 里是异常
            out["unknown_tool"] = {"rpc_error": str(ex)[:160]}
        rr = await s.read_resource("mes://kpi-definitions")
        out["resource"] = rr.contents[0].text[:80]
        await s.send_ping()
        out["ping"] = True
    return out


async def main():
    mode = sys.argv[1]
    try:
        if mode == 'http':
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
            client = httpx2.AsyncClient(headers={'X-API-Key': sys.argv[3]}, timeout=60)
            async with streamable_http_client(sys.argv[2], http_client=client) as streams:
                out = await session_check(streams[0], streams[1])
        else:
            from mcp.client.stdio import StdioServerParameters, stdio_client
            args = sys.argv[3].split(' ') + ['mcp', '--key', sys.argv[4]]
            env = {k: os.environ[k] for k in ('HM_DATA', 'MES_DB', 'PYTHONIOENCODING', 'SYSTEMROOT', 'PATH') if k in os.environ}
            p = StdioServerParameters(command=sys.argv[2], args=args, env=env, cwd=sys.argv[5] if len(sys.argv) > 5 else None)
            async with stdio_client(p) as streams:
                out = await session_check(streams[0], streams[1])
        out['ok'] = True
    except BaseException as ex:  # noqa
        import traceback
        out = {'ok': False, 'error': f'{type(ex).__name__}: {ex}', 'trace': traceback.format_exc()[-1500:]}
    print(json.dumps(out, ensure_ascii=False, default=str))


asyncio.run(main())
