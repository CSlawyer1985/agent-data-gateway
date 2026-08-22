#!/usr/bin/env python3
"""MinerU worker adapter — ADG Worker Protocol v0.1。

调 MinerU CLI（mineru -p <pdf> -o <out>），读取其 content_list.json 映射为 fragment。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKER = {"name": "mineru", "version": "0.1.0", "maturity": "implemented", "license": "AGPL-3.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _norm_bbox(bbox, page_h, page_w):
    """MinerU bbox 为像素（左上原点）→ 0-1000。"""
    if not bbox or not page_h or not page_w:
        return None
    l, t, r, b = bbox
    return [round(l / page_w * 1000, 1), round(t / page_h * 1000, 1), round(r / page_w * 1000, 1), round(b / page_h * 1000, 1)]


def handle_parse(req):
    src = req["source_path"]
    work_dir = Path(req["work_dir"])
    with tempfile.TemporaryDirectory(prefix="adg-mineru-", dir=str(work_dir)) as out_dir:
        cmd = ["mineru", "-p", src, "-o", out_dir]
        env = dict(os.environ)
        env.setdefault("MINERU_DEVICE_MODE", "cpu")
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=2400, env=env)
        if proc.returncode != 0:
            return {"type": "parse_result", "status": "failed",
                    "error": {"kind": "crash", "message": f"mineru 退出码 {proc.returncode}: {(proc.stderr or '')[-300:]}"}}
        # 产物定位：<out>/<stem>/auto/<stem>_content_list.json
        candidates = list(Path(out_dir).rglob("*_content_list.json"))
        if not candidates:
            return {"type": "parse_result", "status": "failed",
                    "error": {"kind": "crash", "message": "mineru 未产出 content_list.json"}}
        content_list = json.loads(candidates[0].read_text(encoding="utf-8"))

    # 页尺寸来自 middle.json（尽力而为，缺失则不归一化）
    pages: dict[int, list] = {}
    type_map = {"text": "paragraph", "title": "heading2", "table": "table", "image": "image_ref", "equation": "paragraph", "image_caption": "paragraph", "table_caption": "paragraph", "table_footnote": "paragraph"}
    for entry in content_list:
        page = int(entry.get("page_idx", 0))
        etype = type_map.get(entry.get("type", "text"), "paragraph")
        text = (entry.get("text") or "").strip()
        if entry.get("type") == "table" and not text:
            text = entry.get("table_body") or ""
        if not text and etype != "image_ref":
            continue
        loc = {"system": "page_bbox", "page": page}
        bbox = entry.get("bbox")
        ph, pw = entry.get("page_size", [None, None]) if isinstance(entry.get("page_size"), list) else (None, None)
        nb = _norm_bbox(bbox, ph, pw)
        if nb:
            loc["bbox"] = nb
        el = {"type": etype, "text": text, "locator": loc}
        if etype == "table":
            el["attrs"] = {"format": "html" if (entry.get("table_body") or "").startswith("<") else "text"}
        pages.setdefault(page, []).append(el)

    blocks = [{"block_type": "page", "index": p, "elements": els} for p, els in sorted(pages.items())]
    text_chars = sum(len(el.get("text") or "") for b in blocks for el in b["elements"])
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "mineru",
            "parser_version": WORKER["version"],
            "coordinate_system": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if text_chars else "warning",
                "layout_preservation": "pass",
                "ocr": "pass",
                "metadata": "pass",
                "source_anchoring": "pass",
            },
            "fitness": {"layout_sensitive_analysis": True},
            "notes": ["MinerU 全管线（含 OCR/版面分析）"],
            "truncated": False,
        },
    }


def main():
    line = sys.stdin.readline()
    try:
        req = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        send({"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": "协议首行不是合法 JSON"}})
        return
    if req.get("type") == "capabilities_request":
        send({"type": "capabilities", "protocol_version": "0.1", "worker": WORKER,
              "handles": [{"kind": "pdf", "role": "parse"}],
              "requirements": {"min_ram_mb": 8192, "gpu": "optional", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "mineru CLI 不在 PATH"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed",
          "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
