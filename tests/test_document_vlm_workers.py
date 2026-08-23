from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from workers.document_vlm_common import config_fingerprint, markdown_fragment
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

    def test_paddle_result_reads_current_markdown_shape(self) -> None:
        result = type("Result", (), {"markdown": {"markdown_texts": ["第一页", "续段"]}})()
        self.assertEqual("第一页\n\n续段", _markdown_text(result))

    def test_config_fingerprint_is_order_independent_and_sensitive(self) -> None:
        self.assertEqual(config_fingerprint({"a": 1, "b": 2}), config_fingerprint({"b": 2, "a": 1}))
        self.assertNotEqual(config_fingerprint({"a": 1}), config_fingerprint({"a": 2}))


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


if __name__ == "__main__":
    unittest.main()
