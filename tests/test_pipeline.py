"""端到端：scan → parse → manifest 完整性 + 隐私 + 降级链。"""

import unittest
from pathlib import Path

from gateway.discover import discover_package
from gateway.dispatcher import Dispatcher, ParseOptions
from gateway.hardware import detect_environment
from gateway.inventory import build_manifest, recompute_package_quality
from gateway.manifest import list_rendition_docs, load_manifest, load_rendition, save_manifest
from gateway.privacy import contains_absolute_path
from gateway.selftest import EXPECTED_DEDUP, EXPECTED_ITEMS_AFTER_SCAN, build_package
from packs.legal import register_lite_parsers
from packs.legal import chat_kind_refiner
from gateway.workers import WorkerRegistry
from gateway.workers import WorkerInfo


def _text_fragment(text: str, *, locator_system: str = "paragraph") -> dict:
    from gateway import quality

    element = {"type": "paragraph", "text": text, "locator": {"system": locator_system, "index": 0}}
    return {
        "parser_name": "fixture",
        "parser_version": "1",
        "content": {"blocks": [{"block_type": "section", "index": 0, "elements": [element] if text else []}]},
        "quality_dims": {name: "pass" for name in quality.DIMENSIONS},
    }


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory(prefix="adg-test-")
        cls.root = Path(cls._tmp.name)
        cls.package = cls.root / "案件材料"
        cls.out = cls.root / "adg-output" / "案件材料"
        build_package(cls.package)
        registry = WorkerRegistry()
        register_lite_parsers(registry)
        registry.load_external()
        cls.registry = registry
        cls.env = detect_environment()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _scan_only(self):
        """仅 scan（不解析）：完整性不变量在此断言。"""
        discovery = discover_package(self.package, self.out, kind_refiners=[chat_kind_refiner()])
        m = build_manifest(discovery)
        recompute_package_quality(m, reliably_read_ids=set())
        save_manifest(m, self.out)
        return load_manifest(self.out)

    def _full_pipeline(self):
        self._scan_only()
        d = Dispatcher(self.package, self.out, load_manifest(self.out), self.registry, env=self.env,
                       options=ParseOptions(force=True, no_abs_log=True))
        d.run()
        return load_manifest(self.out)

    def test_completeness_scan(self):
        """scan 阶段完整性：每个文件都入账，计数与去重符合手工清点。"""
        m = self._scan_only()
        self.assertTrue(m.integrity["all_items_accounted"])
        self.assertEqual(len(m.items), EXPECTED_ITEMS_AFTER_SCAN)
        self.assertEqual(m.stats["dedup_saved"], EXPECTED_DEDUP)

    def test_completeness_after_parse(self):
        """parse 后：邮件附件回灌使 item +1，完整性仍成立。"""
        m = self._full_pipeline()
        self.assertTrue(m.integrity["all_items_accounted"])
        # 每一项都要有来路：scan 基线 + 邮件附件回灌 + （本机装了 pdf_render 时）扫描件页位图
        derived_pages = [i for i in m.items if (i.derived_from or {}).get("tool") == "pdf_render"]
        self.assertEqual(len(m.items), EXPECTED_ITEMS_AFTER_SCAN + 1 + len(derived_pages))

    def test_no_path_traversal_escaped(self):
        self._full_pipeline()
        self.assertFalse((self.root / "evil.txt").exists())
        self.assertFalse((self.package / "evil.txt").exists())

    def test_email_attachment_backfilled(self):
        m = self._full_pipeline()
        att = next((it for it in m.items if it.package_relative_path.endswith("附件说明.txt")), None)
        self.assertIsNotNone(att, "邮件附件应回灌为 item")
        self.assertTrue(att.renditions, "附件应被解析")

    def test_nested_archive_expanded(self):
        m = self._full_pipeline()
        inner = next((it for it in m.items if "内层.zip::c.txt" in it.package_relative_path), None)
        self.assertIsNotNone(inner, "嵌套压缩包成员应展开")

    def test_encrypted_pdf_in_unresolved(self):
        m = self._full_pipeline()
        enc = m.quality.get("encrypted", [])
        self.assertIn("encrypted.pdf", enc)
        enc_item = next(it for it in m.items if it.package_relative_path == "encrypted.pdf")
        self.assertIn(enc_item.status, ("unresolved", "failed"))

    def test_no_absolute_path_leak(self):
        self._full_pipeline()
        leaks = []
        for p in self.out.rglob("*"):
            if not p.is_file() or "logs" in p.parts:
                continue
            if p.suffix in (".json", ".md"):
                text = p.read_text(encoding="utf-8", errors="ignore")
                if contains_absolute_path(text):
                    leaks.append(p.name)
        self.assertEqual(leaks, [], f"产物泄漏绝对路径: {leaks}")

    def test_rendition_payload_hash_self_verifies(self):
        m = self._full_pipeline()
        import hashlib
        import json

        from gateway.hashing import canonical_json

        # 取一个 rendition 验证 payload_sha256
        any_item = next(it for it in m.items if it.renditions)
        doc = list_rendition_docs(self.out, any_item.source_id)[0]
        integ = doc["integrity"]
        clone = json.loads(json.dumps(doc))
        clone["integrity"].pop("payload_sha256")
        recomputed = hashlib.sha256(canonical_json(clone).encode("utf-8")).hexdigest()
        self.assertEqual(integ["payload_sha256"], recomputed)

    def test_rerun_idempotent_rendition_id(self):
        """同 parser/配置重跑 → rendition_id 不变（L2）。"""
        self._full_pipeline()
        m1 = load_manifest(self.out)
        d = Dispatcher(self.package, self.out, m1, self.registry, env=self.env,
                       options=ParseOptions(force=True, no_abs_log=True))
        d.run()
        m2 = load_manifest(self.out)
        # 取 docx item 对比 rendition 集合
        docx1 = next(it for it in m1.items if it.package_relative_path == "协议.docx")
        docx2 = next(it for it in m2.items if it.package_relative_path == "协议.docx")
        self.assertEqual(set(docx1.renditions), set(docx2.renditions))


class QualityRefreshRegressionTest(unittest.TestCase):
    def test_force_rerun_refreshes_best_quality_from_new_rendition(self):
        import tempfile

        from gateway import quality

        with tempfile.TemporaryDirectory(prefix="adg-quality-refresh-") as d:
            root = Path(d)
            package = root / "package"
            package.mkdir()
            (package / "a.txt").write_text("source", encoding="utf-8")
            out = root / "output"
            discovery = discover_package(package, out)
            manifest = build_manifest(discovery)
            recompute_package_quality(manifest, reliably_read_ids=set())
            save_manifest(manifest, out)

            calls = {"count": 0}

            def parse(_path, **_kwargs):
                calls["count"] += 1
                dims = {name: "pass" for name in quality.DIMENSIONS}
                if calls["count"] == 1:
                    dims["text_extraction"] = "fail"
                return {
                    "parser_name": "text_lite",
                    "parser_version": "test",
                    "content": {
                        "blocks": [{
                            "block_type": "section",
                            "index": 0,
                            "elements": [{
                                "type": "paragraph",
                                "text": f"rendition-{calls['count']}",
                                "locator": {"system": "paragraph", "index": 0},
                            }],
                        }]
                    },
                    "quality_dims": dims,
                }

            registry = WorkerRegistry()
            registry.register_lite(WorkerInfo(
                name="text_lite", version="test", role="parse", kinds=["text"], formats=None,
                requirements={}, backend="inproc", parse_fn=parse,
            ))
            Dispatcher(package, out, load_manifest(out), registry, env=self.env if hasattr(self, "env") else detect_environment(),
                       options=ParseOptions(force=True, no_abs_log=True)).run()
            first = next(item for item in load_manifest(out).items if item.package_relative_path == "a.txt")
            self.assertEqual(first.best_quality, "fail")

            Dispatcher(package, out, load_manifest(out), registry, env=detect_environment(),
                       options=ParseOptions(force=True, no_abs_log=True)).run()
            second = next(item for item in load_manifest(out).items if item.package_relative_path == "a.txt")
            self.assertEqual(len(second.renditions), 2)
            self.assertEqual(second.best_quality, "pass")
            self.assertEqual(second.status, "parsed")

    def test_force_rerun_refreshes_same_rendition_assessment(self):
        import tempfile

        from gateway import quality

        with tempfile.TemporaryDirectory(prefix="adg-same-rendition-refresh-") as d:
            root = Path(d)
            package = root / "package"
            package.mkdir()
            (package / "a.txt").write_text("source", encoding="utf-8")
            out = root / "output"
            discovery = discover_package(package, out)
            manifest = build_manifest(discovery)
            recompute_package_quality(manifest, reliably_read_ids=set())
            save_manifest(manifest, out)

            calls = {"count": 0}

            def parse(_path, **_kwargs):
                calls["count"] += 1
                dims = {name: "pass" for name in quality.DIMENSIONS}
                if calls["count"] == 1:
                    dims["text_extraction"] = "unknown"
                return {
                    "parser_name": "text_lite",
                    "parser_version": "test",
                    "content": {
                        "blocks": [{
                            "block_type": "section",
                            "index": 0,
                            "elements": [{
                                "type": "paragraph",
                                "text": "same content",
                                "locator": {"system": "paragraph", "index": 0},
                            }],
                        }]
                    },
                    "quality_dims": dims,
                }

            registry = WorkerRegistry()
            registry.register_lite(WorkerInfo(
                name="text_lite", version="test", role="parse", kinds=["text"], formats=None,
                requirements={}, backend="inproc", parse_fn=parse,
            ))
            env = detect_environment()
            Dispatcher(package, out, load_manifest(out), registry, env=env,
                       options=ParseOptions(force=True, no_abs_log=True)).run()
            first_item = load_manifest(out).items[0]
            first_doc = load_rendition(out, first_item.source_id, first_item.renditions[0])
            self.assertEqual(first_doc["quality"]["overall"], "pass_with_warnings")

            Dispatcher(package, out, load_manifest(out), registry, env=env,
                       options=ParseOptions(force=True, no_abs_log=True)).run()
            second_item = load_manifest(out).items[0]
            second_doc = load_rendition(out, second_item.source_id, second_item.renditions[0])
            self.assertEqual(second_item.renditions, first_item.renditions)
            self.assertNotEqual(second_doc["run_id"], first_doc["run_id"])
            self.assertEqual(second_doc["quality"]["overall"], "pass")
            self.assertEqual(len(second_doc["lineage"]), 2)


class AdmissionBoundaryTest(unittest.TestCase):
    def _run_chain(self, parsers: list[tuple[str, object]]):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="adg-admission-") as d:
            root = Path(d)
            package = root / "package"
            package.mkdir()
            (package / "a.txt").write_text("source", encoding="utf-8")
            out = root / "output"
            manifest = build_manifest(discover_package(package, out))
            recompute_package_quality(manifest, reliably_read_ids=set())
            save_manifest(manifest, out)

            registry = WorkerRegistry()
            for name, parser in parsers:
                registry.register_lite(WorkerInfo(
                    name=name, version="1", role="parse", kinds=["text"], formats=None,
                    requirements={}, backend="inproc", parse_fn=parser,
                ))
            options = ParseOptions(
                force=True,
                no_abs_log=True,
                chain_override={"text": [name for name, _parser in parsers]},
            )
            Dispatcher(package, out, load_manifest(out), registry, env=detect_environment(), options=options).run()
            item = load_manifest(out).items[0]
            return item, list_rendition_docs(out, item.source_id)

    def test_invalid_document_is_rejected_before_persistence(self):
        def invalid(_path, **_kwargs):
            return _text_fragment("invalid", locator_system="not-a-schema-locator")

        def valid(_path, **_kwargs):
            fragment = _text_fragment("valid")
            fragment["parser_name"] = "valid"
            return fragment

        item, docs = self._run_chain([("invalid", invalid), ("valid", valid)])
        self.assertEqual(["valid"], [doc["parser"]["name"] for doc in docs])
        self.assertEqual(1, len(item.renditions), "未通过完整 Schema 的 rendition 不得留下孤儿文件")
        self.assertEqual("rejected_by_preflight", item.chain_executions[0]["fell_back"][0]["reason"])

    def test_low_quality_result_continues_to_next_worker(self):
        calls: list[str] = []

        def empty(_path, **_kwargs):
            calls.append("empty")
            fragment = _text_fragment("")
            fragment["parser_name"] = "empty"
            return fragment

        def useful(_path, **_kwargs):
            calls.append("useful")
            fragment = _text_fragment("useful")
            fragment["parser_name"] = "useful"
            return fragment

        item, docs = self._run_chain([("empty", empty), ("useful", useful)])
        self.assertEqual(["empty", "useful"], calls)
        self.assertEqual(["useful"], [doc["parser"]["name"] for doc in docs])
        self.assertEqual("low_quality", item.chain_executions[0]["fell_back"][0]["reason"])

    def test_last_low_quality_result_is_kept_as_explicit_evidence(self):
        def empty(_path, **_kwargs):
            fragment = _text_fragment("")
            fragment["parser_name"] = "empty-last-resort"
            return fragment

        item, docs = self._run_chain([("empty-last-resort", empty)])
        self.assertEqual(["empty-last-resort"], [doc["parser"]["name"] for doc in docs])
        self.assertFalse(docs[0]["quality"]["fitness"]["fact_extraction"])
        self.assertEqual("ok", item.chain_executions[0]["status"])


if __name__ == "__main__":
    unittest.main()
