"""adg 命令行入口。

输出纪律：stdout 只打一行摘要；详情写 Markdown/JSON 文件；--json 时完整 JSON 到 stdout。
exit code 三态：0=pass / 1=pass_with_warnings / 2=fail。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .commands import check, doctor, list_, parse, report, routes, scan, setup, status, verify
from .errors import ADGError

EXIT_CODE = {"pass": 0, "pass_with_warnings": 1, "fail": 2}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adg",
        description="Agent Data Gateway — 让 Agent 获得可信、完整、可追溯的数据输入。",
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
