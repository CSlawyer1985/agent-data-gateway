"""子命令公共工具：registry 构建、输出目录解析、摘要打印。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..hardware import Environment, detect_environment
from ..manifest import default_out_dir
from ..privacy import display_path
from ..workers import WorkerRegistry


def build_registry() -> WorkerRegistry:
    """lite parser + 外部 worker 全量注册。"""
    from packs.legal import register_lite_parsers

    registry = WorkerRegistry()
    register_lite_parsers(registry)
    registry.load_external()
    return registry


def resolve_out_dir(args, package_path: Path) -> Path:
    out = getattr(args, "out", None)
    if out:
        return Path(out).resolve()
    return default_out_dir(package_path)


def get_env() -> Environment:
    return detect_environment()


def print_json(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def summary_line(text: str) -> None:
    print(f"adg: {text}")


def out_hint(out_dir: Path) -> str:
    return display_path(out_dir)
