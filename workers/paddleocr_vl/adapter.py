#!/usr/bin/env python3
"""PaddleOCR-VL-1.6 adapter — PDF/图片到逐页 Markdown fragment。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from document_vlm_common import config_fingerprint, markdown_fragment  # noqa: E402
from stdio_protocol import library_output_to_stderr  # noqa: E402

WORKER = {"name": "paddleocr_vl", "version": "0.1.0", "maturity": "protocol-smoke", "license": "Apache-2.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _markdown_text(result) -> str:
    payload = result.markdown
    value = payload.get("markdown_texts", payload.get("text", "")) if isinstance(payload, dict) else payload
    if isinstance(value, list):
        return "\n\n".join(str(part) for part in value if part)
    return str(value or "")


def handle_parse(req):
    from paddleocr import PaddleOCRVL

    options = req.get("options") or {}
    pipeline_version = str(options.get("pipeline_version", "v1.6"))
    device = str(options.get("device", "cpu"))
    engine = options.get("engine")
    kwargs = {"pipeline_version": pipeline_version, "device": device}
    if engine:
        kwargs["engine"] = str(engine)
    pipeline = PaddleOCRVL(**kwargs)
    pages = [_markdown_text(result) for result in pipeline.predict(input=req["source_path"])]
    fragment = markdown_fragment(
        parser_name=WORKER["name"],
        parser_version=WORKER["version"],
        pages=pages,
        notes=[f"PaddleOCR-VL {pipeline_version}；设备 {device}；逐页 Markdown"],
    )
    fragment["model_version"] = f"PaddleOCR-VL:{pipeline_version}"
    fragment["config_hash"] = config_fingerprint({
        "pipeline_version": pipeline_version,
        "device": device,
        "engine": str(engine) if engine else None,
    })
    return {"type": "parse_result", "status": "ok", "fragment": fragment}


def main():
    try:
        req = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, ValueError):
        send({"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": "协议首行不是合法 JSON"}})
        return
    if req.get("type") == "capabilities_request":
        send({"type": "capabilities", "protocol_version": "0.1", "worker": WORKER,
              "handles": [{"kind": "pdf", "role": "parse"}, {"kind": "image", "role": "parse"}],
              "requirements": {"min_ram_mb": 8192, "gpu": "optional", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            with library_output_to_stderr():
                result = handle_parse(req)
        except ImportError as exc:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "dependency_missing", "message": str(exc)}}
        except Exception as exc:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(exc).__name__}: {exc}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
