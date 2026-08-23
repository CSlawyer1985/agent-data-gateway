#!/usr/bin/env python3
"""OvisOCR2 adapter — 页图到自然阅读顺序 Markdown。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from document_vlm_common import config_fingerprint, markdown_fragment  # noqa: E402

WORKER = {"name": "ovisocr2", "version": "0.1.0", "maturity": "protocol-smoke", "license": "Apache-2.0"}
MODEL_ID = "ATH-MaaS/OvisOCR2"
PROMPT = (
    "Extract all readable content from the image in natural human reading order and output the result as a single "
    "Markdown document. For charts or images, represent them using an HTML image tag: "
    "<img src=\"images/bbox_{left}_{top}_{right}_{bottom}.jpg\" />, where left, top, right, bottom are bounding box "
    "coordinates scaled to [0, 1000). Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
    "Transcribe all other text as standard Markdown. Preserve the original text without translation or paraphrasing."
)


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    options = req.get("options") or {}
    model_id = str(options.get("model", MODEL_ID))
    revision = options.get("revision")
    load_options = {"local_files_only": True}
    if revision:
        load_options["revision"] = str(revision)
    processor = AutoProcessor.from_pretrained(model_id, **load_options)
    model = AutoModelForMultimodalLM.from_pretrained(model_id, device_map="auto", **load_options)
    messages = [{"role": "user", "content": [
        {"type": "image", "url": Path(req["source_path"]).resolve().as_uri()},
        {"type": "text", "text": PROMPT},
    ]}]
    inputs = processor.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt"
    ).to(model.device)
    max_new_tokens = int(options.get("max_new_tokens", 16384))
    outputs = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
    )
    generated = outputs[0][inputs["input_ids"].shape[-1]:]
    markdown = processor.decode(generated, skip_special_tokens=True).strip()
    eos = getattr(model.generation_config, "eos_token_id", None)
    eos_ids = set(eos if isinstance(eos, list) else [eos]) - {None}
    truncated = generated.numel() >= max_new_tokens and (not eos_ids or int(generated[-1]) not in eos_ids)
    commit = getattr(model.config, "_commit_hash", None) or revision or "local-cache"
    fragment = markdown_fragment(
        parser_name=WORKER["name"], parser_version=WORKER["version"], pages=[markdown],
        notes=[f"OvisOCR2 页级端到端解析；模型 {model_id}@{commit}"],
    )
    fragment["model_version"] = f"{model_id}@{commit}"
    fragment["config_hash"] = config_fingerprint({
        "model": model_id,
        "revision": str(commit),
        "max_new_tokens": max_new_tokens,
        "prompt": PROMPT,
    })
    fragment["truncated"] = truncated
    if truncated:
        fragment["warnings"] = ["生成达到 max_new_tokens，结果可能截断；编排器应继续降级链"]
    return {"type": "parse_result", "status": "ok", "fragment": fragment}


def main():
    try:
        req = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, ValueError):
        send({"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": "协议首行不是合法 JSON"}})
        return
    if req.get("type") == "capabilities_request":
        send({"type": "capabilities", "protocol_version": "0.1", "worker": WORKER,
              "handles": [{"kind": "image", "role": "parse"}],
              "requirements": {"min_ram_mb": 8192, "gpu": "optional", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
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
