"""核心数据模型：EvidenceDocument / PackageManifest / Item。

dataclass 内存形态 + to_dict() 精确产出 models/*.v0.1.json 规定的外形。
finalize() 负责计算 rendition_id 与完整性哈希——所有身份计算集中在 hashing.py。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import SCHEMA_VERSION
from .hashing import package_id_of, payload_hash_of, rendition_id_of


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    return "uuid:" + str(uuid.uuid4())


# ---------- 元素与内容块 ----------


# ---------- EvidenceDocument ----------


@dataclass
class EvidenceDocument:
    source_id: str
    run_id: str
    source: dict  # package_relative_path / detected_type / bytes / mtime / origin / derived_from
    parser: dict  # name / version / worker_id / fell_back_from
    provenance: dict  # coordinate_system
    content: dict  # {"blocks": [...]}
    annotations: dict
    quality: dict  # overall + 8 dims + fitness (+notes)
    lineage: list[dict]
    rendition_id: str = ""
    integrity: dict = field(default_factory=dict)

    def to_dict(self, *, with_payload_hash: bool = True) -> dict:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "source_id": self.source_id,
            "rendition_id": self.rendition_id,
            "run_id": self.run_id,
            "source": self.source,
            "parser": self.parser,
            "provenance": self.provenance,
            "content": self.content,
            "annotations": self.annotations,
            "quality": self.quality,
            "lineage": self.lineage,
            "integrity": dict(self.integrity),
        }
        if with_payload_hash:
            doc["integrity"]["payload_sha256"] = payload_hash_of(doc)
        return doc

    def finalize_identity(self) -> None:
        """内容定稿后调用：计算 rendition_id 与 source 冗余校验位。"""
        self.rendition_id = rendition_id_of(self.content, self.annotations, self.provenance.get("coordinate_system", {}))
        self.integrity = {
            "payload_sha256": "",  # to_dict 时填充
            "source_sha256": self.source_id.split(":", 1)[1],
        }


def empty_annotations() -> dict:
    return {
        "comments": [],
        "revisions": [],
        "ocr_boxes": [],
        "seals": [],
        "embedded_objects": [],
        "alternative_renditions": [],
    }


# ---------- Manifest Item ----------


@dataclass
class Item:
    source_id: str
    package_relative_path: str
    kind: str
    format: str | None = None
    bytes: int | None = None
    origin: dict | None = None  # 容器成员来源
    derived_from: dict | None = None  # 转换来源
    renditions: list[str] = field(default_factory=list)
    status: str = "discovered"  # discovered/parsed/parsed_with_warnings/failed/unresolved/unsupported/duplicate
    best_quality: str | None = None
    errors: list[str] | None = None
    detected_type: dict | None = None  # scan 阶段识别结果，供 parse 路由复用
    signals: dict | None = None  # scan 阶段信号探测结果（spec 11），利用率的分母
    mtime: str | None = None

    def to_manifest_entry(self) -> dict:
        entry: dict[str, Any] = {
            "source_id": self.source_id,
            "package_relative_path": self.package_relative_path,
            "kind": self.kind,
            "renditions": list(self.renditions),
            "status": self.status,
        }
        entry["format"] = self.format
        if self.bytes is not None:
            entry["bytes"] = self.bytes
        entry["origin"] = self.origin
        entry["derived_from"] = self.derived_from
        entry["best_quality"] = self.best_quality
        entry["errors"] = self.errors
        if self.detected_type is not None:
            entry["detected_type"] = self.detected_type
        if self.signals is not None:
            entry["signals"] = self.signals
        if self.mtime is not None:
            entry["mtime"] = self.mtime
        return entry

    @classmethod
    def from_manifest_entry(cls, entry: dict) -> "Item":
        return cls(
            source_id=entry["source_id"],
            package_relative_path=entry["package_relative_path"],
            kind=entry["kind"],
            format=entry.get("format"),
            bytes=entry.get("bytes"),
            origin=entry.get("origin"),
            derived_from=entry.get("derived_from"),
            renditions=list(entry.get("renditions", [])),
            status=entry.get("status", "discovered"),
            best_quality=entry.get("best_quality"),
            errors=entry.get("errors"),
            detected_type=entry.get("detected_type"),
            signals=entry.get("signals"),
            mtime=entry.get("mtime"),
        )


# ---------- PackageManifest ----------


@dataclass
class PackageManifest:
    package_root_name: str
    items: list[Item] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    stats: dict = field(default_factory=dict)
    integrity: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    package_id: str = ""
    _loaded_payload_sha256: str | None = field(default=None, repr=False, compare=False)

    def compute_package_id(self) -> None:
        self.package_id = package_id_of([it.source_id for it in self.items])

    def refresh_stats(self) -> None:
        by_kind: dict[str, int] = {}
        total_bytes = 0
        for it in self.items:
            by_kind[it.kind] = by_kind.get(it.kind, 0) + 1
            total_bytes += it.bytes or 0
        self.stats = {
            "total_items": len(self.items),
            "by_kind": dict(sorted(by_kind.items())),
            "total_bytes": total_bytes,
            "dedup_saved": self.stats.get("dedup_saved", 0),
        }

    def ensure_integrity_shape(self) -> None:
        self.integrity.setdefault("unresolved_items", [])
        self.integrity.setdefault("expanded_archives", [])
        self.integrity.setdefault("expanded_emails", [])
        self.integrity.setdefault("all_items_accounted", True)
        self.integrity.setdefault("payload_sha256", "")

    def ensure_quality_shape(self) -> None:
        self.quality.setdefault("overall", "pass")
        self.quality.setdefault("extraction_coverage", 0.0)
        for key in ("duplicates", "empties", "encrypted", "corrupted", "unsupported", "low_confidence"):
            self.quality.setdefault(key, [])

    def to_dict(self, *, with_payload_hash: bool = True) -> dict:
        self.ensure_integrity_shape()
        self.ensure_quality_shape()
        doc = {
            "schema_version": SCHEMA_VERSION,
            "package_id": self.package_id,
            "package_root_name": self.package_root_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stats": self.stats,
            "integrity": dict(self.integrity),
            "quality": self.quality,
            "items": [it.to_manifest_entry() for it in self.items],
        }
        if with_payload_hash:
            doc["integrity"]["payload_sha256"] = payload_hash_of(doc)
        return doc

    @classmethod
    def from_dict(cls, doc: dict) -> "PackageManifest":
        m = cls(package_root_name=doc.get("package_root_name", ""))
        m.package_id = doc.get("package_id", "")
        m.created_at = doc.get("created_at", now_iso())
        m.updated_at = doc.get("updated_at", now_iso())
        m.stats = doc.get("stats", {})
        m.integrity = doc.get("integrity", {})
        m.quality = doc.get("quality", {})
        m.items = [Item.from_manifest_entry(e) for e in doc.get("items", [])]
        m.ensure_integrity_shape()
        m.ensure_quality_shape()
        return m
