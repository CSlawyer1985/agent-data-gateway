"""adg status — 摄取状态：哪些已读全、哪些未可靠读取。

判定逻辑与 adg search 的覆盖提示同源（gateway.agent_api），避免同一问题出现两种答案。
"""

from __future__ import annotations

from pathlib import Path

from ..agent_api import open_package
from ..errors import ADGError
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
    try:
        with open_package(package_path, out_dir) as view:
            result = view.get_ingestion_status()
    except ADGError as e:
        summary_line(str(e))
        return 2

    unreliable = result["not_reliably_read"]
    not_run = result["chains_not_run"]
    if args.json:
        print_json(result)
    else:
        # 「已读全」只在两件事都成立时才说得出口：文件都可靠读取了，且计划的链都跑过了。
        all_clear = not unreliable and not not_run
        summary_line(
            f"status: 已可靠读取 {result['reliably_read_count']} 项；未可靠读取 {len(unreliable)} 项"
            + (f"（含 {unreliable[0]['package_relative_path']} 等）" if unreliable else
               ("——材料包已读全" if all_clear else ""))
        )
        for e in unreliable[:20]:
            print(f"  ❔ {e['package_relative_path']}（{e['kind']}）: {e['reason']}")
        interceptions = result["security_interceptions"]
        if interceptions:
            total = sum(len(r["rejected_members"]) for r in interceptions)
            print(f"  ⛔ {total} 个压缩包成员被安全拦截：")
            for r in interceptions[:10]:
                for mm in r["rejected_members"][:5]:
                    print(f"     {r['package_relative_path']}::{mm['name']}（{mm['reason']}）")
        if not_run:
            print(f"  ⚠️ {len(not_run)} 条链未执行——文件都处理了，但这些角度没看过：")
            for e in not_run[:10]:
                print(f"     {e['package_relative_path']}: {e['rule_id']}（{e['reason']}）")
            print("     补跑: adg parse <包> --profile quality")
    return 0 if not unreliable and not not_run else 1
