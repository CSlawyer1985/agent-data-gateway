"""质量模型聚合 + fitness（spec 05）。"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
