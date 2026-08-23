"""质量模型聚合 + fitness（spec 05）。"""

import unittest
from pathlib import Path

from gateway import quality


class QualityAggregationTest(unittest.TestCase):
    def test_fail_propagates(self):
        dims = {"text_extraction": "fail", "source_anchoring": "pass"}
        q = quality.finalize_quality("pdf", dims)
        self.assertEqual(q["overall"], "fail")
        self.assertFalse(q["fitness"]["authenticity_assessment"])  # 恒 false

    def test_warning_propagates(self):
        dims = {"text_extraction": "warning"}
        q = quality.finalize_quality("pdf", dims)
        self.assertEqual(q["overall"], "pass_with_warnings")

    def test_all_pass(self):
        dims = {d: "pass" for d in quality.DIMENSIONS}
        q = quality.finalize_quality("pdf", dims)
        self.assertEqual(q["overall"], "pass")

    def test_unknown_on_expected_dim_warns(self):
        # ocr 对 image 是 expected；unknown → 压级
        dims = {"text_extraction": "pass", "ocr": "unknown"}
        q = quality.finalize_quality("image", dims)
        self.assertEqual(q["overall"], "pass_with_warnings")

    def test_unknown_on_unexpected_dim_no_effect(self):
        # ocr 对 text 不 expected；unknown 不压级（其余 expected 维度全 pass）
        dims = {d: "pass" for d in quality.DIMENSIONS}
        dims["ocr"] = "unknown"
        q = quality.finalize_quality("text", dims)
        self.assertEqual(q["overall"], "pass")

    def test_invalid_worker_value_becomes_unknown(self):
        dims = {"text_extraction": "GARBAGE"}  # worker 输出不可信
        q = quality.finalize_quality("pdf", dims)
        self.assertEqual(q["text_extraction"], "unknown")

    def test_reliably_read(self):
        good = [{"overall": "pass_with_warnings", "fitness": {"fact_extraction": True}}]
        self.assertTrue(quality.reliably_read(good))
        no_fact = [{"overall": "pass", "fitness": {"fact_extraction": False}}]
        self.assertFalse(quality.reliably_read(no_fact))
        fail = [{"overall": "fail", "fitness": {"fact_extraction": True}}]
        self.assertFalse(quality.reliably_read(fail))

    def test_empty_content_cannot_claim_fact_extraction(self):
        fit = quality.default_fitness("text", {"text_extraction": "pass"}, text_chars=0)
        self.assertFalse(fit["fact_extraction"])

    def test_archive_reliably_read_by_status(self):
        self.assertTrue(quality.reliably_read_item("archive", "parsed", []))
        self.assertTrue(quality.reliably_read_item("archive", "parsed_with_warnings", []))
        self.assertFalse(quality.reliably_read_item("archive", "unresolved", []))


class DerivedCoverageTest(unittest.TestCase):
    """派生闭包：内容经派生物提取的原件也算读到。

    否则扫描 PDF → 页位图 → OCR 成功之后，报告仍说"没读到"——
    这是"报告说没读到、其实读到了"，比漏读更误导人。
    """

    class _Item:
        def __init__(self, sid, kind, status, derived_from=None, origin=None):
            self.source_id = sid
            self.kind = kind
            self.status = status
            self.derived_from = derived_from
            self.origin = origin

    OK_Q = [{"overall": "pass", "fitness": {"fact_extraction": True}}]

    def test_transform_parent_counted_via_derived_child(self):
        parent = self._Item("sha256:pdf", "pdf", "parsed")
        child = self._Item("sha256:png", "image", "parsed", derived_from={"source_id": "sha256:pdf"})
        ids = quality.reliably_read_ids(
            [parent, child], lambda sid: self.OK_Q if sid == "sha256:png" else []
        )
        self.assertIn("sha256:pdf", ids)
        self.assertIn("sha256:png", ids)

    def test_multi_level_chain(self):
        """PDF → 页位图 → OCR：多级派生要沿链上溯。"""
        a = self._Item("sha256:a", "pdf", "parsed")
        b = self._Item("sha256:b", "pdf", "parsed", derived_from={"source_id": "sha256:a"})
        c = self._Item("sha256:c", "image", "parsed", derived_from={"source_id": "sha256:b"})
        ids = quality.reliably_read_ids([a, b, c], lambda sid: self.OK_Q if sid == "sha256:c" else [])
        self.assertEqual({"sha256:a", "sha256:b", "sha256:c"}, ids)

    def test_parent_not_counted_when_children_failed(self):
        """派生物没读成，原件也不能算读到——不能把失败洗成成功。"""
        parent = self._Item("sha256:dwg", "dwg", "parsed")
        child = self._Item("sha256:dxf", "dxf", "failed", derived_from={"source_id": "sha256:dwg"})
        ids = quality.reliably_read_ids([parent, child], lambda sid: [])
        self.assertNotIn("sha256:dwg", ids)

    def test_unresolved_parent_never_counted(self):
        parent = self._Item("sha256:x", "pdf", "unresolved")
        child = self._Item("sha256:y", "image", "parsed", derived_from={"source_id": "sha256:x"})
        ids = quality.reliably_read_ids([parent, child], lambda sid: self.OK_Q if sid == "sha256:y" else [])
        self.assertNotIn("sha256:x", ids)


class QualitySpecCoverageGuard(unittest.TestCase):
    """守卫：凡是有 parse 链的 kind，内容类维度必须在 quality spec 的 expected_for 内。

    spec 05 规定 expected_for 的语义是「该 kind 不需要此维度（不压级）」。因此一个有
    parser 的格式若不在 expected_for 里，它的 text_extraction=fail 会被聚合规则**静默丢弃**，
    overall 照样报 pass——正是本项目要防的假装成功。这条守卫让该缺口无法再潜伏。
    """

    CONTENT_DIMS = ("text_extraction", "source_anchoring")

    def _parse_kinds(self):
        from gateway import routing

        return {rule.kind for rule in routing.ROUTE_RULES if rule.action == "parse"}

    def test_every_parsed_kind_is_graded_on_content_dimensions(self):
        gaps = []
        for kind in sorted(self._parse_kinds()):
            expected = quality.expected_dimensions(kind)
            for dim in self.CONTENT_DIMS:
                if dim not in expected:
                    gaps.append(f"{kind}/{dim}")
        self.assertEqual([], gaps, f"这些 kind 有 parser 但该维度不压级，失败会被静默丢弃: {gaps}")

    def test_guard_detects_a_planted_gap(self):
        """负向对照：人为拿掉一个 kind，守卫必须报出来——否则它只是条永远绿的死断言。"""
        spec = quality.load_spec()
        spec["dimensions"]["text_extraction"]["expected_for"] = [
            k for k in spec["dimensions"]["text_extraction"]["expected_for"] if k != "pdf"
        ]
        self.assertNotIn("text_extraction", quality.expected_dimensions("pdf", spec))

    def test_failure_is_never_dropped_for_a_parsed_kind(self):
        """端到端语义：任何有 parser 的 kind，文本提取失败都必须压到 fail。"""
        dims = {"discovery": "pass", "type_detection": "pass", "text_extraction": "fail"}
        for kind in sorted(self._parse_kinds()):
            with self.subTest(kind=kind):
                self.assertEqual("fail", quality.aggregate_overall(kind, dims))


class SingleReliabilityAuthorityGuard(unittest.TestCase):
    """守卫：可靠读取判定只有一个权威入口。

    reliably_read_item 只看 item 自身的 rendition，而 transform 型原件（扫描 PDF→页位图、
    DWG→DXF、视频→抽帧、PST→EML）的内容在派生物里。任何消费点绕过包级的
    reliably_read_ids 直接用 item 级判定，就会与 adg status 给出不同答案——
    本仓库已经因此出过一次 report 说 39、status 说 40 的分歧。
    """

    ALLOWED = {"gateway/quality.py"}  # 定义处与包级实现本身

    def test_only_quality_module_calls_item_level_judgment(self):
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in sorted(root.glob("gateway/**/*.py")) + sorted(root.glob("packs/**/*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in self.ALLOWED or "__pycache__" in rel:
                continue
            text = path.read_text(encoding="utf-8")
            if "reliably_read_item(" in text:
                offenders.append(rel)
        self.assertEqual(
            [], offenders,
            f"这些模块绕过包级判定直接用 item 级 reliably_read_item，会与 adg status 分歧: {offenders}",
        )

    def test_guard_would_catch_a_bypass(self):
        """负向对照：确认这条守卫扫的是真实调用点，而不是永远匹配不到的字符串。"""
        quality_src = (Path(__file__).resolve().parent.parent / "gateway/quality.py").read_text(encoding="utf-8")
        self.assertIn("reliably_read_item(", quality_src, "守卫的匹配串必须真实存在于代码中")


if __name__ == "__main__":
    unittest.main()
