"""CLI 子命令包。每个模块暴露 build(subparsers) 注册参数与 handler。"""

from . import (  # noqa: F401
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
