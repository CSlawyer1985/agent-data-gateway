"""全量发现：目录递归、哈希、类型识别、去重、压缩包安全展开（spec 00/06）。

铁律：发现的每个文件（含容器成员）都必须有 item 记录或显式排除记录。
原件只读——所有展开产物写入 out_dir/expanded/，绝不写回包目录。
"""

from __future__ import annotations

import gzip
import io
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import magic
from . import manifest as manifest_mod
from .errors import SecurityViolation
from .hashing import source_id_of_bytes, source_id_of_file
from .privacy import RunLog
from .security import ArchiveGuard, load_policy, safe_join

# kind_refiner: (path, detected, head_bytes) -> DetectedType | None
KindRefiner = Callable[[Path, magic.DetectedType, bytes], magic.DetectedType | None]
# archive_expander: (archive_path, dest_dir, member_review: ExpansionPlan|None) -> dict
#   外部 worker（rar/7z）的展开器，由 CLI 注入；返回 {"members": [...], "rejected": [...]}
ArchiveExpander = Callable[[Path, Path], dict]


@dataclass
class FoundFile:
    """发现阶段的最小记录（尚未成为 Item）。"""

    abs_path: Path
    package_relative_path: str
    source_id: str
    bytes: int
    mtime: str | None
    detected: magic.DetectedType
    origin: dict | None = None  # 容器成员
    content_bytes: bytes | None = None  # 小成员可直接持有字节（邮件附件回灌时）


@dataclass
class DiscoveryResult:
    root: Path
    files: list[FoundFile] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)  # [{source_id, paths}]
    expanded_archives: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)  # stage=discovery/expand/security
    empties: list[str] = field(default_factory=list)
    encrypted: list[str] = field(default_factory=list)
    corrupted: list[str] = field(default_factory=list)
    discovered_file_count: int = 0  # 含重复与容器成员的原始计数
    dedup_saved: int = 0
    all_items_accounted: bool = True


def _iso_mtime(ts: float | int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _zip_mtime(info: zipfile.ZipInfo) -> str | None:
    try:
        dt = datetime(*info.date_time, tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return None


class Discoverer:
    def __init__(
        self,
        root: Path,
        out_dir: Path,
        *,
        policy: dict | None = None,
        run_log: RunLog | None = None,
        kind_refiners: Iterable[KindRefiner] = (),
        archive_expanders: dict[str, ArchiveExpander] | None = None,
    ):
        self.root = root.resolve()
        self.out_dir = out_dir
        self.policy = policy or load_policy()
        self.guard = ArchiveGuard(self.policy)
        self.run_log = run_log or RunLog(out_dir, "discovery", enabled=False)
        self.kind_refiners = list(kind_refiners)
        self.archive_expanders = archive_expanders or {}
        self.result = DiscoveryResult(root=self.root)
        self._seen: dict[str, FoundFile] = {}  # source_id -> first FoundFile
        self._dup_paths: dict[str, list[str]] = {}

    # ---------------- 主入口 ----------------

    def run(self) -> DiscoveryResult:
        if self.root.is_file():
            if not _is_system_junk(self.root.name):
                self._register_file(self.root, self.root.name, origin=None, depth=0)
        else:
            for path in sorted(self.root.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    if path.is_symlink():
                        self._unresolved(path, "符号链接文件不入包（防逃逸）", stage="security")
                    continue
                if _is_system_junk(path.name):
                    continue
                # 跳过 ADG 输出目录内的文件（对父目录跑时防把输出当材料）
                if any(_is_adg_output_seg(part) for part in path.relative_to(self.root).parts):
                    continue
                if self._is_own_output(path):
                    continue
                rel = path.relative_to(self.root).as_posix()
                self._register_file(path, rel, origin=None, depth=0)
        self._finalize_duplicates()
        self._check_accounted()
        return self.result

    def _is_own_output(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.out_dir.resolve())
            return True
        except ValueError:
            return False

    # ---------------- 单文件登记 ----------------

    def _register_file(self, path: Path, rel: str, *, origin: dict | None, depth: int, content_bytes: bytes | None = None, mtime: str | None = None) -> None:
        self.result.discovered_file_count += 1
        size = len(content_bytes) if content_bytes is not None else path.stat().st_size
        if size == 0:
            self.result.empties.append(rel)
        source_id = source_id_of_bytes(content_bytes) if content_bytes is not None else source_id_of_file(path)
        head = content_bytes[:65536] if content_bytes is not None else self._read_head(path)
        # 容器成员：扩展名提示来自成员名（a.zip::dir/b.pdf → b.pdf）
        name_hint = Path(PurePosixName(rel)) if content_bytes is not None else path
        detected = magic.detect(name_hint, head=head)
        for refiner in self.kind_refiners:
            try:
                refined = refiner(path, detected, head)
            except Exception:
                refined = None
            if refined is not None:
                detected = refined
                break
        mt = mtime or (_iso_mtime(path.stat().st_mtime) if content_bytes is None else None)
        ff = FoundFile(
            abs_path=path,
            package_relative_path=rel,
            source_id=source_id,
            bytes=size,
            mtime=mt,
            detected=detected,
            origin=origin,
            content_bytes=content_bytes,
        )
        self.run_log.map_path(rel, path if content_bytes is None else f"<bytes:{size}>")
        # 去重
        if source_id in self._seen:
            self._dup_paths.setdefault(source_id, [self._seen[source_id].package_relative_path]).append(rel)
            return
        self._seen[source_id] = ff
        self.result.files.append(ff)
        # 压缩包递归
        if detected.kind == magic.ARCHIVE:
            self._expand_archive(ff, depth=depth)

    def _read_head(self, path: Path) -> bytes:
        try:
            with open(path, "rb") as f:
                return f.read(65536)
        except OSError:
            return b""

    # ---------------- 压缩包展开 ----------------

    def _expand_archive(self, ff: FoundFile, *, depth: int) -> None:
        fmt = ff.detected.format or ""
        rel = ff.package_relative_path
        record: dict[str, Any] = {
            "archive_source_id": ff.source_id,
            "package_relative_path": rel,
            "member_count": 0,
            "rejected_members": [],
        }
        self.result.expanded_archives.append(record)
        try:
            if fmt == "zip":
                self._expand_zip(ff, record, depth=depth)
            elif fmt == "tar":
                self._expand_tar(ff, record, depth=depth)
            elif fmt == "tar+gzip":
                self._expand_targz(ff, record, depth=depth)
            elif fmt == "gzip":
                self._expand_gzip(ff, record, depth=depth)
            elif fmt in ("rar", "7z"):
                self._expand_external(ff, record, depth=depth, fmt=fmt)
            else:
                self._unresolved_rel(rel, ff.source_id, f"未知压缩格式 {fmt!r}", stage="expand")
        except (SecurityViolation, zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, gzip.BadGzipFile) as e:
            self.result.corrupted.append(rel)
            self._unresolved_rel(rel, ff.source_id, f"压缩包损坏或不可读: {type(e).__name__}: {e}", stage="expand")
        record["member_count"] = sum(
            1 for f in self.result.files if f.origin and f.origin.get("container_source_id") == ff.source_id
        )

    def _member_rel(self, archive_rel: str, member_name: str) -> str:
        return f"{archive_rel}::{member_name.replace(chr(92), '/')}"

    def _ingest_member(self, ff: FoundFile, member_name: str, data: bytes | None, *, depth: int, mtime: str | None, stream=None) -> None:
        """成员 → 展开落盘 + 递归登记。data 与 stream 二选一（大成员走流式）。"""
        rel = self._member_rel(ff.package_relative_path, member_name)
        dest_dir = self.out_dir.joinpath(manifest_mod.INTERNAL_DIR, "expanded", "archives", ff.source_id.split(":", 1)[1][:16])
        try:
            dest = safe_join(dest_dir, member_name)
        except SecurityViolation as e:
            self._unresolved_rel(rel, None, str(e), stage="security")
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        if stream is not None:
            with open(dest, "wb") as out:
                shutil.copyfileobj(stream, out, length=1024 * 1024)
        else:
            dest.write_bytes(data or b"")
        origin = {
            "container_source_id": ff.source_id,
            "container_kind": "archive",
            "container_path": rel,
            "depth": depth + 1,
        }
        self._register_file(dest, rel, origin=origin, depth=depth + 1, content_bytes=None, mtime=mtime)

    def _expand_zip(self, ff: FoundFile, record: dict, *, depth: int) -> None:
        with zipfile.ZipFile(ff.abs_path) as zf:
            plan = self.guard.review_zip(zf, depth=depth + 1)
            for r in plan.rejected:
                record["rejected_members"].append({"name": r.name, "reason": r.reason})
            for name in plan.encrypted:
                record["rejected_members"].append({"name": name, "reason": "成员加密"})
                self.result.encrypted.append(self._member_rel(ff.package_relative_path, name))
            if plan.truncated:
                self._unresolved_rel(ff.package_relative_path, ff.source_id, f"压缩包截断: {plan.stop_reason}", stage="security")
            for verdict in plan.approved:
                info = zf.getinfo(verdict.name)
                try:
                    with zf.open(info) as fp:
                        self._ingest_member(ff, verdict.name, None, depth=depth, mtime=_zip_mtime(info), stream=fp)
                except (zipfile.BadZipFile, RuntimeError, OSError) as e:
                    record["rejected_members"].append({"name": verdict.name, "reason": f"读取失败: {e}"})
                    continue

    def _expand_tar(self, ff: FoundFile, record: dict, *, depth: int) -> None:
        with tarfile.open(ff.abs_path) as tf:
            self._expand_tar_members(tf, ff, record, depth=depth)

    def _expand_targz(self, ff: FoundFile, record: dict, *, depth: int) -> None:
        with gzip.open(ff.abs_path, "rb") as gz:
            data = io.BytesIO(gz.read())
        with tarfile.open(fileobj=data) as tf:
            self._expand_tar_members(tf, ff, record, depth=depth)

    def _expand_tar_members(self, tf: tarfile.TarFile, ff: FoundFile, record: dict, *, depth: int) -> None:
        plan = self.guard.review_tar(tf, depth=depth + 1)
        for r in plan.rejected:
            record["rejected_members"].append({"name": r.name, "reason": r.reason})
        if plan.truncated:
            self._unresolved_rel(ff.package_relative_path, ff.source_id, f"压缩包截断: {plan.stop_reason}", stage="security")
        for verdict in plan.approved:
            m = tf.getmember(verdict.name)
            fp = tf.extractfile(m)
            if fp is None:
                record["rejected_members"].append({"name": verdict.name, "reason": "无法读取"})
                continue
            mt = _iso_mtime(m.mtime) if m.mtime else None
            self._ingest_member(ff, verdict.name, None, depth=depth, mtime=mt, stream=fp)

    def _expand_gzip(self, ff: FoundFile, record: dict, *, depth: int) -> None:
        inner_name = Path(ff.package_relative_path).name
        if inner_name.endswith(".gz"):
            inner_name = inner_name[:-3]
        with gzip.open(ff.abs_path, "rb") as gz:
            data = gz.read(self.guard.max_single + 1)
        if len(data) > self.guard.max_single:
            self._unresolved_rel(ff.package_relative_path, ff.source_id, "gzip 解压超限", stage="security")
            return
        self._ingest_member(ff, inner_name, data, depth=depth, mtime=None)

    def _expand_external(self, ff: FoundFile, record: dict, *, depth: int, fmt: str) -> None:
        expander = self.archive_expanders.get(fmt)
        if expander is None:
            self._unresolved_rel(
                ff.package_relative_path,
                ff.source_id,
                f"{fmt} 需要外部 worker 展开",
                stage="expand",
                suggested_action=f"adg setup --worker py7zr（7z）或安装 unrar（rar）后重试",
            )
            return
        dest_dir = self.out_dir.joinpath(manifest_mod.INTERNAL_DIR, "expanded", "archives", ff.source_id.split(":", 1)[1][:16])
        dest_dir.mkdir(parents=True, exist_ok=True)
        outcome = expander(ff.abs_path, dest_dir)
        for rj in outcome.get("rejected", []):
            record["rejected_members"].append(rj)
        for member_rel_path in outcome.get("members", []):
            member_abs = dest_dir / member_rel_path
            rel = self._member_rel(ff.package_relative_path, member_rel_path)
            origin = {
                "container_source_id": ff.source_id,
                "container_kind": "archive",
                "container_path": rel,
                "depth": depth + 1,
            }
            self._register_file(member_abs, rel, origin=origin, depth=depth + 1)

    # ---------------- 邮件附件回灌（parse 阶段复用） ----------------

    def ingest_container_members(self, container: FoundFile | None, *, container_source_id: str, container_kind: str, container_rel: str, members: list[dict], depth: int) -> list[FoundFile]:
        """通用回灌口：邮件附件 / 嵌入对象 / transform 产物。

        members: [{"name": str, "data": bytes}]。返回新增的 FoundFile 列表。
        """
        added: list[FoundFile] = []
        dest_dir = self.out_dir.joinpath(manifest_mod.INTERNAL_DIR, "expanded", container_kind, container_source_id.split(":", 1)[1][:16])
        for m in members:
            name = m["name"]
            data = m["data"]
            reason = self.guard.check_name(name)
            if reason:
                self._unresolved_rel(f"{container_rel}::{name}", None, f"成员名拒绝: {reason}", stage="security")
                continue
            # 同名成员内容不同 → 加序号防覆盖（同内容走去重即可）
            if (dest_dir / name).exists():
                try:
                    existing = (dest_dir / name).read_bytes()
                except OSError:
                    existing = b""
                if existing != data:
                    stem, dot = name.rsplit(".", 1) if "." in name else (name, "")
                    suffix_n = 2
                    while (dest_dir / f"{stem}_{suffix_n}.{dot}").exists() if dot else (dest_dir / f"{stem}_{suffix_n}").exists():
                        suffix_n += 1
                    name = f"{stem}_{suffix_n}.{dot}" if dot else f"{stem}_{suffix_n}"
            try:
                dest = safe_join(dest_dir, name)
            except SecurityViolation as e:
                self._unresolved_rel(f"{container_rel}::{name}", None, str(e), stage="security")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            rel = f"{container_rel}::{name.replace(chr(92), '/')}"
            origin = {
                "container_source_id": container_source_id,
                "container_kind": container_kind,
                "container_path": rel,
                "depth": depth,
            }
            before = len(self.result.files)
            self._register_file(dest, rel, origin=origin, depth=depth)
            if len(self.result.files) > before:
                added.append(self.result.files[-1])
        return added

    # ---------------- 收尾 ----------------

    def _unresolved(self, path: Path, reason: str, *, stage: str) -> None:
        try:
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            rel = path.name
        self._unresolved_rel(rel, None, reason, stage=stage)

    def _unresolved_rel(self, rel: str, source_id: str | None, reason: str, *, stage: str, suggested_action: str | None = None) -> None:
        entry: dict[str, Any] = {
            "source_id": source_id or "",
            "package_relative_path": rel,
            "reason": reason,
            "stage": stage,
        }
        if suggested_action:
            entry["suggested_action"] = suggested_action
        self.result.unresolved.append(entry)

    def _finalize_duplicates(self) -> None:
        dup_groups = []
        saved = 0
        for sid, paths in self._dup_paths.items():
            dup_groups.append({"source_id": sid, "paths": sorted(paths)})
            saved += len(paths) - 1
        self.result.duplicates = sorted(dup_groups, key=lambda g: g["paths"][0])
        self.result.dedup_saved = saved

    def _check_accounted(self) -> None:
        counted = len(self.result.files) + sum(len(g["paths"]) - 1 for g in self.result.duplicates)
        self.result.all_items_accounted = counted == self.result.discovered_file_count


class PurePosixName(str):
    """从 'a.zip::dir/b.pdf' 取成员名作为扩展名提示的伪路径。"""

    @property
    def suffix(self) -> str:
        name = self.rsplit("::", 1)[-1]
        dot = name.rfind(".")
        return name[dot:] if dot >= 0 else ""

    @property
    def name(self) -> str:
        return self.rsplit("::", 1)[-1]


def discover_package(
    root: Path,
    out_dir: Path,
    *,
    policy: dict | None = None,
    run_log: RunLog | None = None,
    kind_refiners: Iterable[KindRefiner] = (),
    archive_expanders: dict[str, ArchiveExpander] | None = None,
) -> DiscoveryResult:
    return Discoverer(
        root,
        out_dir,
        policy=policy,
        run_log=run_log,
        kind_refiners=kind_refiners,
        archive_expanders=archive_expanders,
    ).run()


_SYSTEM_JUNK = {".ds_store", "thumbs.db", "desktop.ini"}


def _is_system_junk(name: str) -> bool:
    """系统/编辑器垃圾文件：不入材料清单。"""
    low = name.lower()
    if low in _SYSTEM_JUNK:
        return True
    if low.startswith("._"):  # macOS resource fork
        return True
    if low == "__macosx":
        return True
    return False


def _is_adg_output_seg(name: str) -> bool:
    """路径段是否为 ADG 输出目录（<包名>（ADG））。"""
    return name.endswith("（ADG）") or name.endswith("(ADG)")
