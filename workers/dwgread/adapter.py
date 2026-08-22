#!/usr/bin/env python3
"""dwgread worker adapter — ADG Worker Protocol v0.1。DWG 直读（parse）。

为什么不走 dwg2dxf 中转：实测 LibreDWG 转出的 DXF 常结构性损坏（句柄重复、缺 ENDBLK、
BLOCK_RECORD 类型错），连 ezdxf.recover 都拒绝加载；而 `dwgread -O JSON` 直接读原始 DWG，
图纸文字与图层完好。工程纠纷里图纸的证据价值多在标注、图签、说明文字上。

锚点：entity(handle, layer)，与 dxf_lite / ezdxf 一致（spec 07）。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

WORKER = {"name": "dwgread", "version": "0.1.0", "maturity": "implemented", "license": "GPL-3.0"}

DEFAULT_MAX_JSON_MB = 512
DEFAULT_MAX_TEXTS = 20000

# MTEXT 内联格式码：\f字体; \W宽度; \H字高; \P换行 等，以及成对花括号
_MTEXT_CMD = re.compile(r"\\[A-Za-z][^;\\]*;")
_MTEXT_ESC = re.compile(r"\\[{}\\]")


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle_value(raw):
    """LibreDWG 句柄形如 [code, size, value, absolute]；取可比较的数值。"""
    if isinstance(raw, list) and raw:
        return raw[-1]
    return raw


def _mtext_plain(text: str) -> str:
    """MTEXT 去格式码 → 纯文本。"""
    if not text:
        return ""
    out = text.replace("\\P", "\n").replace("\\~", " ")
    out = _MTEXT_CMD.sub("", out)
    out = _MTEXT_ESC.sub(lambda m: m.group(0)[1], out)
    return out.replace("{", "").replace("}", "").strip()


def handle_parse(req):
    work_dir = Path(req["work_dir"])
    options = req.get("options") or {}
    max_json_mb = int(options.get("max_json_mb", DEFAULT_MAX_JSON_MB))
    max_texts = int(options.get("max_texts", DEFAULT_MAX_TEXTS))
    out_json = work_dir / "dwg.json"

    proc = subprocess.run(
        ["dwgread", "-O", "JSON", "-o", str(out_json), req["source_path"]],
        capture_output=True, text=True, errors="replace", timeout=1800,
    )
    if not out_json.exists():
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash",
                          "message": f"dwgread 退出码 {proc.returncode}: {(proc.stderr or '')[-300:]}"}}
    size_mb = out_json.stat().st_size / (1024 * 1024)
    if size_mb > max_json_mb:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "resource_limit",
                          "message": f"dwgread JSON {size_mb:.0f}MB 超过上限 {max_json_mb}MB"
                                     f"（--worker-option max_json_mb=N 调整）"}}

    with open(out_json, encoding="utf-8", errors="replace") as f:
        doc = json.load(f)
    objects = doc.get("OBJECTS") or []

    layer_names: dict = {}
    for o in objects:
        if o.get("object") == "LAYER" and o.get("name"):
            layer_names[_handle_value(o.get("handle"))] = o["name"]

    elements: list[dict] = []
    counts: dict[str, int] = {}
    truncated = False
    for o in objects:
        etype = o.get("entity")
        if not etype:
            continue
        counts[etype] = counts.get(etype, 0) + 1
        if etype not in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            continue
        raw = o.get("text_value") if etype in ("TEXT", "ATTRIB", "ATTDEF") else o.get("text")
        text = _mtext_plain(raw) if etype == "MTEXT" else (raw or "").strip()
        if not text:
            continue
        if len(elements) >= max_texts:
            truncated = True
            break
        layer_handle = _handle_value(o.get("layer"))
        elements.append({
            "type": "entity",
            "text": text,
            "locator": {
                "system": "entity",
                "handle": str(_handle_value(o.get("handle"))),
                "layer": layer_names.get(layer_handle, str(layer_handle)),
            },
            "attrs": {"entity_type": etype},
        })

    header = doc.get("HEADER") or {}
    meta: list[dict] = []
    for key in ("EXTMIN", "EXTMAX"):
        val = header.get(key)
        if isinstance(val, list) and len(val) >= 3:
            meta.append({"type": "field",
                         "text": f"{key}: ({val[0]:.3f}, {val[1]:.3f}, {val[2]:.3f})",
                         "locator": {"system": "field", "name": key}})
    meta.append({"type": "field", "text": f"entities_total: {sum(counts.values())}",
                 "locator": {"system": "field", "name": "entities_total"}})
    meta.append({"type": "field", "text": f"text_entities: {len(elements)}",
                 "locator": {"system": "field", "name": "text_entities"}})
    if doc.get("created_by"):
        meta.append({"type": "field", "text": f"created_by: {doc['created_by']}",
                     "locator": {"system": "field", "name": "created_by"}})

    blocks = [
        {"block_type": "section", "index": 0, "label": "元数据", "elements": meta},
        {"block_type": "section", "index": 1, "label": "图层",
         "elements": [{"type": "field", "text": n, "locator": {"system": "field", "name": f"layer_{i}"}}
                      for i, n in enumerate(sorted(set(layer_names.values()))[:512])]},
        {"block_type": "section", "index": 2, "label": "实体统计",
         "elements": [{"type": "field", "text": f"{t}: {c}", "locator": {"system": "field", "name": f"entity_{t}"}}
                      for t, c in sorted(counts.items(), key=lambda kv: -kv[1])[:60]]},
        {"block_type": "section", "index": 3, "label": "图纸文字", "elements": elements},
    ]
    notes = ["dwgread 直读 DWG（未经 DXF 中转，避免转换损坏）；几何未提取，仅文字/图层/范围"]
    if truncated:
        notes.append(f"文字实体超过上限 {max_texts}，已截断")
    if proc.stderr and proc.returncode != 0:
        notes.append(f"dwgread 有告警（退出码 {proc.returncode}），内容可能不完整")

    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if elements else "warning",
        "layout_preservation": "warning",
        "metadata": "pass",
        "source_anchoring": "pass",
    }
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "dwgread",
            "parser_version": WORKER["version"],
            "operation": "parse",
            "coordinate_system": {"name": "entity", "units": "drawing", "origin": "wcs"},
            "content": {"blocks": blocks},
            "quality_dims": dims,
            "fitness": {"layout_sensitive_analysis": False},
            "notes": notes,
            "truncated": truncated,
        },
        "metrics": {"objects": len(objects), "text_entities": len(elements),
                    "layers": len(layer_names), "json_mb": round(size_mb, 1)},
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
              "handles": [{"kind": "dwg", "role": "parse"}],
              "requirements": {"min_ram_mb": 2048, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "dwgread 不在 PATH（brew install libredwg）"}}
        except MemoryError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "resource_limit", "message": "DWG JSON 过大，内存不足（调小 max_json_mb 或改走 dwg_render）"}}
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
