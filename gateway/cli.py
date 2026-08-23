"""adg 命令行入口。

输出纪律：stdout 只打一行摘要；详情写 Markdown/JSON 文件；--json 时完整 JSON 到 stdout。
exit code 三态：0=pass / 1=pass_with_warnings / 2=fail。
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .commands import (
    anchor,
    check,
    doctor,
    get,
    index_,
    list_,
    parse,
    report,
    routes,
    scan,
    search,
    setup,
    status,
    verify,
)
from .errors import ADGError


AGENT_NOTES = """
典型流程：
  adg scan <包>             全量发现，建清单（不解析内容）
  adg parse <包>            解析并建检索索引
  adg search <包> <词>      检索，命中带来源锚点

退出码：0=全部可靠读取   1=完成但有未读全项（不是失败）   2=失败

检索范围只覆盖已可靠读取的材料——"没搜到"不等于"不存在"。查询结果的 JSON 里
带 coverage_caveat，说明还有多少材料不在范围内；extraction_coverage 是
文件级完整性（多少份材料读到了），不是文件内信息的利用率。
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adg",
        description="Agent Data Gateway — 让 Agent 获得可信、完整、可追溯的数据输入。",
        # 调用方是编码 Agent，--help 是它接触本工具的第一动作（ADR 0003 决策 7）：
        # 退出码语义、流程顺序与检索范围警告只有写在这里才到得了它眼前。
        epilog=AGENT_NOTES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"adg {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    check.build(sub)
    doctor.build(sub)
    setup.build(sub)
    scan.build(sub)
    parse.build(sub)
    report.build(sub)
    status.build(sub)
    list_.build(sub)
    index_.build(sub)
    search.build(sub)
    anchor.build(sub)
    get.build(sub)
    routes.build(sub)
    verify.build(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args))
    except KeyboardInterrupt:
        print("adg: 已中断", file=sys.stderr)
        return 130
    except ADGError as e:
        print(f"adg: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"adg: 未处理错误: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
