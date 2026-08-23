"""解析调度：manifest → 特征路由 → worker 执行 → EvidenceDocument 定稿 → 回写。

编排器独有职责（worker 不得染指）：
- 原件哈希复核（scan→parse 期间文件被改动则拒绝解析）
- 元素锚点 ID 定稿与回溯校验
- 质量定稿（parser 建议值 + 聚合规则）
- 降级链记录（fell_back_from）
- 附件/转换产物递归回灌
- 跨 rendition 对齐（L3）
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import anchor, inventory, magic, manifest as manifest_mod, quality as quality_mod, routing
from .errors import WorkerUnavailable
from .hardware import Environment, detect_environment
from .hashing import payload_hash_of, sha256_file
from .lineage import make_entry, now_iso_ms
from .models import EvidenceDocument, Item, PackageManifest, empty_annotations, new_run_id
from .privacy import RunLog, scrub_text
from .render import render_document_md
from .security import load_policy, resolve_worker_output
from .workers import WorkerInfo, WorkerRegistry, execute_worker, make_work_dir

_KIND_DIR = {"archive": "archives", "email": "email", "office-embed": "office-embed", "transform": "transform"}

# 容器类 kind：解析即"已在 scan 展开"，无内容 rendition
_CONTAINER_KINDS = {"archive"}
_SKIP_KINDS = {"executable", "unknown"}


@dataclass
class ParseOptions:
    only_kind: str | None = None
    resume: bool = False
    force: bool = False
    no_abs_log: bool = False
    keep_work: bool = False
    worker_options: dict = field(default_factory=dict)
    chain_override: dict[str, list[str]] = field(default_factory=dict)  # kind → 强制链


@dataclass
class ParseSummary:
    run_id: str = ""
    parsed: int = 0
    failed: int = 0
    unresolved: int = 0
    skipped: int = 0
    new_items: int = 0
    alignments: int = 0
    exit_overall: str = "pass"


def resolve_source_path(item: Item, package_root: Path, out_dir: Path) -> Path:
    """由包内相对路径还原原件绝对位置（顶层）或展开位置（容器成员）。"""
    if item.origin:
        kind_dir = _KIND_DIR.get(item.origin.get("container_kind", ""), "archives")
        member = item.package_relative_path.rsplit("::", 1)[-1]
        sid16 = item.origin["container_source_id"].split(":", 1)[1][:16]
        return manifest_mod.internal_dir(out_dir, "expanded", kind_dir, sid16, member)
    # 单文件包：package_root 即文件本身（scan 单文件时 root=文件），包内相对路径是文件名，
    # 须以父目录为根还原，否则路径翻倍导致误报"原件缺失"。
    root = package_root.parent if package_root.is_file() else package_root
    return root / item.package_relative_path


class Dispatcher:
    def __init__(
        self,
        package_root: Path,
        out_dir: Path,
        manifest: PackageManifest,
        registry: WorkerRegistry,
        *,
        env: Environment | None = None,
        policy: dict | None = None,
        options: ParseOptions | None = None,
    ):
        self.package_root = package_root
        self.out_dir = out_dir
        self.manifest = manifest
        self.registry = registry
        self.env = env or detect_environment()
        self.policy = policy or load_policy()
        self.options = options or ParseOptions()
        self.run_id = new_run_id()
        self.run_log = RunLog(out_dir, self.run_id, enabled=not self.options.no_abs_log)
        self.summary = ParseSummary(run_id=self.run_id)
        self._quality_cache: dict[str, list[dict]] = {}  # source_id -> rendition qualities

    # ---------------- 主流程 ----------------

    def run(self) -> ParseSummary:
        with manifest_mod.OutputWriteLock(self.out_dir):
            try:
                return self._run_locked()
            finally:
                self.run_log.close()

    def _run_locked(self) -> ParseSummary:
        adopted = manifest_mod.reconcile_renditions(self.manifest, self.out_dir)
        if adopted:
            self.run_log.event(f"recovered {adopted} orphan renditions")
        # 先把历史 rendition 质量全部载入缓存（部分解析时覆盖率不低报）
        for it in self.manifest.items:
            if it.renditions:
                self._cache_item_qualities(it)
        queue = self._initial_queue()
        i = 0
        while i < len(queue):
            item = queue[i]
            i += 1
            try:
                produced = self._parse_item(item)
            except Exception as e:  # 单 item 异常不得击穿整包
                self._mark_unresolved(item, f"编排器内部错误: {type(e).__name__}: {e}", stage="parse")
                produced = []
            queue.extend(produced)
            self.summary.new_items += len(produced)
        self._post_alignments(queue)
        # 基于磁盘最新 rendition 算覆盖（force 重跑时内存 cache 可能过时）
        docs_cache = {
            it.source_id: [d.get("quality", {}) for d in manifest_mod.list_rendition_docs(self.out_dir, it.source_id)]
            for it in self.manifest.items
        }
        # 包级判定：transform 型原件（扫描 PDF→页位图、DWG→DXF、视频→抽帧）自己没有内容
        # rendition，内容在派生物里——只看自身 rendition 会把"已读到"误报成"没读到"。
        reliably_ids = quality_mod.reliably_read_ids(
            self.manifest.items, lambda sid: docs_cache.get(sid, [])
        )
        inventory.sync_item_status(self.manifest, reliably_read_ids=reliably_ids)
        inventory.recompute_package_quality(self.manifest, reliably_read_ids=reliably_ids)
        manifest_mod.save_manifest(self.manifest, self.out_dir)
        md = render_manifest_md_safe(self.manifest)
        manifest_mod.atomic_write_text(self.out_dir / manifest_mod.MANIFEST_MD, md)
        self._write_renderings()
        self.run_log.close()
        self.summary.exit_overall = self.manifest.quality.get("overall", "pass")
        return self.summary

    def _initial_queue(self) -> list[Item]:
        out: list[Item] = []
        for it in self.manifest.items:
            if self.options.only_kind and it.kind != self.options.only_kind:
                continue
            if it.status == "unsupported" or it.kind in _SKIP_KINDS:
                continue
            if it.kind in _CONTAINER_KINDS:
                self._finalize_container_item(it)
                continue
            if self.options.resume and not self.options.force:
                if it.best_quality in ("pass", "pass_with_warnings") and it.renditions:
                    self.summary.skipped += 1
                    self._cache_item_qualities(it)
                    continue
            out.append(it)
        return out

    def _finalize_container_item(self, it: Item) -> None:
        # 损坏/加密/拦截的压缩包 → unresolved
        corrupted = set(self.manifest.quality.get("corrupted", []))
        unresolved_sids = {u.get("source_id") for u in self.manifest.integrity.get("unresolved_items", [])}
        if it.package_relative_path in corrupted or it.source_id in unresolved_sids:
            it.status = "unresolved"
            it.errors = (it.errors or []) + ["压缩包损坏或不可读（见 unresolved_items）"]
            self.summary.skipped += 1
            return
        rec = next(
            (r for r in self.manifest.integrity.get("expanded_archives", []) if r.get("archive_source_id") == it.source_id),
            None,
        )
        if rec is None:
            it.status = "unresolved"
            it.errors = (it.errors or []) + ["压缩包未展开（见 unresolved_items）"]
        elif rec.get("rejected_members"):
            it.status = "parsed_with_warnings"
        else:
            it.status = "parsed"
        self.summary.skipped += 1

    # ---------------- 单 item 解析 ----------------

    def _parse_item(self, item: Item) -> list[Item]:
        path = resolve_source_path(item, self.package_root, self.out_dir)
        if not path.exists():
            self._mark_unresolved(item, "原件缺失（scan 后被移动或删除）", stage="parse")
            return []
        if item.bytes is not None and item.bytes > 0:
            actual = "sha256:" + sha256_file(path)
            if actual != item.source_id:
                self._mark_unresolved(item, "原件哈希与 scan 时不一致（文件已被修改），请重新 adg scan", stage="parse")
                return []
        detected = self._detected_of(item)
        features = routing.preflight(item.kind, path, detected)
        if item.kind in self.options.chain_override:
            chain = list(self.options.chain_override[item.kind])
            route_reason = f"强制链（chain_override）: {' → '.join(chain)}"
        else:
            chain, route_reason = routing.resolve_chain(item.kind, item.format, features)
        if not chain:
            self._mark_unresolved(item, f"该类型（{item.kind}/{item.format or '-'}）无解析链（{route_reason}）", stage="parse")
            return []
        fell_back: list[dict] = []
        all_produced: list[Item] = []
        transform_done = False
        for worker_name in chain:
            w = self.registry.get(worker_name)
            if w is None:
                detail = f"worker {worker_name} 未注册"
                fell_back.append({"name": worker_name, "version": "-", "reason": "unavailable", "detail": detail})
                continue
            ok, reason = self.registry.availability(w, self.env)
            if not ok:
                fell_back.append({"name": w.name, "version": w.version, "reason": "unavailable", "detail": reason})
                continue
            request = {
                "protocol_version": "0.1",
                "type": "parse_request",
                "job_id": f"{item.source_id.split(':', 1)[1][:12]}-{int(time.time() * 1000) % 10_000_000}",
                "source_path": str(path),
                "source_id": item.source_id,
                "kind": item.kind,
                "format": item.format,
                "options": {**self.options.worker_options, "features": features},
                "limits": self.policy.get("parser_limits", {}),
                "work_dir": "",
            }
            work_dir = make_work_dir(self.out_dir, request["job_id"])
            request["work_dir"] = str(work_dir)
            started = now_iso_ms()
            try:
                result = execute_worker(w, request, source_path=path, work_dir=work_dir, policy=self.policy)
            except WorkerUnavailable as e:
                detail = str(e)
                fell_back.append({"name": w.name, "version": w.version, "reason": "unavailable", "detail": detail})
                self._cleanup_work(work_dir)
                continue
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                fell_back.append({"name": w.name, "version": w.version, "reason": "error", "detail": detail})
                self._cleanup_work(work_dir)
                continue
            if result.get("status") != "ok":
                fb = routing.fallback_reason_from_result(result, available=True)
                detail = fb.get("detail") or "worker 执行失败"
                fell_back.append({"name": w.name, "version": w.version, "reason": fb["reason"], "detail": detail})
                self._cleanup_work(work_dir)
                continue
            # transform worker（dwg→dxf、dwg→渲染图、视频→抽帧 等）：产 output_files 不产 fragment
            # 成功后回灌产物并继续链（一个原件可经多个 transform：如 dwg 同时转 dxf + 渲染图）
            if (
                not isinstance(result.get("fragment"), dict)
                and not result.get("fragment_path")
                and result.get("output_files")
            ):
                produced = self._after_transform(item, result, work_dir, w, route_reason, started)
                all_produced.extend(produced)
                transform_done = True
                self._cleanup_work(work_dir)
                continue
            fragment = result.get("fragment")
            if fragment is None and result.get("fragment_path"):
                import json as _json

                fragment_path = resolve_worker_output(work_dir, result["fragment_path"])
                with open(fragment_path, encoding="utf-8") as f:
                    fragment = _json.load(f)
            if not isinstance(fragment, dict):
                detail = "worker 返回空片段"
                fell_back.append({"name": w.name, "version": w.version, "reason": "error", "detail": detail})
                self._cleanup_work(work_dir)
                continue
            try:
                doc = self._finalize_document(item, w, fragment, fell_back, features, route_reason, started)
            except Exception as e:
                detail = f"片段定稿失败: {type(e).__name__}: {e}"
                fell_back.append({"name": w.name, "version": w.version, "reason": "error", "detail": detail})
                self._cleanup_work(work_dir)
                continue
            produced = self._after_success(item, doc, fragment, work_dir, w)
            all_produced.extend(produced)
            self._cleanup_work(work_dir)
            return all_produced  # parse 成功 → 内容 rendition 已得，结束
        # 链尽：若有 transform 成功（如 dwg 转出了 dxf/渲染图），原件标 parsed，派生物已入队
        if transform_done:
            return all_produced
        # 全部候选失败：按链顺序输出每一级的失败原因，避免只留链尾误导排障。
        # detail 可能含 worker 异常消息里的绝对路径，落盘前 scrub（隐私：产物只出现包内相对路径）。
        chain_desc = " → ".join(
            f"{fb['name']}: {scrub_text((fb.get('detail') or fb['reason']))[:160]}"
            for fb in fell_back
        )
        reason_text = f"所有候选 parser 失败/不可用（{route_reason}）。失败链: {chain_desc}"
        if any(fb["reason"] == "unavailable" for fb in fell_back):
            reason_text += "。建议: adg setup --worker " + next((fb["name"] for fb in fell_back if fb["reason"] == "unavailable"), chain[0])
        self._mark_unresolved(item, reason_text, stage="parse", fell_back=fell_back)
        return all_produced

    def _detected_of(self, item: Item) -> Any:
        dt = item.detected_type or {}
        return magic.DetectedType(
            mime=dt.get("mime", "application/octet-stream"),
            magic=dt.get("magic", ""),
            method=dt.get("method", "adg-magic"),
            confidence=dt.get("confidence", "low"),
            kind=item.kind,
            format=item.format,
        )

    # ---------------- 文档定稿 ----------------

    def _finalize_document(
        self,
        item: Item,
        w: WorkerInfo,
        fragment: dict,
        fell_back: list[dict],
        features: dict,
        route_reason: str,
        started: str,
    ) -> EvidenceDocument:
        kind = item.kind
        config_hash = fragment.get("config_hash") or "default"
        parser_name = fragment.get("parser_name") or w.name
        parser_version = fragment.get("parser_version") or w.version

        # 1) 元素锚点 ID 定稿
        blocks = fragment.get("content", {}).get("blocks", [])
        for block in blocks:
            seen_ordinals: dict[str, int] = {}
            for el in block.get("elements", []):
                loc = el.get("locator") or {}
                key = anchor.canonical_locator(loc)
                ordinal = seen_ordinals.get(key, 0)
                seen_ordinals[key] = ordinal + 1
                el["id"] = anchor.derive_element_id(parser_name, parser_version, config_hash, loc, ordinal)

        # 2) annotations 规范化 + 锚点引用解析（_anchor_locator → anchor_element_id）
        annotations = empty_annotations()
        for key, value in (fragment.get("annotations") or {}).items():
            if key in annotations and isinstance(value, list):
                annotations[key] = value
        locator_to_id = {
            anchor.canonical_locator(el.get("locator") or {}): el["id"]
            for b in blocks
            for el in b.get("elements", [])
        }
        for track in ("comments", "revisions"):
            for entry in annotations.get(track, []):
                loc = entry.pop("_anchor_locator", None)
                if loc is not None:
                    entry["anchor_element_id"] = locator_to_id.get(anchor.canonical_locator(loc))
                entry.setdefault("anchor_element_id", None)

        # 3) 质量定稿
        dims = fragment.get("quality_dims") or {}
        notes = list(fragment.get("notes") or [])
        if route_reason:
            notes.append(f"路由: {route_reason}")
        content = {"blocks": blocks}
        text_chars = sum(len(el.get("text") or "") for b in blocks for el in b.get("elements", []))
        truncated = bool(fragment.get("truncated"))
        coordinate_system = fragment.get("coordinate_system") or _default_coordinate_system(kind)
        provisional = {
            "content": content,
            "annotations": annotations,
            "provenance": {"coordinate_system": coordinate_system},
        }
        anchor_problems = anchor.validate_document({**provisional, "source_id": item.source_id})
        if anchor_problems:
            notes.append(f"锚点校验 {len(anchor_problems)} 处问题（前 3: {'; '.join(anchor_problems[:3])}）")
            if dims.get("source_anchoring") == "pass":
                dims["source_anchoring"] = "warning"
        default_fit = quality_mod.default_fitness(
            kind, dims, text_chars=text_chars, truncated=truncated, anchor_problems=len(anchor_problems)
        )
        suggested_fit = fragment.get("fitness") or {}
        trusted = w.backend == "inproc"
        fitness: dict[str, bool] = {}
        for key in quality_mod.FITNESS_KEYS:
            if key in suggested_fit:
                fitness[key] = bool(suggested_fit[key]) if trusted else (bool(suggested_fit[key]) and default_fit[key])
            else:
                fitness[key] = default_fit[key]
        quality = quality_mod.finalize_quality(kind, dims, fitness, notes)
        if truncated:
            quality["notes"].append("内容因结构上限被截断")

        # 4) 组装 + 身份 + 谱系
        source_block: dict[str, Any] = {
            "package_relative_path": item.package_relative_path,
            "detected_type": item.detected_type or {"mime": "?", "magic": "?", "method": "adg-magic"},
            "bytes": item.bytes or 0,
        }
        if item.mtime:
            source_block["mtime"] = item.mtime
        source_block["origin"] = item.origin
        source_block["derived_from"] = item.derived_from
        parser_block = {
            "name": parser_name,
            "version": parser_version,
            "worker_id": w.worker_id,
            # detail 可能含 worker 异常消息里的绝对路径，落盘前 scrub（隐私：产物只出现包内相对路径）
            "fell_back_from": [
                {**fb, "detail": scrub_text(fb["detail"])} if fb.get("detail") else fb
                for fb in fell_back
            ] if fell_back else None,
        }
        doc = EvidenceDocument(
            source_id=item.source_id,
            run_id=self.run_id,
            source=source_block,
            parser=parser_block,
            provenance={"coordinate_system": coordinate_system},
            content=content,
            annotations=annotations,
            quality=quality,
            lineage=[],
        )
        doc.finalize_identity()
        finished = now_iso_ms()
        doc.lineage.append(
            make_entry(
                fragment.get("operation") or "parse",
                input_sha256=item.source_id.split(":", 1)[1],
                output_sha256=doc.rendition_id.split(":", 1)[1],
                tool=parser_name,
                tool_version=parser_version,
                model_version=fragment.get("model_version"),
                config_hash=config_hash,
                started_at=started,
                finished_at=finished,
                exit_code=0,
                warnings=list(fragment.get("warnings") or []),
            )
        )
        return doc

    def _after_success(self, item: Item, doc: EvidenceDocument, fragment: dict, work_dir: Path, w: WorkerInfo) -> list[Item]:
        doc_dict = doc.to_dict()
        # L2：普通重跑复用同一 rendition；显式 --force 可刷新质量/run 等非身份字段。
        # rendition_id 只覆盖 content/annotations/coordinate_system，因此刷新前先加载并验证旧事实，
        # 再保留历史 lineage；内容身份仍由 load_rendition 和写后读取校验守住。
        rpath = manifest_mod.rendition_json_path(self.out_dir, item.source_id, doc.rendition_id)
        existing = manifest_mod.load_rendition(self.out_dir, item.source_id, doc.rendition_id) if rpath.exists() else None
        if existing is None or self.options.force:
            if existing is not None:
                doc_dict["lineage"] = list(existing.get("lineage") or []) + list(doc_dict.get("lineage") or [])
                doc_dict["integrity"]["payload_sha256"] = payload_hash_of(doc_dict)
            manifest_mod.atomic_write_json(rpath, doc_dict)
            manifest_mod.atomic_write_text(
                manifest_mod.rendition_md_path(self.out_dir, item.source_id, doc.rendition_id),
                render_document_md(doc_dict),
            )
        if doc.rendition_id not in item.renditions:
            item.renditions.append(doc.rendition_id)
        self._quality_cache.pop(item.source_id, None)
        self._refresh_item_best_quality(item)
        item.errors = None
        self.run_log.event(f"parsed {item.package_relative_path} via {doc.parser['name']} → {doc.rendition_id[:23]}")

        produced: list[Item] = []
        # 附件回灌（eml 等）：fragment 声明 attachment 文件（相对 work_dir）
        email_added = 0
        for att in fragment.get("attachments") or []:
            src = resolve_worker_output(work_dir, att["path"])
            members = [{"name": att["name"], "path": src}]
            added = self._ingest_members(
                container=item,
                container_kind="email",
                members=members,
                depth=(item.origin or {}).get("depth", 0) + 1 if item.origin else 1,
            )
            produced.extend(added)
            email_added += len(added)
        if fragment.get("attachments") is not None and item.kind == "email":
            self._record_email_expansion(item, email_added, ok=True)
        # transform 产物回灌（dwg→dxf 等）
        for of in fragment.get("output_files") or []:
            src = resolve_worker_output(work_dir, of["path"])
            members = [{"name": Path(of["path"]).name, "path": src, "derived_from": {"source_id": item.source_id, "tool": w.name, "note": of.get("note")}}]
            added = self._ingest_members(
                container=item,
                container_kind="transform",
                members=members,
                depth=1,
                derived_from=members[0]["derived_from"],
            )
            produced.extend(added)
        self.summary.parsed += 1
        return produced

    def _after_transform(self, item: Item, result: dict, work_dir: Path, w: WorkerInfo, route_reason: str, started: str) -> list[Item]:
        """transform worker 成功（dwg→dxf、pst→eml、加密→解密副本等）：
        回灌产物为新 item，原件标记已转换。不创建 EvidenceDocument。
        """
        produced: list[Item] = []
        out_files = result.get("output_files") or []
        warnings = result.get("warnings") or []
        for of in out_files:
            raw_path = of.get("path", "")
            src = resolve_worker_output(work_dir, raw_path)
            derived = {"source_id": item.source_id, "tool": w.name, "note": of.get("note")}
            added = self._ingest_members(
                container=item,
                container_kind="transform",
                members=[{"name": Path(raw_path).name, "path": src, "derived_from": derived}],
                depth=1,
                derived_from=derived,
            )
            produced.extend(added)
        # 原件标记已转换（无内容 rendition，内容在派生产物里）
        item.status = "parsed"
        item.errors = None
        item.best_quality = None
        # 之前失败留下的 unresolved 条目必须清掉：否则报告会带着一条早已不存在的原因
        self.manifest.integrity["unresolved_items"] = [
            entry
            for entry in self.manifest.integrity.get("unresolved_items", [])
            if entry.get("source_id") != item.source_id or entry.get("stage") not in ("parse", None)
        ]
        # 注意：transform 不计 summary.parsed（parse 成功才计）；transform 数量由派生 item 体现
        self.run_log.event(
            f"transform {item.package_relative_path} via {w.name} → {len(produced)} 产物"
            + (f"（{'；'.join(warnings)}）" if warnings else "")
        )
        return produced

    def _ingest_members(self, *, container: Item, container_kind: str, members: list[dict], depth: int, derived_from: dict | None = None) -> list[Item]:
        """把成员文件登记为 manifest items（Discoverer 在 scan 时 owned 顶层；parse 阶段由本方法接管）。"""
        from .discover import Discoverer, FoundFile

        disc = Discoverer(self.package_root, self.out_dir, policy=self.policy, run_log=self.run_log)
        # 让 Discoverer 的 _seen 包含已有 items，避免重复登记
        for existing in self.manifest.items:
            disc._seen[existing.source_id] = FoundFile(  # type: ignore[assignment]
                abs_path=self.package_root,
                package_relative_path=existing.package_relative_path,
                source_id=existing.source_id,
                bytes=existing.bytes or 0,
                mtime=None,
                detected=magic.DetectedType("", "", "container-derived", "low", existing.kind),
            )
        byte_members = []
        for m in members:
            if "data" in m:
                byte_members.append({"name": m["name"], "data": m["data"]})
            else:
                byte_members.append({"name": m["name"], "data": Path(m["path"]).read_bytes()})
        added_files = disc.ingest_container_members(
            None,
            container_source_id=container.source_id,
            container_kind=container_kind,
            container_rel=container.package_relative_path,
            members=byte_members,
            depth=depth,
        )
        added_items: list[Item] = []
        known = {it.source_id for it in self.manifest.items}
        for ff in added_files:
            if ff.source_id in known:
                continue
            it = Item(
                source_id=ff.source_id,
                package_relative_path=ff.package_relative_path,
                kind=ff.detected.kind,
                format=ff.detected.format,
                bytes=ff.bytes,
                origin=ff.origin,
                derived_from=derived_from,
                detected_type=ff.detected.to_dict(),
                mtime=ff.mtime,
            )
            self.manifest.items.append(it)
            added_items.append(it)
            known.add(ff.source_id)
        if added_items:
            self.manifest.compute_package_id()
            self.manifest.refresh_stats()
        return added_items

    def _record_email_expansion(self, item: Item, count: int, *, ok: bool) -> None:
        entries = self.manifest.integrity.setdefault("expanded_emails", [])
        for e in entries:
            if e.get("email_source_id") == item.source_id:
                e["status"] = "expanded" if ok else "failed"
                e["attachment_count"] = count
                return
        entries.append(
            {
                "email_source_id": item.source_id,
                "package_relative_path": item.package_relative_path,
                "status": "expanded" if ok else "failed",
                "attachment_count": count,
            }
        )

    # ---------------- 失败与收尾 ----------------

    def _mark_unresolved(self, item: Item, reason: str, *, stage: str, fell_back: list[dict] | None = None) -> None:
        item.status = "unresolved" if not fell_back else "failed"
        item.errors = (item.errors or []) + [reason]
        entry: dict[str, Any] = {
            "source_id": item.source_id,
            "package_relative_path": item.package_relative_path,
            "reason": reason,
            "stage": stage,
            "suggested_action": None,
        }
        if "需要密码" in reason or "加密" in reason:
            entry["suggested_action"] = "提供密码或解密副本后重新 scan/parse"
            if item.package_relative_path not in self.manifest.quality.get("encrypted", []):
                self.manifest.quality.setdefault("encrypted", []).append(item.package_relative_path)
        upsert_unresolved(self.manifest, entry)
        if item.kind == "email":
            self._record_email_expansion(item, 0, ok=False)
        self.summary.unresolved += 1 if not fell_back else 0
        self.summary.failed += 1 if fell_back else 0

    def _cache_item_qualities(self, item: Item) -> None:
        if item.source_id in self._quality_cache:
            return
        docs = manifest_mod.list_rendition_docs(self.out_dir, item.source_id)
        self._quality_cache[item.source_id] = [d.get("quality", {}) for d in docs]

    def _refresh_item_best_quality(self, item: Item) -> None:
        self._cache_item_qualities(item)
        qualities = self._quality_cache.get(item.source_id, [])
        best = None
        for q in qualities:
            o = q.get("overall")
            if quality_mod.best_quality_rank(o) > quality_mod.best_quality_rank(best):
                best = o
        item.best_quality = best

    def _post_alignments(self, queue: list[Item]) -> None:
        for item in self.manifest.items:
            if len(item.renditions) < 2:
                continue
            docs = manifest_mod.list_rendition_docs(self.out_dir, item.source_id)
            docs = [d for d in docs if d.get("rendition_id") in set(item.renditions)]
            for i in range(len(docs)):
                for j in range(i + 1, len(docs)):
                    path = manifest_mod.alignment_path(self.out_dir, item.source_id, docs[i]["rendition_id"], docs[j]["rendition_id"])
                    if path.exists():
                        continue
                    table = anchor.align_renditions(docs[i], docs[j])
                    manifest_mod.atomic_write_json(path, table)
                    self.summary.alignments += 1

    def _cleanup_work(self, work_dir: Path) -> None:
        if self.options.keep_work:
            return
        shutil.rmtree(work_dir, ignore_errors=True)

    # ---------------- 镜像视图（人类定位） ----------------

    def _write_renderings(self) -> None:
        """写 renderings/ 镜像视图：每个 item 的最优 rendition 按"原相对路径"布局，
        方便用户精准定位（与原件目录结构一致）。documents/哈希 仍是程序寻址源。
        """
        from .render import render_document_md

        index_rows: list[dict] = []
        docs_by_sid = {
            it.source_id: manifest_mod.list_rendition_docs(self.out_dir, it.source_id)
            for it in self.manifest.items
        }
        reliable_ids = quality_mod.reliably_read_ids(
            self.manifest.items, lambda sid: [d.get("quality", {}) for d in docs_by_sid.get(sid, [])]
        )
        for it in self.manifest.items:
            if not it.renditions:
                # 无自有 rendition ≠ 没读到：transform 型原件的内容在派生物里
                index_rows.append({
                    "rel": it.package_relative_path, "kind": it.kind,
                    "status": it.status, "reliable": it.source_id in reliable_ids,
                    "md": None, "note": _item_note(it),
                })
                continue
            docs = docs_by_sid[it.source_id]
            if not docs:
                continue
            best = max(docs, key=lambda d: quality_mod.best_quality_rank(d.get("quality", {}).get("overall")))
            try:
                md_text = render_document_md(best)
            except Exception:
                md_text = f"# {it.package_relative_path}\n\n（渲染失败）\n"
            md_path = manifest_mod.rendering_md_path(self.out_dir, it.package_relative_path)
            json_path = manifest_mod.rendering_json_path(self.out_dir, it.package_relative_path)
            manifest_mod.atomic_write_text(md_path, md_text)
            manifest_mod.atomic_write_json(json_path, best)
            # 派生物（渲染图/帧/dxf/svg 等）的实际可看文件复制到顶层镜像同名位置
            if it.origin or it.derived_from:
                try:
                    src = resolve_source_path(it, self.package_root, self.out_dir)
                    if src.exists() and src.is_file():
                        dest = manifest_mod.viewable_path(self.out_dir, it.package_relative_path)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not dest.exists():
                            shutil.copy2(src, dest)
                except Exception:
                    pass  # 复制失败不阻塞（md/json 已写）
            reliable = it.source_id in reliable_ids
            index_rows.append({
                "rel": it.package_relative_path, "kind": it.kind,
                "status": it.status, "reliable": reliable,
                "md": md_path.relative_to(self.out_dir).as_posix(),
                "parser": best.get("parser", {}).get("name"),
            })
        self._write_renderings_index(index_rows)

    def _write_renderings_index(self, rows: list[dict]) -> None:
        from .privacy import scrub_text

        lines = [
            "# 原文件 → 转换产物 对照索引",
            "",
            "原件只读未改。本目录顶层按**原文件相对路径**布局转换产物，结构与原件一致——并排打开即可对比。",
            "派生物（渲染图/帧/dxf 等）在与原件同名的子目录里，可直接打开查看。",
            "`.adg/` 是程序内部目录（哈希寻址/展开中间产物/日志），一般无需理会。",
            "",
            "| 原文件 | 类型 | 状态 | 内容可作事实依据 | 解析器 | 可读视图 |",
            "|--------|------|------|------------------|--------|----------|",
        ]
        for r in sorted(rows, key=lambda x: x["rel"]):
            reliable = "✅ 是" if r.get("reliable") else ("⚠️ 部分" if r.get("md") else "❌ 否")
            md = f"`{r['md']}`" if r.get("md") else "—"
            parser = r.get("parser") or "—"
            note = r.get("note", "")
            note_txt = f" <br><sub>{note}</sub>" if note else ""
            lines.append(f"| `{scrub_text(r['rel'])}` | {r['kind']} | {r['status']}{note_txt} | {reliable} | {parser} | {md} |")
        lines.append("")
        (self.out_dir / "对照索引.md").write_text("\n".join(lines), encoding="utf-8")


def _item_note(it) -> str:
    if it.kind == "archive":
        return "容器：见展开成员"
    if it.kind == "dwg" and it.status == "parsed":
        return "已转 DXF，见派生 .dxf 产物"
    if it.status == "unresolved":
        return (it.errors or ["无可用 parser"])[0][:80]
    return ""


def upsert_unresolved(m: PackageManifest, entry: dict) -> None:
    entries = m.integrity.setdefault("unresolved_items", [])
    for i, e in enumerate(entries):
        if e.get("source_id") == entry["source_id"] and e.get("stage") == entry.get("stage"):
            entries[i] = entry
            return
    entries.append(entry)


def render_manifest_md_safe(m: PackageManifest) -> str:
    from .manifest import render_manifest_md

    return render_manifest_md(m)


def _default_coordinate_system(kind: str) -> dict:
    mapping = {
        "pdf": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
        "image": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
        "docx": {"name": "paragraph", "units": "index"},
        "xlsx": {"name": "cell", "units": "A1"},
        "csv": {"name": "row", "units": "index"},
        "chat": {"name": "message", "units": "id"},
        "audio": {"name": "timecode", "units": "seconds"},
        "video": {"name": "timecode", "units": "seconds"},
        "dxf": {"name": "entity", "units": "handle"},
        "email": {"name": "paragraph", "units": "index"},
        "html": {"name": "paragraph", "units": "index"},
        "text": {"name": "paragraph", "units": "index"},
    }
    return mapping.get(kind, {"name": "paragraph", "units": "index"})
