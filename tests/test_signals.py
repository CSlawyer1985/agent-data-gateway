"""信号探测与利用率（spec 11）：三态纪律、分母构成、PDF 探测器行为。"""

import tempfile
import unittest
from pathlib import Path

from gateway import signals
from gateway.selftest import make_pdf_digital, make_pdf_encrypted, make_pdf_scanned
from packs.legal import register_preflights


class TestThreeStateDiscipline(unittest.TestCase):
    """spec 11 §3.2：拿不准必须 unknown，不得默认 absent。"""

    def test_unprobed_signals_become_unknown(self):
        got = signals.normalize("pdf", {"text_layer": signals.PRESENT})
        self.assertEqual(got["text_layer"], signals.PRESENT)
        # 探测器没报的一律 unknown，而不是 absent
        for name in ("tables", "annotations", "bookmarks"):
            self.assertEqual(got[name], signals.UNKNOWN, name)

    def test_probe_result_is_clamped_to_three_states(self):
        # 探测器返回越界值时不得被当作有效判定
        got = signals.normalize("pdf", {"text_layer": "yes", "page_images": True, "tables": None})
        for name in ("text_layer", "page_images", "tables"):
            self.assertEqual(got[name], signals.UNKNOWN, name)

    def test_kind_without_catalog_has_no_denominator(self):
        self.assertEqual(signals.normalize("executable", {"whatever": "present"}), {})
        self.assertEqual(signals.normalize("unknown", None), {})


class TestUtilizationMath(unittest.TestCase):
    def setUp(self):
        self.sig = {
            "text_layer": signals.PRESENT,
            "page_images": signals.PRESENT,
            "tables": signals.UNKNOWN,
            "bookmarks": signals.ABSENT,
        }

    def test_denominator_is_present_only(self):
        self.assertEqual(sorted(signals.denominator(self.sig)), ["page_images", "text_layer"])

    def test_unknown_excluded_from_both_sides(self):
        self.assertEqual(signals.unknown_signals(self.sig), ["tables"])
        self.assertEqual(signals.utilization(self.sig, {"text_layer", "tables"}), 0.5)

    def test_absent_never_counts(self):
        # 提取了一个 absent 信号也不会让利用率超过 1
        self.assertEqual(signals.utilization(self.sig, {"text_layer", "page_images", "bookmarks"}), 1.0)

    def test_empty_denominator_is_none_not_zero(self):
        """「没有可提取的信号」与「一个都没提出来」不是一回事。"""
        self.assertIsNone(signals.utilization({"tables": signals.UNKNOWN}, set()))
        self.assertEqual(signals.utilization(self.sig, set()), 0.0)

    def test_gaps_are_present_but_unextracted(self):
        self.assertEqual(signals.gaps(self.sig, {"text_layer"}), ["page_images"])
        self.assertEqual(signals.gaps(self.sig, None), ["text_layer", "page_images"])


class TestPdfProbe(unittest.TestCase):
    """PDF 探测器落到真实字节上的行为。"""

    @classmethod
    def setUpClass(cls):
        register_preflights()

    def _probe(self, raw: bytes) -> dict:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            path = Path(f.name)
        try:
            return signals.probe("pdf", path)
        finally:
            path.unlink(missing_ok=True)

    def test_digital_pdf_has_text_layer_and_no_images(self):
        got = self._probe(make_pdf_digital())
        self.assertEqual(got["text_layer"], signals.PRESENT)
        self.assertEqual(got["page_images"], signals.ABSENT)

    def test_scanned_pdf_is_the_inverse(self):
        """ADR 0002 的原型场景：文字层缺席、图像在场——利用率缺口就在这里。"""
        got = self._probe(make_pdf_scanned())
        self.assertEqual(got["text_layer"], signals.ABSENT)
        self.assertEqual(got["page_images"], signals.PRESENT)

    def test_encrypted_pdf_admits_it_knows_nothing(self):
        got = self._probe(make_pdf_encrypted())
        self.assertTrue(all(state == signals.UNKNOWN for state in got.values()), got)
        self.assertEqual(signals.denominator(got), [])

    def test_tables_are_never_claimed(self):
        """PDF 没有表格的结构标记，粗检无从判定，任何样本都不得判 present/absent。"""
        for raw in (make_pdf_digital(), make_pdf_scanned()):
            self.assertEqual(self._probe(raw)["tables"], signals.UNKNOWN)

    def test_object_streams_force_unknown_not_absent(self):
        """spec 11 §七 判据②：探测不确定不得虚高利用率。

        PDF 1.5+ 把字典塞进对象流压缩，正则搜不到不代表不存在。同一份样本
        仅仅因为出现 /ObjStm，未命中的信号就必须从 absent 退让为 unknown。
        """
        plain = self._probe(make_pdf_digital())
        self.assertEqual(plain["bookmarks"], signals.ABSENT)

        with_objstm = self._probe(make_pdf_digital() + b"\n/ObjStm\n")
        self.assertEqual(with_objstm["bookmarks"], signals.UNKNOWN)
        # 分母只会缩小或持平，绝不会因为不确定而变大
        self.assertLessEqual(len(signals.denominator(with_objstm)), len(signals.denominator(plain)))


if __name__ == "__main__":
    unittest.main()
