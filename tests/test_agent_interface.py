"""P5 Agent 接口：检索索引、元素检索、锚点回溯与六个只读查询操作。"""

import atexit
import json
import tempfile
import unittest
from pathlib import Path

from gateway import index as index_mod
from gateway.agent_api import open_package
from gateway.discover import discover_package
from gateway.dispatcher import Dispatcher, ParseOptions
from gateway.errors import ADGError
from gateway.hardware import detect_environment
from gateway.index import build_index, make_snippet, open_index, parse_terms, search, segment_cjk
from gateway.inventory import build_manifest, recompute_package_quality
from gateway.manifest import load_manifest, save_manifest
from gateway.privacy import contains_absolute_path
from gateway.selftest import build_package
from gateway.workers import WorkerRegistry
from packs.legal import chat_kind_refiner, register_lite_parsers

_FIXTURE: tuple[Path, Path] | None = None


def built_package() -> tuple[Path, Path]:
    """整个模块共用一次 scan+parse（构建成本高，结论只读）。"""
    global _FIXTURE
    if _FIXTURE is None:
        tmp = tempfile.TemporaryDirectory(prefix="adg-agent-if-")
        atexit.register(tmp.cleanup)
        root = Path(tmp.name)
        package = root / "案件材料"
        out = root / "案件材料（ADG）"
        build_package(package)
        registry = WorkerRegistry()
        register_lite_parsers(registry)
        registry.load_external()
        discovery = discover_package(package, out, kind_refiners=[chat_kind_refiner()])
        manifest = build_manifest(discovery)
        recompute_package_quality(manifest, reliably_read_ids=set())
        save_manifest(manifest, out)
        Dispatcher(
            package,
            out,
            load_manifest(out),
            registry,
            env=detect_environment(),
            options=ParseOptions(force=True, no_abs_log=True),
        ).run()
        _FIXTURE = (package, out)
    return _FIXTURE


class SegmentationTest(unittest.TestCase):
    """中日韩逐字切分：入库与查询共用同一函数，2 字词也必须可检索。"""

    def test_cjk_split_latin_intact(self):
        self.assertEqual(segment_cjk("担保合同 contract v2"), "担 保 合 同 contract v2")

    def test_non_cjk_untouched(self):
        self.assertEqual(segment_cjk("invoice-2024"), "invoice-2024")

    def test_parse_terms_quotes(self):
        self.assertEqual(parse_terms('担保 "债务加入" 效力'), ["担保", "债务加入", "效力"])

    def test_snippet_marks_hit(self):
        s = make_snippet("第三步：核对签章。", ["签章"])
        self.assertIn("【签章】", s)


class IndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package, cls.out = built_package()
        cls.stats = build_index(cls.out, force=True)
        cls.conn = open_index(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_covers_every_manifest_item(self):
        manifest = load_manifest(self.out)
        self.assertEqual(self.stats.items, len(manifest.items))
        self.assertGreater(self.stats.elements, 0)

    def test_rebuild_is_noop_when_manifest_unchanged(self):
        again = build_index(self.out)
        self.assertFalse(again.rebuilt)
        self.assertEqual(again.elements, self.stats.elements)

    def test_force_rebuild_is_deterministic(self):
        again = build_index(self.out, force=True)
        self.assertTrue(again.rebuilt)
        self.assertEqual((again.items, again.renditions, again.elements),
                         (self.stats.items, self.stats.renditions, self.stats.elements))

    def test_cjk_two_char_query_hits(self):
        hits = search(self.conn, "签章")["hits"]
        self.assertTrue(hits, "两字中文词必须可检索（FTS5 默认分词器切不开中文）")
        self.assertTrue(any(h["package_relative_path"] == "合同笔记.txt" for h in hits))

    def test_multiple_terms_are_and(self):
        both = search(self.conn, "核对 签章")["hits"]
        self.assertTrue(all("核对" in h["text"] and "签章" in h["text"] for h in both))
        self.assertFalse(search(self.conn, "签章 完全不存在的词元")["hits"])

    def test_hit_carries_anchor_and_identity(self):
        hit = search(self.conn, "签章")["hits"][0]
        for key in ("element_id", "rendition_id", "source_id", "locator", "snippet", "quotable"):
            self.assertIn(key, hit)
        self.assertIn("system", hit["locator"])

    def test_kind_filter(self):
        hits = search(self.conn, "核对", kind="csv")["hits"]
        self.assertTrue(all(h["kind"] == "csv" for h in hits))

    def test_quotable_only_filters(self):
        hits = search(self.conn, "核对", quotable_only=True)["hits"]
        self.assertTrue(all(h["quotable"] for h in hits))

    def test_punctuation_only_query_degrades_not_crashes(self):
        self.assertEqual(search(self.conn, "、、、")["hits"], [])

    def test_like_wildcards_are_escaped(self):
        self.assertEqual(search(self.conn, "%")["hits"], [])
        self.assertEqual(index_mod._like_escape("a%b_c"), r"a\%b\_c")

    def test_like_fallback_still_searches_chinese(self):
        """FTS5 不可用时降级 LIKE：显性标注 engine，中文检索仍要能用。"""
        original = index_mod._create_fts
        index_mod._create_fts = lambda conn: False
        try:
            stats = build_index(self.out, force=True)
            self.assertEqual(stats.engine, "like")
            conn = open_index(self.out)
            try:
                result = search(conn, "签章")
                self.assertEqual(result["engine"], "like")
                self.assertTrue(result["hits"])
            finally:
                conn.close()
        finally:
            index_mod._create_fts = original
            build_index(self.out, force=True)


class SearchSemanticsRegressionTest(unittest.TestCase):
    """检索过滤语义的回归：这些组合曾经会给出静默错误答案。"""

    @classmethod
    def setUpClass(cls):
        cls.package, cls.out = built_package()
        build_index(cls.out, force=True)
        cls.conn = open_index(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_quotable_only_searches_all_renditions(self):
        """否则最优轨恰好不可引用时，会谎报"这份材料没有可引用内容"。"""
        result = search(self.conn, "核对", quotable_only=True)
        self.assertEqual(result["rendition_scope"], "all_renditions")
        self.assertTrue(all(h["quotable"] for h in result["hits"]))

    def test_default_scope_is_best_rendition(self):
        self.assertEqual(search(self.conn, "核对")["rendition_scope"], "best_rendition")

    def test_path_prefix_accepts_both_container_forms(self):
        """产物目录里看到的是镜像形态（zip/成员），manifest 里是 :: 形态，两种都必须命中。"""
        raw = search(self.conn, "证人证言", path_prefix="bundle.zip::")["hits"]
        mirrored = search(self.conn, "证人证言", path_prefix="bundle.zip/")["hits"]
        self.assertTrue(raw, "容器成员应可被 :: 前缀命中")
        self.assertEqual(
            [h["element_id"] for h in raw], [h["element_id"] for h in mirrored],
            "镜像形态前缀必须与 :: 形态命中同一批元素",
        )

    def test_path_prefix_miss_is_not_silent_wrong_answer(self):
        self.assertEqual(search(self.conn, "证人证言", path_prefix="不存在目录/")["hits"], [])

    def test_like_candidate_cap_is_reported(self):
        """LIKE 降级模式截断候选时必须显性标注，不能让"最佳命中"悄悄落在窗口外。"""
        original = (index_mod.LIKE_CANDIDATE_MULTIPLIER, index_mod.LIKE_CANDIDATE_FLOOR)
        index_mod.LIKE_CANDIDATE_MULTIPLIER, index_mod.LIKE_CANDIDATE_FLOOR = 1, 1
        create_fts = index_mod._create_fts
        index_mod._create_fts = lambda conn: False
        try:
            build_index(self.out, force=True)
            conn = open_index(self.out)
            try:
                result = search(conn, "的", limit=1)
                self.assertEqual(result["engine"], "like")
                self.assertTrue(result["candidate_cap_reached"], "候选触顶必须显性告知")
            finally:
                conn.close()
        finally:
            index_mod.LIKE_CANDIDATE_MULTIPLIER, index_mod.LIKE_CANDIDATE_FLOOR = original
            index_mod._create_fts = create_fts
            build_index(self.out, force=True)


class AgentApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package, cls.out = built_package()
        cls.view = open_package(cls.package, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.view.close()

    def test_ingestion_status_matches_manifest(self):
        status = self.view.get_ingestion_status()
        manifest = load_manifest(self.out)
        self.assertEqual(status["total_items"], len(manifest.items))
        self.assertTrue(status["all_items_accounted"])
        self.assertEqual(
            status["reliably_read_count"] + status["not_reliable_count"],
            len([i for i in manifest.items if i.status != "unsupported"]),
        )

    def test_unreliable_items_carry_reason(self):
        for entry in self.view.get_ingestion_status()["not_reliably_read"]:
            self.assertTrue(entry["reason"], f"{entry['package_relative_path']} 未给出原因")

    def test_list_items_filters_and_pagination(self):
        all_items = self.view.list_package_items(limit=1000)
        self.assertEqual(all_items["total"], all_items["returned"])
        pdfs = self.view.list_package_items(kind="pdf")
        self.assertTrue(all(i["kind"] == "pdf" for i in pdfs["items"]))
        page = self.view.list_package_items(limit=2, offset=1)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["items"][0]["source_id"], all_items["items"][1]["source_id"])
        unreliable = self.view.list_package_items(unreliable_only=True)
        self.assertTrue(all(not i["reliably_read"] for i in unreliable["items"]))

    def test_get_document_by_path_and_by_source_id(self):
        doc = self.view.get_document("合同笔记.txt", max_elements=2)
        self.assertEqual(doc["kind"], "text")
        self.assertTrue(doc["selected_rendition"]["parser"])
        self.assertEqual(len(doc["elements"]), 2)
        self.assertGreaterEqual(doc["elements_total"], 2)
        by_hash = self.view.get_document(doc["source_id"], include_elements=False)
        self.assertEqual(by_hash["package_relative_path"], "合同笔记.txt")
        prefix = self.view.get_document(doc["source_id"].split(":", 1)[1][:16], include_elements=False)
        self.assertEqual(prefix["source_id"], doc["source_id"])

    def test_get_document_pagination_is_stable(self):
        first = self.view.get_document("合同笔记.txt", max_elements=1, offset=0)["elements"]
        second = self.view.get_document("合同笔记.txt", max_elements=1, offset=1)["elements"]
        self.assertNotEqual(first[0]["element_id"], second[0]["element_id"])

    def test_unknown_ref_fails_loudly(self):
        with self.assertRaises(ADGError):
            self.view.get_document("不存在的文件.pdf")
        with self.assertRaises(ADGError):
            self.view.get_document("sha256:" + "0" * 64)

    def test_search_then_resolve_anchor_round_trip(self):
        hit = self.view.search_elements("签章", limit=1)["hits"][0]
        anchor = self.view.resolve_anchor(hit["element_id"])
        self.assertGreaterEqual(anchor["found"], 1)
        match = anchor["matches"][0]
        self.assertEqual(match["package_relative_path"], hit["package_relative_path"])
        self.assertEqual(match["locator"], hit["locator"])
        self.assertIn("context", match)

    def test_search_warns_about_unread_material(self):
        result = self.view.search_elements("核对", limit=5)
        self.assertTrue(result["coverage_caveat"], "包内存在未读全材料时必须显性提示")
        self.assertTrue(result["index"]["available"])

    def test_unresolved_items_have_reason(self):
        unresolved = self.view.list_unresolved_items()
        self.assertGreater(unresolved["total"], 0)
        for entry in unresolved["unresolved_items"]:
            self.assertTrue(entry["reason"])
            self.assertTrue(entry["package_relative_path"])

    def test_deduplicated_paths_stay_visible(self):
        """去重只保留一个 item，但另一份路径是案卷事实，不能就此消失。"""
        rows = self.view.list_package_items(limit=1000)["items"]
        dup_rows = [r for r in rows if r.get("duplicate_paths")]
        self.assertTrue(dup_rows, "fixture 含同字节重复文件，应有 duplicate_paths")
        paths = dup_rows[0]["duplicate_paths"]
        self.assertGreaterEqual(len(paths), 2)
        self.assertIn(dup_rows[0]["package_relative_path"], paths)
        doc = self.view.get_document(dup_rows[0]["source_id"], include_elements=False)
        self.assertEqual(doc["duplicate_paths"], paths)

    def test_negative_offset_is_clamped(self):
        head = self.view.get_document("合同笔记.txt", max_elements=2, offset=0)["elements"]
        clamped = self.view.get_document("合同笔记.txt", max_elements=2, offset=-5)["elements"]
        self.assertEqual([e["element_id"] for e in head], [e["element_id"] for e in clamped])

    def test_every_operation_stamps_the_contract_version(self):
        """Agent 消费的是这些 JSON：没有版本戳，下游无法判定契约是否变过。"""
        from gateway.agent_api import AGENT_API_VERSION

        results = [
            self.view.get_ingestion_status(),
            self.view.list_package_items(limit=1),
            self.view.list_unresolved_items(),
            self.view.get_document("合同笔记.txt", include_elements=False),
            self.view.search_elements("核对", limit=1),
            self.view.resolve_anchor("el_none"),
        ]
        for r in results:
            self.assertEqual(AGENT_API_VERSION, r.get("contract_version"))

    def test_contract_version_is_independent_of_release_version(self):
        """契约版本绑到发布版本上就没有意义了——发版不改契约就不该动它。"""
        from gateway import __version__
        from gateway.agent_api import AGENT_API_VERSION

        self.assertNotEqual(__version__, AGENT_API_VERSION)

    def test_no_absolute_path_leaks_in_any_result(self):
        payloads = [
            self.view.get_ingestion_status(),
            self.view.list_package_items(limit=1000),
            self.view.list_unresolved_items(),
            self.view.get_document("合同笔记.txt"),
            self.view.search_elements("核对", limit=20),
        ]
        for payload in payloads:
            text = json.dumps(payload, ensure_ascii=False)
            self.assertFalse(contains_absolute_path(text), "只读接口返回值泄漏了绝对路径")


class StaleIndexDegradationTest(unittest.TestCase):
    """索引需重建但输出目录被写占用：查询仍可用，但必须显性标注陈旧。

    直接失败会让 parse 期间所有检索不可用（受管路径变贵）；不标注则是拿旧答案冒充新答案。
    """

    @classmethod
    def setUpClass(cls):
        cls.package, cls.out = built_package()

    def test_search_degrades_to_stale_index_with_explicit_caveat(self):
        from gateway.manifest import OutputWriteLock

        build_index(self.out, force=True)
        with OutputWriteLock(self.out):
            with open_package(self.package, self.out) as view:
                view.index(force=True)  # 强制重建 = 模拟索引过期
                result = view.search_elements("核对", limit=1)
                self.assertTrue(result["index"]["stale"])
                self.assertIn("stale_index_caveat", result)

    def test_fresh_index_is_not_marked_stale(self):
        build_index(self.out, force=True)
        with open_package(self.package, self.out) as view:
            self.assertFalse(view.search_elements("核对", limit=1)["index"]["stale"])

    def test_missing_index_under_lock_still_fails_loudly(self):
        """既有索引都没有时无话可说，必须失败而不是假装查过。"""
        import tempfile
        from gateway.errors import ADGError
        from gateway.index import index_path
        from gateway.manifest import OutputWriteLock

        target = index_path(self.out)
        backup = target.with_suffix(".bak")
        target.rename(backup)
        try:
            with OutputWriteLock(self.out):
                with open_package(self.package, self.out) as view:
                    with self.assertRaises(ADGError):
                        view.search_elements("核对", limit=1)
        finally:
            backup.rename(target)


if __name__ == "__main__":
    unittest.main()
