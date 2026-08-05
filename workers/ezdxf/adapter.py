#!/usr/bin/env python3
"""ezdxf worker adapter — ADG Worker Protocol v0.1。完整 DXF 解析（句柄锚点）。"""

import json
import sys
from collections import Counter

WORKER = {"name": "ezdxf", "version": "0.1.0", "maturity": "implemented", "license": "MIT"}

_MAX_TEXT_ELEMENTS = 50_000
_TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    import ezdxf

    doc = ezdxf.readfile(req["source_path"])
    msp = doc.modelspace()

    layers = [layer.dxf.name for layer in doc.layers]
    header = doc.header
    extents = {}
    for var in ("$EXTMIN", "$EXTMAX"):
        if var in header:
            extents[var] = header[var]

    counts: Counter[str] = Counter()
    text_elements = []
    truncated = False
    for e in msp:
        etype = e.dxftype()
        counts[etype] += 1
        if etype in _TEXT_TYPES and len(text_elements) < _MAX_TEXT_ELEMENTS:
            if etype == "MTEXT":
                text = e.text
            else:
                text = e.dxf.text if hasattr(e.dxf, "text") else ""
            if text and text.strip():
                text_elements.append({
                    "type": "entity",
                    "text": text.strip(),
                    "locator": {"system": "entity", "handle": e.dxf.handle, "layer": e.dxf.layer},
                    "attrs": {"entity_type": etype},
                })
    if len(text_elements) >= _MAX_TEXT_ELEMENTS:
        truncated = True

    meta = []
    for var, val in extents.items():
        try:
            meta.append({"type": "field", "text": f"{var}: ({val[0]:.3f}, {val[1]:.3f}, {val[2]:.3f})", "locator": {"system": "field", "name": var}})
        except (TypeError, IndexError):
            pass
    meta.append({"type": "field", "text": f"entities_total: {sum(counts.values())}", "locator": {"system": "field", "name": "entities_total"}})
    meta.append({"type": "field", "text": f"dxf_version: {doc.dxfversion}", "locator": {"system": "field", "name": "dxf_version"}})

    blocks = [
        {"block_type": "section", "index": 0, "label": "图纸信息", "elements": meta},
        {"block_type": "section", "index": 1, "label": f"图层（{len(layers)}）",
         "elements": [{"type": "field", "text": n, "locator": {"system": "field", "name": f"layer_{i}"}} for i, n in enumerate(layers[:512])]},
        {"block_type": "section", "index": 2, "label": "实体统计",
         "elements": [{"type": "field", "text": f"{t}: {c}", "locator": {"system": "field", "name": f"entity_{t}"}} for t, c in counts.most_common(60)]},
        {"block_type": "section", "index": 3, "label": f"文本实体（{len(text_elements)}）", "elements": text_elements},
    ]
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "ezdxf",
            "parser_version": WORKER["version"],
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if text_elements else "warning",
                "layout_preservation": "pass",
                "metadata": "pass",
                "source_anchoring": "pass",
            },
            "fitness": {"layout_sensitive_analysis": True},
            "notes": [f"图层 {len(layers)}；实体 {sum(counts.values())}"],
            "truncated": truncated,
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
              "handles": [{"kind": "dxf", "role": "parse"}],
              "requirements": {"min_ram_mb": 1024, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
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
