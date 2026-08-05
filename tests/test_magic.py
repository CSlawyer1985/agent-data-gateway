"""类型识别（magic）单元测试。"""

import unittest

from gateway import magic
from tests.fixtures import (
    make_docx,
    make_dxf,
    make_eml,
    make_html,
    make_jpeg_exif,
    make_pdf_corrupt,
    make_pdf_digital,
    make_pdf_encrypted,
    make_pdf_scanned,
    make_png,
    make_wav,
    make_xlsx,
    make_zip_bomb,
    make_zip_normal,
    make_zip_traversal,
)


class MagicTest(unittest.TestCase):
    def _detect_bytes(self, data: bytes, name: str = "x") -> magic.DetectedType:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=name, delete=False) as f:
            f.write(data)
            p = Path(f.name)
        try:
            return magic.detect(p)
        finally:
            p.unlink(missing_ok=True)

    def test_pdf_digital(self):
        d = self._detect_bytes(make_pdf_digital(), "a.pdf")
        self.assertEqual(d.kind, magic.PDF)
        self.assertEqual(d.confidence, "high")

    def test_pdf_scanned(self):
        d = self._detect_bytes(make_pdf_scanned(), "a.pdf")
        self.assertEqual(d.kind, magic.PDF)

    def test_docx_by_ooxml_parts(self):
        d = self._detect_bytes(make_docx(), "a.docx")
        self.assertEqual(d.kind, magic.DOCX)
        self.assertEqual(d.confidence, "high")

    def test_xlsx_by_ooxml_parts(self):
        d = self._detect_bytes(make_xlsx(), "a.xlsx")
        self.assertEqual(d.kind, magic.XLSX)

    def test_png(self):
        self.assertEqual(self._detect_bytes(make_png(), "a.png").kind, magic.IMAGE)

    def test_jpeg_exif(self):
        d = self._detect_bytes(make_jpeg_exif(), "a.jpg")
        self.assertEqual(d.kind, magic.IMAGE)

    def test_wav(self):
        self.assertEqual(self._detect_bytes(make_wav(), "a.wav").kind, magic.AUDIO)

    def test_zip(self):
        self.assertEqual(self._detect_bytes(make_zip_normal(), "a.zip").kind, magic.ARCHIVE)

    def test_eml_by_header_pattern(self):
        d = self._detect_bytes(make_eml(), "a.eml")
        self.assertEqual(d.kind, magic.EMAIL)

    def test_dxf_by_sniff(self):
        d = self._detect_bytes(make_dxf(), "a.dxf")
        self.assertEqual(d.kind, magic.DXF)

    def test_html_by_sniff(self):
        d = self._detect_bytes(make_html(), "a.html")
        self.assertEqual(d.kind, magic.HTML)

    def test_renamed_pdf_uses_magic_not_extension(self):
        """改名 PDF（.docx 扩展名）仍按魔数识别为 PDF。"""
        d = self._detect_bytes(make_pdf_digital(), "report.docx")
        self.assertEqual(d.kind, magic.PDF)

    def test_traversal_zip_still_archive(self):
        self.assertEqual(self._detect_bytes(make_zip_traversal(), "t.zip").kind, magic.ARCHIVE)

    def test_corrupt_pdf_still_pdf_magic(self):
        """损坏 PDF（有 %PDF 头）仍识别为 PDF，由预检/解析判定质量。"""
        d = self._detect_bytes(make_pdf_corrupt(), "c.pdf")
        self.assertEqual(d.kind, magic.PDF)

    def test_encrypted_pdf_detected(self):
        from gateway.security import detect_pdf_encrypted

        self.assertTrue(detect_pdf_encrypted(make_pdf_encrypted()))


if __name__ == "__main__":
    unittest.main()
