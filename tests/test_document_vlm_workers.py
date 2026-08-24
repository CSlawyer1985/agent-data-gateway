from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from workers.document_vlm_common import config_fingerprint, markdown_fragment
from workers.ovisocr2.adapter import (
    _clean_truncated_repeats,
    _image_content,
    _mark_incomplete_generation,
    _repeated_token_tail,
)
from workers.paddleocr_vl.adapter import _markdown_text


ROOT = Path(__file__).resolve().parent.parent


class DocumentVlmFragmentTest(unittest.TestCase):
    def test_markdown_output_is_page_anchored_but_not_fake_bbox(self) -> None:
        fragment = markdown_fragment(
            parser_name="probe", parser_version="1", pages=["# 标题", "第二页"], notes=[]
        )
        blocks = fragment["content"]["blocks"]
        self.assertEqual([0, 1], [block["index"] for block in blocks])
        self.assertEqual({"system": "page_bbox", "page": 0}, blocks[0]["elements"][0]["locator"])
        self.assertEqual("warning", fragment["quality_dims"]["layout_preservation"])
        self.assertFalse(fragment["fitness"]["layout_sensitive_analysis"])

    def test_image_placeholder_is_not_claimed_as_extracted_body_text(self) -> None:
        fragment = markdown_fragment(
            parser_name="probe",
            parser_version="1",
            pages=['# 正确标题\n\n<div><img src="imgs/page-body.jpg" alt="Image" /></div>'],
            notes=[],
        )
        self.assertEqual("warning", fragment["quality_dims"]["text_extraction"])
        self.assertEqual("warning", fragment["quality_dims"]["ocr"])
        self.assertTrue(fragment["fitness"]["fulltext_search"])
        self.assertFalse(fragment["fitness"]["fact_extraction"])
        self.assertFalse(fragment["fitness"]["quotation"])

    def test_paddle_result_reads_current_markdown_shape(self) -> None:
        result = type("Result", (), {"markdown": {"markdown_texts": ["第一页", "续段"]}})()
        self.assertEqual("第一页\n\n续段", _markdown_text(result))

    def test_config_fingerprint_is_order_independent_and_sensitive(self) -> None:
        self.assertEqual(config_fingerprint({"a": 1, "b": 2}), config_fingerprint({"b": 2, "a": 1}))
        self.assertNotEqual(config_fingerprint({"a": 1}), config_fingerprint({"a": 2}))

    def test_ovis_uses_local_path_instead_of_file_uri(self) -> None:
        source = ROOT / "tests" / "fixture image.png"
        content = _image_content(str(source))
        self.assertEqual(str(source.resolve()), content["url"])
        self.assertFalse(content["url"].startswith("file://"))

    def test_ovis_cleans_degenerate_repeated_tail(self) -> None:
        # 真实中文长图回归：JSON 字节很多，但 Python 字符数可能低于官方 8000 门槛。
        prefix = "正文内容-" * 500
        repeated = "法院应当认定担保合同无效。"
        cleaned, detected = _clean_truncated_repeats(prefix + repeated * 372)
        self.assertTrue(detected)
        self.assertEqual(prefix + repeated, cleaned)

    def test_ovis_keeps_short_legitimate_repetition(self) -> None:
        text = "同意。" * 4
        self.assertEqual((text, False), _clean_truncated_repeats(text))

    def test_ovis_repetition_cannot_remain_fact_quality(self) -> None:
        fragment = markdown_fragment(parser_name="ovisocr2", parser_version="1", pages=["正文"], notes=[])
        _mark_incomplete_generation(fragment, token_limit_reached=False, repeated_output=True)
        self.assertTrue(fragment["truncated"])
        self.assertEqual("warning", fragment["quality_dims"]["text_extraction"])
        self.assertEqual("warning", fragment["quality_dims"]["ocr"])
        self.assertFalse(fragment["fitness"]["fact_extraction"])
        self.assertTrue(any("重复循环" in warning for warning in fragment["warnings"]))

    def test_ovis_token_loop_is_detected_before_generation_budget_is_exhausted(self) -> None:
        period = [7, 8, 9, 10]
        self.assertEqual((4, 30), _repeated_token_tail(list(range(300)) + period * 30))
        self.assertIsNone(_repeated_token_tail(period * 4))

    def test_library_stdout_is_kept_out_of_jsonl_channel(self) -> None:
        script = (
            "from workers.stdio_protocol import library_output_to_stderr\n"
            "with library_output_to_stderr():\n"
            "    print('third-party noise', flush=True)\n"
            "print('{\"type\":\"parse_result\"}', flush=True)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, text=True, capture_output=True, timeout=10, check=True
        )
        self.assertEqual('{"type":"parse_result"}\n', proc.stdout)
        self.assertIn("third-party noise", proc.stderr)


class AdapterCapabilitiesTest(unittest.TestCase):
    def test_new_adapters_answer_without_importing_heavy_models(self) -> None:
        expected = {
            "paddleocr_vl": {"pdf", "image"},
            "ovisocr2": {"image"},
        }
        request = json.dumps({"type": "capabilities_request", "protocol_version": "0.1"}) + "\n"
        for name, kinds in expected.items():
            with self.subTest(worker=name):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "workers" / name / "adapter.py")],
                    input=request, text=True, capture_output=True, timeout=10, check=True,
                )
                response = json.loads(proc.stdout)
                self.assertEqual("capabilities", response["type"])
                self.assertEqual(kinds, {handle["kind"] for handle in response["handles"]})

    def test_unvalidated_models_do_not_claim_implemented_maturity(self) -> None:
        for name in ("paddleocr_vl", "ovisocr2"):
            with self.subTest(worker=name):
                manifest = json.loads((ROOT / "workers" / name / "worker.json").read_text(encoding="utf-8"))
                self.assertEqual("protocol-smoke", manifest["maturity"])

    def test_ovis_declares_torchvision_runtime_dependency(self) -> None:
        manifest = json.loads((ROOT / "workers" / "ovisocr2" / "worker.json").read_text(encoding="utf-8"))
        self.assertIn("torchvision", manifest["python_requires"])


if __name__ == "__main__":
    unittest.main()
