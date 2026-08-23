"""lite parser 单元测试：各格式 fragment 契约 + 诚实降级标注。"""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from gateway import anchor
from gateway.errors import WorkerFailed
from tests.fixtures import (
    make_chat_whatsapp,
    make_docx,
    make_dxf,
    make_eml,
    make_html,
    make_pdf_corrupt,
    make_pdf_digital,
    make_pdf_encrypted,
    make_png,
    make_wav,
    make_xlsx,
)
from packs.legal import chat_lite, csv_lite, doc_lite, docx_lite, dxf_lite, eml_lite, html_lite, image_lite, pdf_lite, pptx_lite, text_lite, xlsx_lite


def _parse(module, data: bytes, name: str, kind: str, fmt: str | None = None) -> dict:
    with tempfile.NamedTemporaryFile(suffix=name, delete=False) as f:
        f.write(data)
        p = Path(f.name)
    try:
        return module.parse(p, kind=kind, format=fmt, options={}, limits={}, work_dir=p.parent)
    finally:
        p.unlink(missing_ok=True)


def _make_minimal_pptx() -> bytes:
    out = io.BytesIO()
    slide = """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
        xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody>
        <a:p><a:r><a:t>标题</a:t></a:r></a:p>
        <a:p><a:r><a:t>正文</a:t></a:r></a:p>
      </p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>"""
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)
    return out.getvalue()


class ParserContractTest(unittest.TestCase):
    def _assert_blocks(self, frag: dict):
        self.assertIn("content", frag)
        self.assertIn("blocks", frag["content"])
        for b in frag["content"]["blocks"]:
            self.assertIn("block_type", b)
            self.assertIn("index", b)
            self.assertIn("elements", b)

    def test_text_lite(self):
        frag = _parse(text_lite, "# 标题\n\n正文段落。".encode("utf-8"), "a.md", "text", "markdown")
        self._assert_blocks(frag)
        self.assertEqual(frag["parser_name"], "text_lite")
        self.assertEqual(frag["quality_dims"]["text_extraction"], "pass")

    def test_csv_lite(self):
        frag = _parse(csv_lite, "a,b\n1,2\n".encode("utf-8"), "a.csv", "csv")
        self._assert_blocks(frag)

    def test_html_lite_marks_fallback(self):
        frag = _parse(html_lite, make_html(), "a.html", "html")
        self._assert_blocks(frag)
        # 兜底标注诚实
        self.assertIn(frag["quality_dims"]["layout_preservation"], ("warning", "fail"))

    def test_docx_lite_comments_and_revisions(self):
        frag = _parse(docx_lite, make_docx(), "a.docx", "docx")
        self._assert_blocks(frag)
        comments = frag.get("annotations", {}).get("comments", [])
        revisions = frag.get("annotations", {}).get("revisions", [])
        self.assertTrue(any("监管账户" in c["text"] for c in comments), comments)
        self.assertTrue(any(r["kind"] == "insert" for r in revisions), revisions)
        self.assertTrue(any(r["kind"] == "delete" for r in revisions), revisions)

    def test_doc_lite_uses_schema_locator(self):
        with patch("packs.legal.doc_lite.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "第一段\n第二段\n"
            run.return_value.stderr = ""
            frag = _parse(doc_lite, b"dummy ole2", "a.doc", "doc")

        self._assert_blocks(frag)
        locators = [element["locator"] for block in frag["content"]["blocks"] for element in block["elements"]]
        self.assertEqual([locator["system"] for locator in locators], ["paragraph", "paragraph"])

    def test_pptx_lite_uses_schema_locator_and_keeps_slide(self):
        frag = _parse(pptx_lite, _make_minimal_pptx(), "a.pptx", "presentation")
        self._assert_blocks(frag)
        elements = [element for block in frag["content"]["blocks"] for element in block["elements"]]
        self.assertEqual([element["locator"] for element in elements], [
            {"system": "paragraph", "index": 0},
            {"system": "paragraph", "index": 1},
        ])
        self.assertEqual([element["attrs"]["slide"] for element in elements], [1, 1])

    def test_xlsx_lite_multi_sheet_and_formula(self):
        frag = _parse(xlsx_lite, make_xlsx(), "a.xlsx", "xlsx")
        self._assert_blocks(frag)
        sheets = [b for b in frag["content"]["blocks"] if b["block_type"] == "sheet"]
        self.assertEqual(len(sheets), 2)
        # 第一表第二行 C2 含公式
        row = sheets[0]["elements"][1]
        cells = row["attrs"]["cells"]
        self.assertTrue(any("formula" in c for c in cells), cells)

    def test_eml_lite_attachments(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.eml"
            p.write_bytes(make_eml())
            frag = eml_lite.parse(p, kind="email", format="eml", options={}, limits={}, work_dir=Path(d))
        self._assert_blocks(frag)
        self.assertTrue(frag["attachments"], "应有附件回灌声明")
        meta = [e["text"] for e in frag["content"]["blocks"][0]["elements"]]
        self.assertTrue(any("from" in m for m in meta), meta)

    def test_pdf_lite_corrupt_fails_honestly(self):
        frag = _parse(pdf_lite, make_pdf_corrupt(), "c.pdf", "pdf")
        # corrupt.pdf 无文本 → text_extraction=fail，且 layout=fail（诚实）
        self.assertEqual(frag["quality_dims"]["text_extraction"], "fail")
        self.assertEqual(frag["quality_dims"]["layout_preservation"], "fail")

    def test_pdf_lite_encrypted_raises_unsupported(self):
        with self.assertRaises(WorkerFailed):
            _parse(pdf_lite, make_pdf_encrypted(), "e.pdf", "pdf")

    def test_pdf_lite_digital_extracts_text(self):
        frag = _parse(pdf_lite, make_pdf_digital(), "d.pdf", "pdf")
        text = "".join(e.get("text", "") for b in frag["content"]["blocks"] for e in b["elements"])
        self.assertIn("digital", text.lower())

    def test_pdf_preflight_classification(self):
        from gateway.selftest import make_pdf_scanned

        with tempfile.TemporaryDirectory() as d:
            for name, data, expect in [
                ("d.pdf", make_pdf_digital(), "digital_simple"),
                ("s.pdf", make_pdf_scanned(), "scanned"),
                ("e.pdf", make_pdf_encrypted(), "encrypted"),
            ]:
                p = Path(d) / name
                p.write_bytes(data)
                feat = pdf_lite.preflight_pdf(p)
                self.assertEqual(feat["pdf_class"], expect, f"{name}: {feat}")

    def test_image_lite_marks_ocr_unknown(self):
        frag = _parse(image_lite, make_png(), "a.png", "image")
        self._assert_blocks(frag)
        self.assertEqual(frag["quality_dims"]["ocr"], "unknown")
        self.assertFalse(frag["fitness"]["fact_extraction"])
        self.assertFalse(frag["fitness"]["quotation"])
        self.assertFalse(frag["fitness"]["layout_sensitive_analysis"])

    def test_dxf_lite_layers_and_text(self):
        frag = _parse(dxf_lite, make_dxf(), "a.dxf", "dxf")
        self._assert_blocks(frag)
        labels = [b["label"] for b in frag["content"]["blocks"]]
        self.assertTrue(any("图层" in l for l in labels), labels)

    def test_chat_lite_whatsapp(self):
        frag = _parse(chat_lite, make_chat_whatsapp(), "a.txt", "chat", chat_lite.detect_chat_format(make_chat_whatsapp().decode("utf-8")[:1000]))
        self._assert_blocks(frag)
        groups = [b for b in frag["content"]["blocks"] if b["block_type"] == "message_group"]
        self.assertGreater(len(groups), 0)
        self.assertGreater(len(groups[0]["elements"]), 0)

    def test_chat_detect_format(self):
        sample = "2024/01/05, 09:30 - 张三: 你好\n2024/01/05, 09:31 - 李四: 收到"
        self.assertEqual(chat_lite.detect_chat_format(sample), "whatsapp")

    def test_wav_metadata_only(self):
        frag = _parse(image_lite if False else __import__("packs.legal.av_lite", fromlist=["parse"]), make_wav(), "a.wav", "audio")
        # av_lite 不做转写（whisper 的职责）：text_extraction=unknown（非 fail），fact_extraction=false
        self.assertEqual(frag["quality_dims"]["text_extraction"], "unknown")
        self.assertFalse(frag["fitness"]["fact_extraction"])


class AnchorFinalizationTest(unittest.TestCase):
    """编排器锚点定稿逻辑的等价校验（不经过完整 dispatcher）。"""

    def test_element_ids_assigned_and_validated(self):
        frag = _parse(docx_lite, make_docx(), "a.docx", "docx")
        # 模拟编排器派生 ID
        config_hash = "test"
        ordinal_map: dict[str, int] = {}
        for b in frag["content"]["blocks"]:
            for el in b["elements"]:
                loc = el["locator"]
                key = anchor.canonical_locator(loc)
                ord_ = ordinal_map.get(key, 0)
                ordinal_map[key] = ord_ + 1
                el["id"] = anchor.derive_element_id("docx_lite", "0.1.0", config_hash, loc, ord_)
        doc = {
            "source_id": "sha256:x",
            "content": frag["content"],
            "annotations": {"comments": [], "revisions": []},
        }
        problems = anchor.validate_document(doc)
        self.assertEqual(problems, [], problems)


if __name__ == "__main__":
    unittest.main()
