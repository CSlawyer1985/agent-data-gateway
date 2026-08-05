"""adg verify — 跑验证矩阵自检（对应 plan §16）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..selftest import render_matrix_md, run_matrix
from ._common import out_hint, print_json, summary_line


def build(sub) -> None:
    p = sub.add_parser("verify", help="验证矩阵自检")
    p.add_argument("--work-dir", help="自检工作目录（默认系统临时目录，结束后清理）")
    p.add_argument("--keep", action="store_true", help="保留自检产物（排障用）")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    work_root = Path(args.work_dir).resolve() if args.work_dir else None
    result = run_matrix(work_root, keep=args.keep or bool(work_root))

    md = render_matrix_md(result)
    out_dir = Path(tempfile.mkdtemp(prefix="adg-verify-report-")) if result.work_dir is None else result.work_dir
    report_path = out_dir / "verify.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(md, encoding="utf-8")

    if args.json:
        print_json(
            {
                "rows": [{"name": r.name, "status": r.status, "detail": r.detail} for r in result.rows],
                "failed": result.failed,
                "skipped": result.skipped,
                "report": str(report_path),
            }
        )
    else:
        passed = sum(1 for r in result.rows if r.status == "pass")
        summary_line(
            f"verify: {passed}/{len(result.rows)} 通过（失败 {result.failed}，跳过 {result.skipped}）→ {out_hint(report_path.parent)}/verify.md"
        )
        for r in result.rows:
            if r.status != "pass":
                icon = "❌" if r.status == "fail" else "⏭️"
                print(f"  {icon} {r.name}: {r.detail}")
    return 2 if result.failed else 0
