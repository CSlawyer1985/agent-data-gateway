"""adg status — 摄取状态：哪些已读全、哪些未可靠读取。"""

from __future__ import annotations

from pathlib import Path

from ..manifest import list_rendition_docs, load_manifest
from ..quality import reliably_read_item
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("status", help="摄取状态：已读全 vs 未可靠读取")
    p.add_argument("path", help="材料包目录")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    manifest = load_manifest(out_dir)
    if manifest is None:
        summary_line("未找到 manifest——请先 adg scan")
        return 2

    reliable_items: list[dict] = []
    unreliable_items: list[dict] = []
    unresolved_reasons = {
        u["source_id"]: u["reason"] for u in manifest.integrity.get("unresolved_items", [])
    }
    for it in manifest.items:
        if it.status == "unsupported":
            continue
        docs = list_rendition_docs(out_dir, it.source_id)
        ok = reliably_read_item(it.kind, it.status, [d.get("quality", {}) for d in docs])
        entry = {"path": it.package_relative_path, "kind": it.kind, "status": it.status}
        if ok:
            reliable_items.append(entry)
        else:
            reason = unresolved_reasons.get(it.source_id) or (it.errors or [None])[0] or "尚无可靠 rendition"
            entry["reason"] = reason
            unreliable_items.append(entry)

    result = {
        "package_root_name": manifest.package_root_name,
        "reliably_read_count": len(reliable_items),
        "not_reliable_count": len(unreliable_items),
        "reliably_read": reliable_items,
        "not_reliably_read": unreliable_items,
        "all_items_accounted": manifest.integrity.get("all_items_accounted", False),
    }
    if args.json:
        print_json(result)
    else:
        summary_line(
            f"status: 已可靠读取 {len(reliable_items)} 项；未可靠读取 {len(unreliable_items)} 项"
            + (f"（含 {unreliable_items[0]['path']} 等）" if unreliable_items else "——材料包已读全")
        )
        for e in unreliable_items[:20]:
            print(f"  ❔ {e['path']}（{e['kind']}）: {e['reason']}")
    return 0 if not unreliable_items else 1
