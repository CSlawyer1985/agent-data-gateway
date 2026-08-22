"""安全策略加载与不可信文件防护（spec 06）。

策略数据：models/security_policy.v0.1.json（本模块直接加载执行）。
防护覆盖：压缩包炸弹/路径穿越/符号链接逃逸/极端嵌套/海量成员、活动内容检测、
结构上限。所有拦截必须显性记录，不得静默丢弃。
"""

from __future__ import annotations

import json
import stat
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

_POLICY_PATH = Path(__file__).resolve().parent.parent / "models" / "security_policy.v0.1.json"


def load_policy(path: Path | None = None) -> dict[str, Any]:
    p = path or _POLICY_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


DEFAULT_POLICY: dict[str, Any] = load_policy()


@dataclass
class MemberVerdict:
    name: str
    ok: bool
    reason: str = ""
    size: int = 0


@dataclass
class ExpansionPlan:
    """压缩包展开计划：批准的成员 + 拒绝记录 + 截断标记。"""

    approved: list[MemberVerdict] = field(default_factory=list)
    rejected: list[MemberVerdict] = field(default_factory=list)
    encrypted: list[str] = field(default_factory=list)
    truncated: bool = False
    stop_reason: str = ""

    def reject(self, name: str, reason: str, size: int = 0) -> None:
        self.rejected.append(MemberVerdict(name=name, ok=False, reason=reason, size=size))


class ArchiveGuard:
    """按策略审查压缩包成员，产出 ExpansionPlan。只审查，不解压。"""

    def __init__(self, policy: dict[str, Any] | None = None):
        pol = (policy or DEFAULT_POLICY)["archive"]
        self.max_depth = pol["max_depth"]
        self.max_members = pol["max_members"]
        self.max_total = pol["max_total_uncompressed_bytes"]
        self.max_ratio = pol["max_compression_ratio"]
        self.max_single = pol["max_single_member_bytes"]
        self.deny_traversal = pol["deny_path_traversal"]
        self.deny_symlink = pol["deny_symlink_escape"]

    # ---- 名称安全 ----

    def check_name(self, name: str) -> str | None:
        """返回拒绝原因；None 表示通过。"""
        if not name or "\x00" in name:
            return "非法成员名"
        norm = name.replace("\\", "/")
        if norm.startswith("/"):
            return "绝对路径"
        parts = PurePosixPath(norm).parts
        if any(p == ".." for p in parts):
            return "路径穿越"
        for p in parts:
            if len(p) == 2 and p[1] == ":" and p[0].isalpha():  # 盘符 C:
                return "盘符绝对路径"
        if parts and parts[0] in ("~",):
            return "家目录逃逸"
        return None

    # ---- ZIP ----

    def review_zip(self, zf: zipfile.ZipFile, *, depth: int) -> ExpansionPlan:
        plan = ExpansionPlan()
        if depth > self.max_depth:
            plan.truncated = True
            plan.stop_reason = f"嵌套深度超限({depth}>{self.max_depth})"
            return plan
        infos = zf.infolist()
        if len(infos) > self.max_members:
            plan.truncated = True
            plan.stop_reason = f"成员数超限({len(infos)}>{self.max_members})"
            return plan
        total = 0
        for info in infos:
            name = info.filename
            reason = self.check_name(name)
            if reason:
                plan.reject(name, reason)
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                plan.reject(name, "符号链接")
                continue
            if info.flag_bits & 0x1:
                plan.encrypted.append(name)
                continue
            if info.is_dir():
                continue
            if info.file_size > self.max_single:
                plan.reject(name, f"单成员超限({info.file_size}B)", info.file_size)
                continue
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > self.max_ratio:
                    plan.reject(name, f"压缩比异常({ratio:.0f}:1)", info.file_size)
                    continue
            total += info.file_size
            if total > self.max_total:
                plan.truncated = True
                plan.stop_reason = f"累计解压量超限(>{self.max_total}B)"
                plan.reject(name, "超出累计解压上限", info.file_size)
                continue
            plan.approved.append(MemberVerdict(name=name, ok=True, size=info.file_size))
        return plan

    # ---- TAR ----

    def review_tar(self, tf: tarfile.TarFile, *, depth: int) -> ExpansionPlan:
        plan = ExpansionPlan()
        if depth > self.max_depth:
            plan.truncated = True
            plan.stop_reason = f"嵌套深度超限({depth}>{self.max_depth})"
            return plan
        members = tf.getmembers()
        if len(members) > self.max_members:
            plan.truncated = True
            plan.stop_reason = f"成员数超限({len(members)}>{self.max_members})"
            return plan
        total = 0
        for m in members:
            reason = self.check_name(m.name)
            if reason:
                plan.reject(m.name, reason)
                continue
            if m.issym() or m.islnk():
                plan.reject(m.name, "符号/硬链接")
                continue
            if m.isdev():
                plan.reject(m.name, "设备文件")
                continue
            if not m.isfile():
                continue
            if m.size > self.max_single:
                plan.reject(m.name, f"单成员超限({m.size}B)", m.size)
                continue
            total += m.size
            if total > self.max_total:
                plan.truncated = True
                plan.stop_reason = f"累计解压量超限(>{self.max_total}B)"
                plan.reject(m.name, "超出累计解压上限", m.size)
                continue
            plan.approved.append(MemberVerdict(name=m.name, ok=True, size=m.size))
        return plan


def safe_join(dest_dir: Path, member_name: str) -> Path:
    """防御纵深：名称审查后再做一次 realpath 容器内校验。"""
    norm = member_name.replace("\\", "/")
    target = (dest_dir / norm).resolve()
    root = dest_dir.resolve()
    if root != target and root not in target.parents:
        from .errors import SecurityViolation

        raise SecurityViolation(f"成员逃逸目标目录: {member_name!r}")
    return target


def resolve_worker_output(work_dir: Path, untrusted_path: str, *, require_file: bool = True) -> Path:
    """把 worker 声明的相对路径录取为 work_dir 内的普通文件。

    同时拒绝 POSIX/Windows 绝对路径、父目录穿越和任意路径组件中的符号链接。
    worker 进程在协议终态后已被停止，因此校验通过后不会继续改写路径结构。
    """
    from .errors import SecurityViolation

    if not isinstance(untrusted_path, str) or not untrusted_path or "\x00" in untrusted_path:
        raise SecurityViolation("worker 产物路径为空或非法")
    posix = PurePosixPath(untrusted_path.replace("\\", "/"))
    windows = PureWindowsPath(untrusted_path)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise SecurityViolation(f"worker 产物必须是 work_dir 相对路径: {untrusted_path!r}")
    if any(part in ("..", "") for part in posix.parts):
        raise SecurityViolation(f"worker 产物路径穿越: {untrusted_path!r}")

    root = work_dir.resolve()
    candidate = work_dir.joinpath(*posix.parts)
    current = work_dir
    for part in posix.parts:
        current = current / part
        if current.is_symlink():
            raise SecurityViolation(f"worker 产物不得经过符号链接: {untrusted_path!r}")
    target = candidate.resolve()
    if target == root or root not in target.parents:
        raise SecurityViolation(f"worker 产物逃逸 work_dir: {untrusted_path!r}")
    if require_file and not target.is_file():
        raise SecurityViolation(f"worker 声明的产物不存在或不是普通文件: {untrusted_path!r}")
    return target


# ---- 活动内容与可执行文件检测 ----

_EXECUTABLE_MIMES = {
    "application/x-msdownload",
    "application/x-executable",
    "application/x-mach-binary",
    "application/x-elf",
    "application/x-dosexec",
}


def is_executable_mime(mime: str) -> bool:
    return mime in _EXECUTABLE_MIMES


def detect_office_macros(zip_names: list[str]) -> bool:
    """OOXML 包内存在 vbaProject.bin 即视为含宏。"""
    return any(n.lower().endswith("vbaproject.bin") for n in zip_names)


def detect_pdf_javascript(raw: bytes) -> bool:
    """粗检 PDF 中的 JavaScript 动作字典。"""
    return b"/JavaScript" in raw or b"/JS" in raw


def detect_pdf_encrypted(raw: bytes) -> bool:
    """粗检 /Encrypt 字典（尾部 64KB 内为常见位置，全文兜底）。"""
    tail = raw[-65536:]
    return b"/Encrypt" in tail or b"/Encrypt" in raw[:65536]
