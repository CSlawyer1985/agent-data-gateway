"""CLI 子命令包。每个模块暴露 build(subparsers) 注册参数与 handler。"""

from . import check, doctor, list_, parse, report, scan, setup, status, verify  # noqa: F401
