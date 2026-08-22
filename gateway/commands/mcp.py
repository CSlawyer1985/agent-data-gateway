"""adg mcp — 以只读 MCP server 形态把摄取结果暴露给 Agent。"""

from __future__ import annotations

import sys
from pathlib import Path

from ..mcp import SERVER_NAME, TOOLS, MCPServer
from ._common import print_json, resolve_out_dir


def build(sub) -> None:
    p = sub.add_parser("mcp", help="启动只读 MCP server（stdio）")
    p.add_argument("path", help="材料包目录（server 绑定到这个包）")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--list-tools", action="store_true", help="打印工具清单后退出（不启动 server）")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    if args.list_tools:
        print_json({"server": SERVER_NAME, "tools": TOOLS})
        return 0
    if not (out_dir / "manifest.json").exists():
        print(f"adg: 未找到 manifest（{out_dir.name}）——请先 adg scan / adg parse", file=sys.stderr)
        return 2
    # stdout 是 JSON-RPC 通道：这里之后不再打印任何非协议内容
    server = MCPServer(package_path, out_dir)
    return server.serve()
