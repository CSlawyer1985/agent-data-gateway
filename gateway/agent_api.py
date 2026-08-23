"""Agent 只读接口：六个消费操作的唯一实现，`adg` 各查询子命令共用（spec 10）。

纪律：

- **只读**：本模块永不修改 manifest/rendition，唯一的写动作是按需重建检索索引
  （索引是可丢弃的派生物，见 `gateway.index`）。
- **单一权威**：status / search / anchor / get 不各写一份查询逻辑——避免同一问题出现两种答案。
- **隐私**：返回值只含包内相对路径（`package_relative_path` 与输出目录内相对产物名），
  绝不外泄绝对路径。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ADGError, ConcurrentWriteError
from .index import (
    IndexStats,
    build_index,
    index_meta,
    index_path,
    open_index,
    resolve_element,
    search,
)
from .manifest import default_out_dir, list_rendition_docs, load_manifest, mirror_relpath
from .models import Item, PackageManifest
from .quality import best_quality_rank, reliably_read_ids

# Agent 消费的查询契约版本。独立于 adg 发布版本——发版不改契约就不动它，
# 改了字段语义才递增，让下游有可判定的兼容依据（spec 10）。
AGENT_API_VERSION = "1"

# extraction_coverage 极易被读成「信息提取了多少」。它是文件级的，与文件内的
# 利用率（spec 11）正交；两个百分比并列时 Agent 必须分得开（ADR 0003 决策 7）。
COVERAGE_MEANING = (
    "文件级完整性：多少份材料被可靠读取。不是文件内信息的利用率——"
    "一份扫描件的文字层读到了，图里的印章与手写签名仍可能没提取。"
)

_HEX_RE = re.compile(r"^[0-9a-f]{8,64}$")

MAX_ELEMENTS_DEFAULT = 200


@dataclass
class PackageView:
    """绑定到一个材料包输出目录的只读视图。"""

    package_path: Path
    out_dir: Path
    manifest: PackageManifest
    _conn: sqlite3.Connection | None = None
    _index_stats: IndexStats | None = None
    _docs_cache: dict[str, list[dict]] = field(default_factory=dict)
    _dup_paths: dict[str, list[str]] | None = None
    _reliable_cache: set[str] | None = None
    _index_stale: bool = False

    # ---------------- 生命周期 ----------------

    @classmethod
    def open(cls, package_path: Path | str, out_dir: Path | str | None = None) -> "PackageView":
        package_path = Path(package_path).resolve()
        out = Path(out_dir).resolve() if out_dir else default_out_dir(package_path)
        manifest = load_manifest(out)
        if manifest is None:
            raise ADGError(f"未找到 manifest（{out.name}）——请先 adg scan / adg parse")
        return cls(package_path=package_path, out_dir=out, manifest=manifest)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "PackageView":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def index(self, *, force: bool = False) -> sqlite3.Connection:
        """按需构建并只读打开索引。

        索引需要重建、但输出目录正被 parse 之类的写入者占用时：若已有一份可读索引，
        就用它并把 `stale=True` 显性带进结果——查询照常可用，但调用方知道它可能
        缺最新解析产物。既有索引都没有时才失败，因为那时无话可说。
        """
        if self._conn is not None and not force:
            return self._conn
        self.close()
        self._index_stale = False
        try:
            self._index_stats = build_index(self.out_dir, force=force, manifest=self.manifest)
        except ConcurrentWriteError:
            if not index_path(self.out_dir).is_file():
                raise
            self._index_stats = None
            self._index_stale = True
        self._conn = open_index(self.out_dir)
        return self._conn

    @property
    def index_stats(self) -> IndexStats | None:
        return self._index_stats

    # ---------------- 1. list_package_items ----------------

    def list_package_items(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        unreliable_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        rows: list[dict] = []
        for item in self.manifest.items:
            if kind and item.kind != kind:
                continue
            if status and item.status != status:
                continue
            reliable = self._reliable(item)
            if unreliable_only and reliable:
                continue
            rows.append(
                {
                    "source_id": item.source_id,
                    "package_relative_path": item.package_relative_path,
                    "kind": item.kind,
                    "format": item.format,
                    "bytes": item.bytes,
                    "status": item.status,
                    "best_quality": item.best_quality,
                    "renditions": list(item.renditions),
                    "reliably_read": reliable,
                    "errors": item.errors,
                    "origin": item.origin,
                    "derived_from": item.derived_from,
                    "duplicate_paths": self.duplicate_paths(item.source_id),
                }
            )
        offset = max(0, int(offset))
        total = len(rows)
        window = rows[offset : offset + max(1, int(limit))]
        return {
            "contract_version": AGENT_API_VERSION,
            "package_root_name": self.manifest.package_root_name,
            "package_id": self.manifest.package_id,
            "total": total,
            "returned": len(window),
            "offset": offset,
            "items": window,
        }

    # ---------------- 2. get_ingestion_status ----------------

    def get_ingestion_status(self) -> dict:
        reliable: list[dict] = []
        unreliable: list[dict] = []
        reasons = {
            u["source_id"]: u.get("reason") for u in self.manifest.integrity.get("unresolved_items", [])
        }
        by_status: dict[str, int] = {}
        for item in self.manifest.items:
            by_status[item.status] = by_status.get(item.status, 0) + 1
            if item.status == "unsupported":
                continue
            entry = {
                "package_relative_path": item.package_relative_path,
                "kind": item.kind,
                "status": item.status,
            }
            if self._reliable(item):
                reliable.append(entry)
            else:
                entry["reason"] = (
                    reasons.get(item.source_id) or (item.errors or [None])[0] or "尚无可靠 rendition"
                )
                unreliable.append(entry)
        quality = self.manifest.quality
        return {
            "contract_version": AGENT_API_VERSION,
            "package_root_name": self.manifest.package_root_name,
            "package_id": self.manifest.package_id,
            "overall": quality.get("overall"),
            "extraction_coverage": quality.get("extraction_coverage"),
            "extraction_coverage_meaning": COVERAGE_MEANING,
            "all_items_accounted": self.manifest.integrity.get("all_items_accounted", False),
            "total_items": len(self.manifest.items),
            "by_status": dict(sorted(by_status.items())),
            "reliably_read_count": len(reliable),
            "not_reliable_count": len(unreliable),
            "reliably_read": reliable,
            "not_reliably_read": unreliable,
            "unresolved_count": len(self.manifest.integrity.get("unresolved_items", [])),
        }

    # ---------------- 3. get_document ----------------

    def get_document(
        self,
        ref: str,
        *,
        rendition_id: str | None = None,
        include_elements: bool = True,
        max_elements: int = MAX_ELEMENTS_DEFAULT,
        offset: int = 0,
    ) -> dict:
        offset = max(0, int(offset))
        item = self.find_item(ref)
        docs = [d for d in self._docs(item.source_id) if d["rendition_id"] in item.renditions]
        summaries = [self._rendition_summary(d) for d in docs]
        selected = None
        if rendition_id:
            selected = next((d for d in docs if d["rendition_id"] == rendition_id), None)
            if selected is None:
                raise ADGError(f"rendition 不存在于该文件: {rendition_id}")
        elif docs:
            selected = max(docs, key=lambda d: self._rendition_rank(d))

        result: dict[str, Any] = {
            "contract_version": AGENT_API_VERSION,
            "source_id": item.source_id,
            "package_relative_path": item.package_relative_path,
            "kind": item.kind,
            "format": item.format,
            "bytes": item.bytes,
            "status": item.status,
            "best_quality": item.best_quality,
            "reliably_read": self._reliable(item),
            "errors": item.errors,
            "origin": item.origin,
            "derived_from": item.derived_from,
            "duplicate_paths": self.duplicate_paths(item.source_id),
            "renditions": summaries,
            "rendering_relpath": mirror_relpath(item.package_relative_path) + ".md",
        }
        if selected is None:
            result["selected_rendition"] = None
            result["elements"] = []
            result["elements_total"] = 0
            result["note"] = "该文件尚无解析产物（见 status/errors）"
            return result

        result["selected_rendition"] = self._rendition_summary(selected)
        result["source"] = selected.get("source", {})
        result["lineage"] = selected.get("lineage", [])
        result["annotations_counts"] = {
            k: len(v) for k, v in (selected.get("annotations") or {}).items() if isinstance(v, list)
        }
        elements = list(self._flatten(selected))
        result["elements_total"] = len(elements)
        if include_elements:
            window = elements[offset : offset + max(1, int(max_elements))]
            result["elements"] = window
            result["elements_offset"] = offset
            result["elements_truncated"] = offset + len(window) < len(elements)
        else:
            result["elements"] = []
            result["elements_truncated"] = len(elements) > 0
        return result

    # ---------------- 4. search_elements ----------------

    def search_elements(
        self,
        query: str,
        *,
        kind: str | None = None,
        limit: int = 20,
        quotable_only: bool = False,
        all_renditions: bool = False,
        path_prefix: str | None = None,
    ) -> dict:
        conn = self.index()
        result = search(
            conn,
            query,
            kind=kind,
            limit=limit,
            quotable_only=quotable_only,
            all_renditions=all_renditions,
            path_prefix=path_prefix,
        )
        result["contract_version"] = AGENT_API_VERSION
        result["package_root_name"] = self.manifest.package_root_name
        result["index"] = self.index_summary()
        result["coverage_caveat"] = self._coverage_caveat()
        if self._index_stale:
            result["stale_index_caveat"] = "输出目录正被其他任务写入，本次检索基于上一版索引，可能缺最新解析产物"
        return result

    # ---------------- 5. resolve_anchor ----------------

    def resolve_anchor(self, element_id: str, *, rendition_id: str | None = None) -> dict:
        conn = self.index()
        matches = resolve_element(conn, element_id, rendition_id)
        return {
            "contract_version": AGENT_API_VERSION,
            "element_id": element_id,
            "found": len(matches),
            "matches": matches,
            "index": self.index_summary(),
        }

    # ---------------- 6. list_unresolved_items ----------------

    def list_unresolved_items(self) -> dict:
        entries: list[dict] = []
        seen: set[tuple[str, str]] = set()
        item_by_sid = {it.source_id: it for it in self.manifest.items}
        for u in self.manifest.integrity.get("unresolved_items", []):
            key = (u.get("source_id", ""), u.get("stage") or "")
            seen.add(key)
            item = item_by_sid.get(u.get("source_id", ""))
            entries.append(
                {
                    "package_relative_path": u.get("package_relative_path"),
                    "source_id": u.get("source_id"),
                    "kind": item.kind if item else None,
                    "stage": u.get("stage"),
                    "reason": u.get("reason"),
                    "suggested_action": u.get("suggested_action"),
                    "status": item.status if item else None,
                }
            )
        for item in self.manifest.items:
            if item.status not in ("failed", "unresolved"):
                continue
            if any(e["source_id"] == item.source_id for e in entries):
                continue
            entries.append(
                {
                    "package_relative_path": item.package_relative_path,
                    "source_id": item.source_id,
                    "kind": item.kind,
                    "stage": "parse",
                    "reason": (item.errors or ["解析失败"])[0],
                    "suggested_action": None,
                    "status": item.status,
                }
            )
        return {
            "contract_version": AGENT_API_VERSION,
            "package_root_name": self.manifest.package_root_name,
            "total": len(entries),
            "all_items_accounted": self.manifest.integrity.get("all_items_accounted", False),
            "unresolved_items": entries,
        }

    # ---------------- 内部工具 ----------------

    def index_summary(self) -> dict:
        if self._conn is None:
            return {"available": False}
        meta = index_meta(self._conn)
        return {
            "available": True,
            "stale": self._index_stale,
            "engine": meta.get("engine"),
            "elements": int(meta.get("elements", 0)),
            "renditions": int(meta.get("renditions", 0)),
            "index_schema_version": meta.get("index_schema_version"),
            "rebuilt": bool(self._index_stats.rebuilt) if self._index_stats else None,
        }

    def _coverage_caveat(self) -> str | None:
        """检索只覆盖已可靠读取的部分——未读全的材料必须在结果里显性提示。"""
        status = sum(1 for it in self.manifest.items if it.status in ("failed", "unresolved"))
        if status:
            return f"{status} 项材料未被可靠读取，其内容不在检索范围内（adg status 查看）"
        return None

    def duplicate_paths(self, source_id: str) -> list[str] | None:
        """同一字节在包内出现的全部路径。去重只保留一个 item，另几份路径不能就此消失——
        「这份合同在证据三和证据五各有一份」对律师是案卷事实。"""
        if self._dup_paths is None:
            self._dup_paths = {
                group["source_id"]: list(group.get("paths", []))
                for group in self.manifest.quality.get("duplicates", [])
                if len(group.get("paths", [])) > 1
            }
        return self._dup_paths.get(source_id)

    def _docs(self, source_id: str) -> list[dict]:
        """同一视图内缓存 rendition 读取（每次读取都做完整性校验，缓存避免重复付费）。"""
        cached = self._docs_cache.get(source_id)
        if cached is None:
            cached = list_rendition_docs(self.out_dir, source_id)
            self._docs_cache[source_id] = cached
        return cached

    def _reliable(self, item: Item) -> bool:
        return item.source_id in self._reliable_ids()

    def _reliable_ids(self) -> set[str]:
        """包级判定并缓存：派生物读到即算原件读到（见 quality.reliably_read_ids）。"""
        if self._reliable_cache is None:
            self._reliable_cache = reliably_read_ids(
                self.manifest.items,
                lambda sid: [d.get("quality", {}) for d in self._docs(sid)],
            )
        return self._reliable_cache

    def _rendition_rank(self, doc: dict) -> tuple:
        elements = sum(len(b.get("elements", [])) for b in doc.get("content", {}).get("blocks", []))
        return (best_quality_rank(doc.get("quality", {}).get("overall")), elements, doc["rendition_id"])

    @staticmethod
    def _rendition_summary(doc: dict) -> dict:
        quality = doc.get("quality", {})
        parser = doc.get("parser", {})
        return {
            "rendition_id": doc["rendition_id"],
            "run_id": doc.get("run_id"),
            "parser": parser.get("name"),
            "parser_version": parser.get("version"),
            "fell_back_from": parser.get("fell_back_from"),
            "quality": {k: v for k, v in quality.items() if k != "fitness"},
            "fitness": quality.get("fitness", {}),
            "coordinate_system": (doc.get("provenance", {}) or {}).get("coordinate_system"),
            "element_count": sum(
                len(b.get("elements", [])) for b in doc.get("content", {}).get("blocks", [])
            ),
        }

    @staticmethod
    def _flatten(doc: dict):
        for block in doc.get("content", {}).get("blocks", []):
            for el in block.get("elements", []):
                yield {
                    "element_id": el.get("id"),
                    "type": el.get("type"),
                    "block_type": block.get("block_type"),
                    "block_index": block.get("index"),
                    "locator": el.get("locator", {}),
                    "text": el.get("text"),
                    "attrs": el.get("attrs"),
                }

    def find_item(self, ref: str) -> Item:
        """按 source_id 或包内相对路径定位 item；歧义显性报错而不是猜。"""
        ref = (ref or "").strip()
        if not ref:
            raise ADGError("需要 source_id 或包内相对路径")
        if ref.startswith("sha256:"):
            for item in self.manifest.items:
                if item.source_id == ref:
                    return item
            raise ADGError(f"包内无此 source_id: {ref}")
        if _HEX_RE.match(ref):
            hits = [it for it in self.manifest.items if it.source_id.split(":", 1)[1].startswith(ref)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise ADGError(f"source_id 前缀有歧义（{len(hits)} 个匹配），请给更长前缀")
        norm = ref.replace("\\", "/")
        for item in self.manifest.items:
            if item.package_relative_path == norm:
                return item
        candidates = [
            it
            for it in self.manifest.items
            if it.package_relative_path.endswith("/" + norm) or mirror_relpath(it.package_relative_path) == norm
        ]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            candidates = [it for it in self.manifest.items if norm in it.package_relative_path]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            shown = "、".join(c.package_relative_path for c in candidates[:5])
            raise ADGError(f"路径有歧义（{len(candidates)} 个匹配）：{shown}")
        raise ADGError(f"包内无此文件: {ref}")


def open_package(package_path: Path | str, out_dir: Path | str | None = None) -> PackageView:
    return PackageView.open(package_path, out_dir)
