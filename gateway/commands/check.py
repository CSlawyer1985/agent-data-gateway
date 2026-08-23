"""adg check — 环境检测：Python/容器/CPU/内存/GPU/Java/.NET + 各 worker 可用性。"""

from __future__ import annotations

import sys

from ._common import build_registry, get_env, print_json, summary_line


def build(sub) -> None:
    p = sub.add_parser("check", help="环境检测与 worker 可用性")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    env = get_env()
    registry = build_registry()
    workers_report = []
    available = 0
    for w in sorted(registry.workers.values(), key=lambda x: x.name):
        status = registry.availability_status(w, env)
        workers_report.append(
            {
                "name": w.name,
                "version": w.version,
                "backend": w.backend,
                "kinds": w.kinds,
                "available": status.available,
                "availability_reason": status.reason,
                "detail": status.detail,
                "requirement_problems": list(status.requirement_problems),
                "maturity": w.maturity,
            }
        )
        if status.available:
            available += 1
    report = {
        "environment": env.to_dict(),
        "workers": workers_report,
        "workers_available": available,
        "workers_total": len(workers_report),
    }
    if args.json:
        print_json(report)
    else:
        c = env.container
        summary_line(
            f"python {env.python_version} | RAM {env.total_ram_mb or '?'}MB | GPU {env.gpu.get('kind', '?')} | "
            f"容器 {c.get('name') or '无'}{'(运行中)' if c.get('running') else ''} | "
            f"worker {available}/{len(workers_report)} 可用"
        )
        for w in workers_report:
            mark = "✅" if w["available"] else "—"
            detail = "" if w["available"] else f"（{w['detail']}）"
            print(f"  {mark} {w['name']:<14} {w['backend']:<10} {','.join(w['kinds'])[:36]:<36} {detail}")
    # python 版本过低才失败；worker 缺失只是现状报告
    major, minor = sys.version_info[:2]
    return 0 if (major, minor) >= (3, 10) else 2
