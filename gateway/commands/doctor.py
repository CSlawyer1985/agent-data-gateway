"""adg doctor — 按实际材料诊断 worker 缺口，给出安装建议。

不被动罗列"装了啥"，而是 scan 本包 → 看每类文件需要哪些 parser → 对照本机列缺口 → 建议怎么补。
"""

from __future__ import annotations

from pathlib import Path

from .. import inventory
from ..discover import discover_package
from ..gap import analyze_manifest_gaps, render_doctor_md
from ..hardware import detect_environment
from ..manifest import MANIFEST_MD, atomic_write_text, load_manifest, render_manifest_md, save_manifest
from ..models import now_iso
from ..privacy import RunLog
from ..workers import WorkerRegistry
from ._common import build_registry, out_hint, print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("doctor", help="按实际材料诊断 worker 缺口与安装建议")
    p.add_argument("path", help="材料包目录")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--no-rescan", action="store_true", help="复用已有 manifest，不重新 scan")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = load_manifest(out_dir)
    if existing is not None:
        manifest = existing  # 复用（含历史 parse 状态）
    elif args.no_rescan:
        summary_line("未找到 manifest——先 adg scan，或去掉 --no-rescan 让 doctor 现场扫描")
        return 2
    else:
        manifest = _scan_fresh(package_path, out_dir)

    registry = build_registry()
    env = detect_environment()
    report = analyze_manifest_gaps(manifest, registry, env)

    atomic_write_text(out_dir / "reports" / "doctor.md", render_doctor_md(report))
    if args.json:
        print_json(_report_to_json(report))
    else:
        blocked = report.blocked_kinds
        summary_line(
            f"doctor: {report.summary}"
            + (f" → {out_hint(out_dir)}/reports/doctor.md" if blocked or report.install_plan else "（无需补装）")
        )
        for kind, g in report.by_kind.items():
            if g.verdict == "ok":
                continue
            icon = {"needs_install": "⏬", "no_parser": "❌", "partial": "⚠️", "upgrade": "⏫"}.get(g.verdict, "·")
            extra = f" → 装 {', '.join(g.installable)}" if g.installable else f"（{g.detail}）"
            print(f"  {icon} {kind:<10} ({g.count} 个) {extra}")
    # 有缺口/可升级需安装 → 退出码 1（警告）；无缺口 0
    needs = report.install_plan or any(g.verdict == "no_parser" for g in report.by_kind.values())
    return 1 if needs else 0


def _scan_fresh(package_path: Path, out_dir: Path):
    run_log = RunLog(out_dir, "doctor-" + now_iso().replace(":", ""), enabled=True)
    from packs.legal import chat_kind_refiner

    result = discover_package(package_path, out_dir, run_log=run_log, kind_refiners=[chat_kind_refiner()])
    manifest = inventory.build_manifest(result)
    inventory.recompute_package_quality(manifest, reliably_read_ids=set())
    save_manifest(manifest, out_dir)
    atomic_write_text(out_dir / MANIFEST_MD, render_manifest_md(manifest))
    run_log.close()
    return manifest


def _report_to_json(report) -> dict:
    return {
        "package_root_name": report.package_root_name,
        "total_items": report.total_items,
        "summary": report.summary,
        "install_plan": report.install_plan,
        "kinds": {
            kind: {
                "count": g.count,
                "verdict": g.verdict,
                "available": g.available,
                "missing": g.missing,
                "installable": g.installable,
                "binary_hints": g.binary_hints,
                "detail": g.detail,
            }
            for kind, g in report.by_kind.items()
        },
    }
