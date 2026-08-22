"""只读 MCP server：把摄取结果按 MCP 协议暴露给 Agent（spec 10）。

纪律：

- **纯 stdlib**：JSON-RPC 2.0 over stdio，逐行 UTF-8 JSON，无第三方依赖。
- **只读**：六个工具只查询，不解析、不改 manifest；唯一副作用是按需重建可丢弃的检索索引。
- **stdout 只跑协议**：任何诊断信息走 stderr，避免污染 JSON-RPC 通道。
- **工具语义与 CLI 同源**：全部委托 `gateway.agent_api.PackageView`，杜绝两个入口对同一问题给出不同答案。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from .agent_api import PackageView
from .errors import ADGError

SERVER_NAME = "agent-data-gateway"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2024-11-05"
MAX_RESULT_CHARS = 400_000

INSTRUCTIONS = (
    "Agent Data Gateway 的只读摄取接口。先用 get_ingestion_status 确认材料是否已读全，"
    "再用 search_elements 检索、resolve_anchor 回溯到原件位置后引用。"
    "quotable=false 的命中只能用于检索与线索，不能作为可靠引用；"
    "list_unresolved_items 列出的材料尚未被读取，其内容不在任何检索结果内。"
)


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: list[dict] = [
    {
        "name": "get_ingestion_status",
        "description": "摄取状态总览：覆盖率、已可靠读取 vs 未可靠读取的清单、是否全部入账。任何分析前先调它，确认材料读全了。",
        "inputSchema": _schema({}),
    },
    {
        "name": "list_package_items",
        "description": "列出材料包内的文件条目（含压缩包/邮件展开出的成员），可按 kind、status 过滤，或只看未可靠读取的。",
        "inputSchema": _schema(
            {
                "kind": {"type": "string", "description": "材料类型过滤，如 pdf/docx/xlsx/image/audio/video/email/archive"},
                "status": {"type": "string", "description": "状态过滤，如 parsed/parsed_with_warnings/failed/unresolved"},
                "unreliable_only": {"type": "boolean", "description": "只返回尚未可靠读取的条目"},
                "limit": {"type": "integer", "description": "返回上限，默认 200"},
                "offset": {"type": "integer", "description": "分页偏移，默认 0"},
            }
        ),
    },
    {
        "name": "search_elements",
        "description": "元素级全文检索，命中带来源锚点（页码/坐标/单元格/段落/时间码）。检索范围只覆盖已可靠读取的材料。空格分隔为 AND，双引号内为短语。",
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "检索词"},
                "kind": {"type": "string", "description": "只搜某类材料"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，上限 500"},
                "quotable_only": {"type": "boolean", "description": "只返回可可靠引用（fitness.quotation=true）的命中"},
                "all_renditions": {"type": "boolean", "description": "搜全部 rendition（默认只搜每个文件的最优 rendition）"},
                "path_prefix": {"type": "string", "description": "只搜某个子路径下的材料"},
            },
            ["query"],
        ),
    },
    {
        "name": "get_document",
        "description": "取出单个材料的解析产物：质量八维、fitness、降级链、谱系与元素列表（按 max_elements/offset 分页）。",
        "inputSchema": _schema(
            {
                "ref": {"type": "string", "description": "包内相对路径或 source_id（可给哈希前缀）"},
                "rendition_id": {"type": "string", "description": "指定 rendition，默认取最优"},
                "include_elements": {"type": "boolean", "description": "是否返回元素，默认 true"},
                "max_elements": {"type": "integer", "description": "元素条数上限，默认 200"},
                "offset": {"type": "integer", "description": "元素起始偏移，默认 0"},
            },
            ["ref"],
        ),
    },
    {
        "name": "resolve_anchor",
        "description": "把 element_id 回溯为原件位置：文件、parser、locator、上下文、是否可可靠引用。引用前必查。",
        "inputSchema": _schema(
            {
                "element_id": {"type": "string", "description": "元素 ID（search_elements 结果里的 el_xxxx）"},
                "rendition_id": {"type": "string", "description": "限定某个 rendition"},
            },
            ["element_id"],
        ),
    },
    {
        "name": "list_unresolved_items",
        "description": "列出未能可靠读取的材料及原因与建议动作——这些内容不在检索范围内，结论不得假设已覆盖它们。",
        "inputSchema": _schema({}),
    },
]


class MCPServer:
    """一个 server 实例绑定一个材料包输出目录。"""

    def __init__(self, package_path: Path, out_dir: Path | None = None, *, log: TextIO | None = None):
        self.package_path = Path(package_path)
        self.out_dir = Path(out_dir) if out_dir else None
        self._view: PackageView | None = None
        self._manifest_stamp: tuple[int, int] | None = None
        self._log = log
        self.protocol_version = DEFAULT_PROTOCOL
        self.initialized = False

    # ---------------- 视图（随 manifest 变化自动刷新） ----------------

    def view(self) -> PackageView:
        stamp = self._stamp()
        if self._view is None or stamp != self._manifest_stamp:
            if self._view is not None:
                self._view.close()
            self._view = PackageView.open(self.package_path, self.out_dir)
            self._manifest_stamp = stamp
        return self._view

    def _stamp(self) -> tuple[int, int] | None:
        view_out = self.out_dir
        if view_out is None:
            from .manifest import default_out_dir

            view_out = default_out_dir(Path(self.package_path).resolve())
        path = Path(view_out) / "manifest.json"
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def close(self) -> None:
        if self._view is not None:
            self._view.close()
            self._view = None

    # ---------------- 工具分发 ----------------

    def call_tool(self, name: str, arguments: dict) -> dict:
        view = self.view()
        handlers: dict[str, Callable[[], Any]] = {
            "get_ingestion_status": view.get_ingestion_status,
            "list_package_items": lambda: view.list_package_items(
                kind=arguments.get("kind"),
                status=arguments.get("status"),
                unreliable_only=bool(arguments.get("unreliable_only", False)),
                limit=int(arguments.get("limit", 200)),
                offset=int(arguments.get("offset", 0)),
            ),
            "search_elements": lambda: view.search_elements(
                str(arguments["query"]),
                kind=arguments.get("kind"),
                limit=int(arguments.get("limit", 20)),
                quotable_only=bool(arguments.get("quotable_only", False)),
                all_renditions=bool(arguments.get("all_renditions", False)),
                path_prefix=arguments.get("path_prefix"),
            ),
            "get_document": lambda: view.get_document(
                str(arguments["ref"]),
                rendition_id=arguments.get("rendition_id"),
                include_elements=bool(arguments.get("include_elements", True)),
                max_elements=int(arguments.get("max_elements", 200)),
                offset=int(arguments.get("offset", 0)),
            ),
            "resolve_anchor": lambda: view.resolve_anchor(
                str(arguments["element_id"]), rendition_id=arguments.get("rendition_id")
            ),
            "list_unresolved_items": view.list_unresolved_items,
        }
        handler = handlers.get(name)
        if handler is None:
            raise KeyError(name)
        return handler()

    # ---------------- JSON-RPC ----------------

    def handle(self, message: dict) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(message.get("id") if isinstance(message, dict) else None, -32600, "非法 JSON-RPC 报文")
        method = message.get("method")
        msg_id = message.get("id")
        if method is None:
            return None  # 响应报文：本 server 不发请求，忽略
        params = message.get("params") or {}
        is_notification = "id" not in message

        try:
            result = self._dispatch(method, params)
        except _MethodNotFound:
            return None if is_notification else _error(msg_id, -32601, f"未知方法: {method}")
        except _InvalidParams as e:
            return None if is_notification else _error(msg_id, -32602, str(e))
        except Exception as e:  # noqa: BLE001 —— server 不能因单个请求崩掉
            self._warn(f"{method} 未处理错误: {type(e).__name__}: {e}")
            return None if is_notification else _error(msg_id, -32603, f"内部错误: {type(e).__name__}: {e}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion")
            self.protocol_version = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
            return {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        if method in ("notifications/initialized", "initialized"):
            self.initialized = True
            return {}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise _InvalidParams("tools/call 需要 name 与 arguments 对象")
            try:
                payload = self.call_tool(name, arguments)
            except KeyError:
                raise _InvalidParams(f"未知工具: {name}") from None
            except (ADGError, ValueError, TypeError) as e:
                return _tool_error(f"{type(e).__name__}: {e}")
            return _tool_result(payload)
        raise _MethodNotFound(method)

    # ---------------- 主循环 ----------------

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _write(stdout, _error(None, -32700, "JSON 解析失败"))
                continue
            if isinstance(message, list):  # 兼容旧版批量请求
                for item in message:
                    response = self.handle(item)
                    if response is not None:
                        _write(stdout, response)
                continue
            response = self.handle(message)
            if response is not None:
                _write(stdout, response)
        self.close()
        return 0

    def _warn(self, text: str) -> None:
        stream = self._log if self._log is not None else sys.stderr
        print(f"adg-mcp: {text}", file=stream, flush=True)


class _MethodNotFound(Exception):
    pass


class _InvalidParams(Exception):
    pass


def _write(stdout: TextIO, payload: dict) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_result(payload: Any) -> dict:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n…（结果超过上限已截断，请用 limit/offset 分页）"
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}
