from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from workers.paddleocr.adapter import handle_parse as paddleocr_parse
from workers.rapidocr.adapter import handle_parse as rapidocr_parse


class _ImageFile:
    size = (100, 200)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ImageApi:
    @staticmethod
    def open(_path):
        return _ImageFile()


def _pil_module():
    module = types.ModuleType("PIL")
    module.Image = _ImageApi
    return module


class OcrAdapterTest(unittest.TestCase):
    def test_paddleocr_v3_result_shape_is_supported(self):
        class Result:
            json = {
                "res": {
                    "rec_texts": ["正确文本"],
                    "rec_scores": [0.95],
                    "rec_polys": [[[10, 20], [30, 20], [30, 40], [10, 40]]],
                }
            }

        class PaddleOCR:
            def __init__(self, **kwargs):
                if "show_log" in kwargs:
                    raise ValueError("Unknown argument: show_log")

            def predict(self, _path):
                return [Result()]

        paddle = types.ModuleType("paddleocr")
        paddle.PaddleOCR = PaddleOCR
        with mock.patch.dict(sys.modules, {"paddleocr": paddle, "PIL": _pil_module()}):
            fragment = paddleocr_parse({"source_path": "/tmp/page.png", "options": {}})["fragment"]

        element = fragment["content"]["blocks"][0]["elements"][0]
        self.assertEqual("正确文本", element["text"])
        self.assertEqual([100.0, 100.0, 300.0, 200.0], element["locator"]["bbox"])
        self.assertEqual(0.95, element["attrs"]["ocr_confidence"])

    def test_majority_low_confidence_ocr_is_not_fact_quality(self):
        class RapidOCR:
            def __call__(self, _path):
                rows = [
                    ([[0, 0], [10, 0], [10, 10], [0, 10]], "高", 0.95),
                    ([[0, 20], [10, 20], [10, 30], [0, 30]], "低一", 0.70),
                    ([[0, 40], [10, 40], [10, 50], [0, 50]], "低二", 0.60),
                ]
                return rows, None

        rapid = types.ModuleType("rapidocr_onnxruntime")
        rapid.RapidOCR = RapidOCR
        with mock.patch.dict(sys.modules, {"rapidocr_onnxruntime": rapid, "PIL": _pil_module()}):
            fragment = rapidocr_parse({"source_path": "/tmp/page.png"})["fragment"]

        self.assertEqual("warning", fragment["quality_dims"]["text_extraction"])
        self.assertEqual("warning", fragment["quality_dims"]["ocr"])


if __name__ == "__main__":
    unittest.main()
