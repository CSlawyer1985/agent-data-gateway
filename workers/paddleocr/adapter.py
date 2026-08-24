#!/usr/bin/env python3
"""paddleocr worker adapter — ADG Worker Protocol v0.1。

OCR 文本框 → ocr_boxes 轨 + page_bbox 元素（像素坐标归一化 0-1000）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ocr_common import confidence_quality  # noqa: E402
from stdio_protocol import library_output_to_stderr  # noqa: E402

WORKER = {"name": "paddleocr", "version": "0.1.0", "maturity": "implemented", "license": "Apache-2.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    from paddleocr import PaddleOCR
    from PIL import Image

    options = req.get("options") or {}
    lang = options.get("language", "ch")
    if callable(getattr(PaddleOCR, "predict", None)):
        ocr = PaddleOCR(
            lang=lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        lines = _v3_lines(ocr.predict(req["source_path"]))
    else:
        ocr = PaddleOCR(use_angle_cls=False, lang=lang, show_log=False)
        result = ocr.ocr(req["source_path"], cls=False)
        lines = result[0] if result and result[0] else []

    with Image.open(req["source_path"]) as im:
        img_w, img_h = im.size

    ocr_boxes = []
    elements = []
    for i, line in enumerate(lines):
        box, (text, conf) = line[0], line[1]
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        bbox = [
            round(min(xs) / img_w * 1000, 1),
            round(min(ys) / img_h * 1000, 1),
            round(max(xs) / img_w * 1000, 1),
            round(max(ys) / img_h * 1000, 1),
        ]
        ocr_boxes.append({"text": text, "bbox": bbox, "confidence": round(float(conf), 4), "page_index": 0})
        elements.append({
            "type": "paragraph",
            "text": text,
            "locator": {"system": "page_bbox", "page": 0, "bbox": bbox},
            "attrs": {"ocr_confidence": round(float(conf), 4)},
        })

    ocr_quality, low_conf, majority_low = confidence_quality(ocr_boxes)
    blocks = [{"block_type": "page", "index": 0, "elements": elements}]
    notes = [f"OCR 文本框 {len(ocr_boxes)} 个；低置信（<0.8）{low_conf} 个", "OCR 有损：引用前需对照原件核验"]
    if majority_low:
        notes.append("多数文本框低置信；仅适合粗略检索，不能作为可靠事实提取")
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "paddleocr",
            "parser_version": WORKER["version"],
            "operation": "ocr",
            "coordinate_system": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
            "content": {"blocks": blocks},
            "annotations": {"ocr_boxes": ocr_boxes},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": ocr_quality,
                "layout_preservation": "pass",
                "ocr": ocr_quality,
                "metadata": "pass",
                "source_anchoring": "pass",
            },
            "notes": notes,
            "truncated": False,
        },
    }


def _v3_lines(results):
    lines = []
    for result in results or []:
        payload = getattr(result, "json", result)
        if callable(payload):
            payload = payload()
        if not isinstance(payload, dict):
            continue
        payload = payload.get("res", payload)
        texts = payload.get("rec_texts")
        scores = payload.get("rec_scores")
        polygons = payload.get("rec_polys")
        if polygons is None:
            polygons = payload.get("dt_polys")
        if texts is None or scores is None or polygons is None:
            continue
        for polygon, text, score in zip(polygons, texts, scores):
            if hasattr(polygon, "tolist"):
                polygon = polygon.tolist()
            lines.append([polygon, (str(text), float(score))])
    return lines


def main():
    line = sys.stdin.readline()
    try:
        req = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        send({"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": "协议首行不是合法 JSON"}})
        return
    if req.get("type") == "capabilities_request":
        send({"type": "capabilities", "protocol_version": "0.1", "worker": WORKER,
              "handles": [{"kind": "image", "role": "parse"}],
              "requirements": {"min_ram_mb": 4096, "gpu": "optional", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            with library_output_to_stderr():
                result = handle_parse(req)
        except ImportError as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "dependency_missing", "message": str(e)}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
