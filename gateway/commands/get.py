"""adg get — 取出单个材料的解析产物（EvidenceDocument 视图）。"""

from __future__ import annotations

from pathlib import Path

from ..agent_api import open_package
from ..render import format_locator
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("get", help="按路径或 source_id 取出解析产物与元素")
    p.add_argument("path", help="材料包目录")
    p.add_argument("ref", help="包内相对路径或 source_id（可给哈希前缀）")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--rendition", dest="rendition_id", help="指定 rendition（默认取最优）")
    p.add_argument("--limit", type=int, default=50, help="返回元素条数上限（默认 50）")
    p.add_argument("--offset", type=int, default=0, help="元素起始偏移（分页）")
    p.add_argument("--meta-only", action="store_true", help="只看元信息与质量，不取元素")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    with open_package(package_path, out_dir) as view:
        doc = view.get_document(
            args.ref,
            rendition_id=args.rendition_id,
            include_elements=not args.meta_only,
            max_elements=args.limit,
            offset=args.offset,
        )

    if args.json:
        print_json(doc)
        return 0 if doc["selected_rendition"] else 1

    sel = doc["selected_rendition"]
    summary_line(
        f"get: {doc['package_relative_path']}（{doc['kind']}，{doc['status']}）"
        + (f"，{doc['elements_total']} 个元素" if sel else "，无解析产物")
    )
    if not sel:
        seen: set[str] = set()
        for e in doc.get("errors") or []:
            if e in seen:
                continue
            seen.add(e)
            print(f"  ❌ {e}")
        print(f"  {doc.get('note', '')}")
        return 1
    fit = sel["fitness"]
    print(f"  parser={sel['parser']} {sel['parser_version'] or ''} quality={sel['quality'].get('overall')}")
    print(
        "  fitness: "
        + " ".join(f"{k}={'✓' if v else '✗'}" for k, v in fit.items())
    )
    if sel.get("fell_back_from"):
        chain = " → ".join(f"{f.get('name')}({f.get('reason')})" for f in sel["fell_back_from"])
        print(f"  降级链: {chain}")
    print(f"  产物视图: {doc['rendering_relpath']}")
    for el in doc["elements"]:
        text = (el["text"] or "").replace("\n", " ")
        print(f"  [{el['element_id']}] {format_locator(el['locator'])}: {text[:120]}{'…' if len(text) > 120 else ''}")
    if doc.get("elements_truncated"):
        print(f"  … 其余元素用 --offset {args.offset + len(doc['elements'])} 继续")
    return 0
