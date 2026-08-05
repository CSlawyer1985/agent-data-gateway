"""adg scan — 全量发现：递归目录 → 哈希 → 类型识别 → 压缩包安全展开 → manifest。

不解析内容（parse 阶段才解析）。--resume 保留已有解析状态做增量对账。
"""

from __future__ import annotations

from pathlib import Path

from .. import inventory
from ..discover import discover_package
from ..manifest import MANIFEST_MD, atomic_write_text, load_manifest, render_manifest_md, save_manifest
from ..models import now_iso
from ..privacy import RunLog
from ._common import out_hint, print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("scan", help="全量发现，生成材料包清单（不解析内容）")
    p.add_argument("path", help="材料包目录（或单文件）")
    p.add_argument("--out", help="输出目录（默认 ./adg-output/<包名>/）")
    p.add_argument("--resume", action="store_true", help="增量：保留已有解析状态对账")
    p.add_argument("--force", action="store_true", help="重新发现（丢弃旧状态）")
    p.add_argument("--no-abs-log", action="store_true", help="关闭绝对路径私有日志")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def _build_7z_expander():
    """py7zr worker 可用时，构造 7z 展开器（经 worker 协议，安全审查由编排器二次执行）。"""
    from ..workers import WorkerRegistry, execute_worker

    registry = WorkerRegistry()
    registry.load_external()
    w = registry.get("py7zr")
    if w is None:
        return None
    ok, _ = registry.availability(w)
    if not ok:
        return None

    def expand(archive_path: Path, dest_dir: Path) -> dict:
        request = {
            "protocol_version": "0.1",
            "type": "parse_request",
            "job_id": f"expand-{archive_path.stem}",
            "source_path": str(archive_path),
            "source_id": "",
            "kind": "archive",
            "format": "7z",
            "options": {},
            "work_dir": str(dest_dir),
        }
        result = execute_worker(w, request, source_path=archive_path, work_dir=dest_dir)
        if result.get("status") != "ok":
            err = (result.get("error") or {}).get("message", "展开失败")
            return {"members": [], "rejected": [{"name": "*", "reason": err}]}
        members = [of["path"] for of in (result.get("output_files") or [])]
        rejected = ((result.get("fragment") or {}).get("rejected")) or []
        return {"members": members, "rejected": rejected}

    return expand


def run(args) -> int:
    package_path = Path(args.path).resolve()
    if not package_path.exists():
        summary_line(f"路径不存在: {args.path}")
        return 2
    out_dir = resolve_out_dir(args, package_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_log = RunLog(out_dir, "scan-" + now_iso().replace(":", ""), enabled=not args.no_abs_log)
    from packs.legal import chat_kind_refiner

    expanders = {}
    expander_7z = _build_7z_expander()
    if expander_7z:
        expanders["7z"] = expander_7z

    result = discover_package(
        package_path,
        out_dir,
        run_log=run_log,
        kind_refiners=[chat_kind_refiner()],
        archive_expanders=expanders,
    )
    manifest = inventory.build_manifest(result)

    # --resume：与既有 manifest 对账，保留解析状态
    if args.resume and not args.force:
        old = load_manifest(out_dir)
        if old is not None:
            old_by_id = {it.source_id: it for it in old.items}
            new_ids = {it.source_id for it in manifest.items}
            for it in manifest.items:
                prev = old_by_id.get(it.source_id)
                if prev is not None:
                    it.renditions = prev.renditions
                    it.status = prev.status
                    it.best_quality = prev.best_quality
                    it.errors = prev.errors
            for sid, prev in old_by_id.items():
                if sid not in new_ids and prev.renditions:
                    manifest.items.append(prev)
                    manifest.integrity["unresolved_items"].append(
                        {
                            "source_id": sid,
                            "package_relative_path": prev.package_relative_path,
                            "reason": "本次 scan 时已不存在于包内（保留历史 rendition）",
                            "stage": "discovery",
                            "suggested_action": None,
                        }
                    )
            manifest.integrity["expanded_emails"] = old.integrity.get("expanded_emails", [])
            manifest.refresh_stats()
            manifest.compute_package_id()
    manifest.updated_at = now_iso()

    inventory.recompute_package_quality(manifest, reliably_read_ids=set(), discovered_only=True)
    save_manifest(manifest, out_dir)
    atomic_write_text(out_dir / MANIFEST_MD, render_manifest_md(manifest))
    run_log.close()

    stats = manifest.stats
    unresolved = len(manifest.integrity.get("unresolved_items", []))
    if args.json:
        print_json(manifest.to_dict())
    else:
        summary_line(
            f"scan 完成: {stats['total_items']} 项（去重节省 {stats['dedup_saved']}，"
            f"压缩包 {len(manifest.integrity.get('expanded_archives', []))} 个，未解决 {unresolved}）"
            f" → {out_hint(out_dir)}/manifest.md"
        )
    if not manifest.integrity.get("all_items_accounted", True):
        return 2
    return 1 if unresolved or manifest.quality.get("corrupted") else 0
