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


if __name__ == "__main__":
    unittest.main()
