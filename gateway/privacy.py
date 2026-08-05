"""隐私保护：路径脱敏与本地私有运行日志（spec 09）。

规则：
1. 所有持久化产物只出现 package_relative_path。
2. 绝对路径只写入 <out>/logs/run-<run_id>.log（可用开关关闭）。
3. 渲染层调用 scrub_text 做兜底清洗，防意外泄漏。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


class RunLog:
    """本地私有运行日志：相对路径 ↔ 绝对路径映射，供本地排障。"""

    def __init__(self, out_dir: Path, run_id: str, *, enabled: bool = True):
        self.enabled = enabled
        from .manifest import internal_dir

        self.path = internal_dir(out_dir, "logs", f"run-{run_id.replace('uuid:', '')}.log")
        self._fp = None
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fp = open(self.path, "a", encoding="utf-8")

    def map_path(self, relative: str, absolute: Path | str) -> None:
        if self._fp:
            self._fp.write(f"MAP\t{relative}\t{absolute}\n")

    def event(self, text: str) -> None:
        if self._fp:
            self._fp.write(f"EVT\t{text}\n")

    def close(self) -> None:
        if self._fp:
            self._fp.close()
            self._fp = None


def display_path(path: Path | str) -> str:
    """给 stdout 用的安全展示：优先 ~ 缩写，绝不打完整绝对路径中段。"""
    s = str(path)
    home = str(Path.home())
    if s.startswith(home):
        return "~" + s[len(home):]
    return s


_ABS_PATTERNS = [
    re.compile(r"/Users/[^\s\"']+"),
    re.compile(r"/home/[^\s\"']+"),
    re.compile(r"[A-Za-z]:\\\\[^\s\"']+"),
    re.compile(r"[A-Za-z]:/[^\s\"']+"),
]

# 系统临时目录形态（macOS /private/tmp 与 /var/folders、POSIX /tmp）——scrub 覆盖，检测按 home_agnostic 单独算
_SCRUB_PATTERNS = _ABS_PATTERNS + [
    re.compile(r"/var/folders/[^\s\"']+"),
    re.compile(r"/private/tmp/[^\s\"']+"),
    re.compile(r"/tmp/[^\s\"']+"),
]


def scrub_text(text: str, allowed_prefixes: Iterable[str] = ("~",)) -> str:
    """兜底清洗：把疑似绝对路径替换为 <path>。allowed_prefixes 保留展示形态。"""
    def _sub(m: re.Match) -> str:
        s = m.group(0)
        for p in allowed_prefixes:
            if s.startswith(p):
                return s
        return "<path>"

    for pat in _SCRUB_PATTERNS:
        text = pat.sub(_sub, text)
    return text


def contains_absolute_path(text: str, *, home_agnostic: bool = True) -> bool:
    """验证矩阵用：文本中是否存在包外绝对路径痕迹。"""
    for pat in _ABS_PATTERNS:
        if pat.search(text):
            return True
    if home_agnostic and re.search(r"/var/folders/|/tmp/[^\s\"']+", text):
        return True
    return False
