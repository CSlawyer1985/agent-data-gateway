"""查看由运行时实际使用的总路由表。"""

from __future__ import annotations

from ..routing import route_catalog
from ._common import print_json, summary_line


def build(sub) -> None:
    p = sub.add_parser("routes", help="查看格式、触发条件、处理原因与工具链")
    p.add_argument("--kind", help="按类型过滤（如 pdf/email/archive）")
    p.add_argument("--format", dest="format_filter", help="按格式或扩展名过滤（如 msg/.pdf）")
    p.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    p.set_defaults(handler=run)


def _condition(row: dict) -> str:
    parts = []
    formats = row["when"]["format_in"]
    if formats:
        parts.append("format=" + "/".join(formats))
    for key, value in row["when"]["features"].items():
        parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else "默认"


def run(args) -> int:
    rows = route_catalog(kind=args.kind, format_=args.format_filter)
    payload = {
        "schema_version": "0.1",
        "authority": "gateway.routing.ROUTE_RULES",
        "recognition_authority": "gateway.magic",
        "note": "路由表表示项目配置能力；worker 在当前机器是否可用请运行 adg check。",
        "total": len(rows),
        "routes": rows,
    }
    if args.json:
        print_json(payload)
        return 0

    summary_line(f"routes: {len(rows)} 条（当前机器可用性请运行 adg check）")
    for row in rows:
        extensions = ",".join(row["extensions"]) or "—"
        tools = " → ".join(row["tools"]) or "—"
        print(f"  [{row['action']:<10}] {row['kind']:<12} {extensions}")
        print(f"    条件: {_condition(row)}")
        print(f"    工具: {tools}")
        print(f"    原因: {row['reason']}")
    return 0
