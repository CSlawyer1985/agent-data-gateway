#!/usr/bin/env python3
"""OvisOCR2 adapter — 页图到自然阅读顺序 Markdown。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from document_vlm_common import config_fingerprint, markdown_fragment  # noqa: E402
from stdio_protocol import library_output_to_stderr  # noqa: E402

WORKER = {"name": "ovisocr2", "version": "0.1.0", "maturity": "protocol-smoke", "license": "Apache-2.0"}
MODEL_ID = "ATH-MaaS/OvisOCR2"
TAIL_REPEAT_MAX_PERIOD = 200
TAIL_REPEAT_MIN_TOKENS = 100
TAIL_REPEAT_MIN_TIMES = 5
TAIL_REPEAT_CHECK_INTERVAL = 16
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


def _image_content(source_path: str) -> dict:
    return {"type": "image", "url": str(Path(source_path).resolve())}


def _clean_truncated_repeats(
    text: str,
    *,
    min_text_len: int = 4000,
    max_period: int = 200,
    min_period: int = 1,
    min_repeat_chars: int = 100,
    min_repeat_times: int = 5,
) -> tuple[str, bool]:
    """清理 Ovis 官方已知的生成尾部循环，并返回是否命中。"""
    n = len(text)
    if n < min_text_len:
        return text, False

    max_period = min(max_period, n - 1)
    for unit_len in range(min_period, max_period + 1):
        if text[n - 1] != text[n - 1 - unit_len]:
            continue

        match_len = 1
        index = n - 2
        while index >= unit_len and text[index] == text[index - unit_len]:
            match_len += 1
            index -= 1

        total_len = match_len + unit_len
        repeat_times = total_len // unit_len
        tail_len = total_len % unit_len
        if repeat_times >= min_repeat_times and total_len >= min_repeat_chars:
            cleaned = text[: n - total_len + unit_len] + text[n - tail_len:]
            return cleaned, True

    return text, False


def _repeated_token_tail(
    token_ids: list[int],
    *,
    max_period: int = TAIL_REPEAT_MAX_PERIOD,
    min_repeat_tokens: int = TAIL_REPEAT_MIN_TOKENS,
    min_repeat_times: int = TAIL_REPEAT_MIN_TIMES,
) -> tuple[int, int] | None:
    """返回退化循环的（周期 token 数, 连续次数），否则返回 None。"""
    length = len(token_ids)
    max_period = min(max_period, length // min_repeat_times)
    for period in range(1, max_period + 1):
        repeats_needed = max(min_repeat_times, (min_repeat_tokens + period - 1) // period)
        if length < period * repeats_needed:
            continue
        unit = token_ids[-period:]
        repeat_count = 1
        cursor = length - period * 2
        while cursor >= 0 and token_ids[cursor:cursor + period] == unit:
            repeat_count += 1
            cursor -= period
        if repeat_count >= repeats_needed:
            return period, repeat_count
    return None


class _TailRepeatStoppingCriteria:
    """在 Transformers 生成阶段尽早停止已确认的尾部循环。"""

    def __init__(self, prompt_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.last_checked_tokens = 0
        self.triggered = False
        self.trim_tokens = 0

    def __call__(self, input_ids, scores, **kwargs):
        del scores, kwargs
        generated = input_ids[0][self.prompt_tokens:].tolist()
        if len(generated) - self.last_checked_tokens < TAIL_REPEAT_CHECK_INTERVAL:
            return False
        self.last_checked_tokens = len(generated)
        repeated = _repeated_token_tail(generated)
        if repeated is None:
            return False
        period, repeat_count = repeated
        self.triggered = True
        self.trim_tokens = period * (repeat_count - 1)
        return True


def _mark_incomplete_generation(fragment: dict, *, token_limit_reached: bool, repeated_output: bool) -> None:
    incomplete = token_limit_reached or repeated_output
    fragment["truncated"] = incomplete
    if not incomplete:
        return

    fragment["quality_dims"]["text_extraction"] = "warning"
    fragment["quality_dims"]["ocr"] = "warning"
    fragment["fitness"]["fact_extraction"] = False
    warnings = []
    if token_limit_reached:
        warnings.append("生成达到 max_new_tokens，结果可能截断；编排器应继续降级链")
    if repeated_output:
        warnings.append("检测到并清理生成尾部重复循环；结果不完整，编排器应继续降级链")
    fragment["warnings"] = warnings
    fragment["notes"].extend(warnings)


def handle_parse(req):
    from transformers import AutoModelForMultimodalLM, AutoProcessor, StoppingCriteriaList

    options = req.get("options") or {}
    model_id = str(options.get("model", MODEL_ID))
    revision = options.get("revision")
    load_options = {"local_files_only": True}
    if revision:
        load_options["revision"] = str(revision)
    processor = AutoProcessor.from_pretrained(model_id, **load_options)
    model = AutoModelForMultimodalLM.from_pretrained(model_id, device_map="auto", **load_options)
    messages = [{"role": "user", "content": [
        _image_content(req["source_path"]),
        {"type": "text", "text": PROMPT},
    ]}]
    inputs = processor.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt"
    ).to(model.device)
    max_new_tokens = int(options.get("max_new_tokens", 16384))
    repeat_stopper = _TailRepeatStoppingCriteria(inputs["input_ids"].shape[-1])
    outputs = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        stopping_criteria=StoppingCriteriaList([repeat_stopper]),
    )
    raw_generated = outputs[0][inputs["input_ids"].shape[-1]:]
    generated = raw_generated[:-repeat_stopper.trim_tokens] if repeat_stopper.trim_tokens else raw_generated
    raw_markdown = processor.decode(generated, skip_special_tokens=True).strip()
    markdown, text_repeat = _clean_truncated_repeats(
        raw_markdown,
        min_text_len=0 if repeat_stopper.triggered else 4000,
    )
    repeated_output = repeat_stopper.triggered or text_repeat
    eos = getattr(model.generation_config, "eos_token_id", None)
    eos_ids = set(eos if isinstance(eos, list) else [eos]) - {None}
    token_limit_reached = raw_generated.numel() >= max_new_tokens and (
        not eos_ids or int(raw_generated[-1]) not in eos_ids
    )
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
        "tail_repeat_stop": {
            "max_period": TAIL_REPEAT_MAX_PERIOD,
            "min_repeat_tokens": TAIL_REPEAT_MIN_TOKENS,
            "min_repeat_times": TAIL_REPEAT_MIN_TIMES,
            "check_interval": TAIL_REPEAT_CHECK_INTERVAL,
        },
    })
    _mark_incomplete_generation(
        fragment,
        token_limit_reached=token_limit_reached,
        repeated_output=repeated_output,
    )
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
