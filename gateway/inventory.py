"""清单构建：DiscoveryResult → PackageManifest（Item 注册、包级质量聚合）。

parse 阶段也复用本模块把 rendition/状态回写 manifest。
"""

from __future__ import annotations


from . import magic, signals as signals_mod
from .discover import DiscoveryResult
from .models import Item, PackageManifest, now_iso


def build_manifest(result: DiscoveryResult) -> PackageManifest:
    """scan 阶段：发现结果 → 初始 manifest（全部 item status=discovered）。"""
    root_name = result.root.name if result.root.is_dir() else result.root.parent.name
    m = PackageManifest(package_root_name=root_name)
    for ff in result.files:
        item = Item(
            source_id=ff.source_id,
            package_relative_path=ff.package_relative_path,
            kind=ff.detected.kind,
            format=ff.detected.format,
            bytes=ff.bytes,
            origin=ff.origin,
            detected_type=ff.detected.to_dict(),
            signals=signals_mod.probe(ff.detected.kind, ff.abs_path, ff.detected) or None,
            mtime=ff.mtime,
        )
        if ff.detected.kind == magic.EXECUTABLE:
            item.status = "unsupported"
            item.errors = ["可执行文件不入解析范围（安全策略）"]
        elif ff.detected.kind == magic.UNKNOWN:
            item.status = "unresolved"
            item.errors = ["类型无法识别"]
        m.items.append(item)

    # scan 阶段 unresolved：发现/展开/安全拦截
    m.integrity["unresolved_items"] = [dict(u) for u in result.unresolved]
    for item in m.items:
        if item.status in ("unresolved", "unsupported") and item.errors:
            m.integrity["unresolved_items"].append(
                {
                    "source_id": item.source_id,
                    "package_relative_path": item.package_relative_path,
                    "reason": item.errors[0],
                    "stage": "type_detection",
                    "suggested_action": None,
                }
            )
    m.integrity["expanded_archives"] = result.expanded_archives
    # 邮件附件在 parse 阶段递归回灌；scan 先登记 pending
    m.integrity["expanded_emails"] = [
        {
            "email_source_id": it.source_id,
            "package_relative_path": it.package_relative_path,
            "status": "pending",
            "attachment_count": None,
        }
        for it in m.items
        if it.kind == magic.EMAIL
    ]
    m.integrity["all_items_accounted"] = result.all_items_accounted
    m.integrity["payload_sha256"] = ""

    m.quality["duplicates"] = result.duplicates
    m.quality["empties"] = result.empties
    m.quality["encrypted"] = result.encrypted
    m.quality["corrupted"] = result.corrupted
    m.quality["unsupported"] = [
        it.package_relative_path for it in m.items if it.status == "unsupported"
    ]
    m.quality["low_confidence"] = [
        it.package_relative_path
        for it in m.items
        if (it.detected_type or {}).get("confidence") == "low"
    ]
    m.refresh_stats()
    m.stats["dedup_saved"] = result.dedup_saved
    m.compute_package_id()
    m.updated_at = now_iso()
    return m


def recompute_package_quality(m: PackageManifest, *, reliably_read_ids: set[str], discovered_only: bool = False) -> None:
    """parse/report 阶段：重算包级 overall 与 extraction_coverage。

    discovered_only=True 用于 scan 阶段：尚未解析，covered 无从计算，
    不适用"全灭才 fail"语义——有 unresolved 只给 pass_with_warnings（与 exit code 1 一致）。
    """
    total = len(m.items)
    covered = sum(1 for it in m.items if it.source_id in reliably_read_ids)
    m.quality["extraction_coverage"] = round(covered / total, 4) if total else 0.0
    unresolved_count = len(m.integrity.get("unresolved_items", []))
    failed_items = [it for it in m.items if it.status in ("failed", "unresolved", "unsupported")]
    if discovered_only and (failed_items or unresolved_count):
        m.quality["overall"] = "pass_with_warnings"
    elif not discovered_only and total > 0 and covered == 0:
        m.quality["overall"] = "fail"
    elif (
        (not discovered_only and covered < total)
        or failed_items
        or unresolved_count
        or m.quality.get("encrypted")
        or m.quality.get("corrupted")
        or m.quality.get("duplicates")
        or m.quality.get("low_confidence")
    ):
        m.quality["overall"] = "pass_with_warnings"
    else:
        m.quality["overall"] = "pass"
    m.updated_at = now_iso()


def sync_item_status(m: PackageManifest, *, reliably_read_ids: set[str]) -> None:
    """按 rendition 质量回写 item.status。

    status 表达"解析是否完成"：parsed/parsed_with_warnings/failed。
    reliably_read（是否可作事实依据）由 status 命令用 reliably_read_item 另算，不混入 status。

    容器类（archive）与已显式判定 unresolved/unsupported 的 item 不重算——
    其状态由 dispatcher._finalize_container_item 或 scan 阶段决定。
    """
    status_by_quality = {"pass": "parsed", "pass_with_warnings": "parsed_with_warnings", "fail": "failed"}
    for it in m.items:
        if it.status in ("unsupported", "unresolved"):
            continue
        if it.kind == "archive":
            # 容器：展开即"已读"，状态由 finalize_container_item 定稿，不依赖 rendition
            continue
        if it.renditions:
            # 有 rendition：按最优 rendition 质量映射
            it.status = status_by_quality.get(it.best_quality, "parsed_with_warnings")
        else:
            # 无 rendition：保留 transform 成功(parsed)的状态；其余回到 discovered/unresolved
            if it.status not in ("parsed", "parsed_with_warnings"):
                it.status = "discovered" if not it.errors else "unresolved"
