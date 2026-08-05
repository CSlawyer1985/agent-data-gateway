#!/usr/bin/env python3
"""whisper worker adapter — ADG Worker Protocol v0.1。

faster-whisper 转写 → segment 块 + timecode 锚点。
非 wav 输入先用 ffmpeg 转 16k 单声道 wav。
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

WORKER = {"name": "whisper", "version": "0.1.0", "maturity": "implemented", "license": "MIT"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _to_wav(src: str, work_dir: Path) -> str:
    if src.lower().endswith(".wav"):
        return src
    out = work_dir / "adg-audio.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1", "-f", "wav", str(out)],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {(proc.stderr or '')[-300:]}")
    return str(out)


def handle_parse(req):
    from faster_whisper import WhisperModel

    options = req.get("options") or {}
    model_size = options.get("model", "small")
    language = options.get("language", "zh")
    diarize = bool(options.get("diarize", False))
    warnings: list[str] = ["ASR 有损转写：引用前需对照原件核验"]

    work_dir = Path(req["work_dir"])
    wav = _to_wav(req["source_path"], work_dir)

    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments_iter, info = model.transcribe(wav, language=language, vad_filter=True, word_timestamps=False)

    elements = []
    for seg in segments_iter:
        elements.append({
            "type": "segment_text",
            "text": seg.text.strip(),
            "locator": {"system": "timecode", "start": round(seg.start, 3), "end": round(seg.end, 3)},
            "attrs": {"avg_logprob": round(seg.avg_logprob, 4), "no_speech_prob": round(seg.no_speech_prob, 4)},
        })

    speaker_map = None
    if diarize:
        try:
            import torch  # noqa: F401
            from pyannote.audio import Pipeline  # noqa: F401
            warnings.append("说话人分离：pyannote 集成点（需在 adapter 中完善 token/模型配置，当前记录为未执行）")
        except ImportError:
            warnings.append("options.diarize=true 但 pyannote 未安装，说话人分离未执行")

    blocks = [{"block_type": "segment", "index": 0, "label": f"转写（{model_size}/{language}）", "elements": elements}]
    text_chars = sum(len(e["text"]) for e in elements)
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "whisper",
            "parser_version": WORKER["version"],
            "model_version": f"faster-whisper:{model_size}",
            "operation": "transcribe",
            "coordinate_system": {"name": "timecode", "units": "seconds"},
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if text_chars else "warning",
                "metadata": "pass",
                "source_anchoring": "pass",
            },
            "notes": [
                f"检测语言: {getattr(info, 'language', language)}（p={round(getattr(info, 'language_probability', 0), 2)}）",
                f"时长: {round(getattr(info, 'duration', 0), 1)}s；片段 {len(elements)} 个",
                *(["说话人分离未执行"] if diarize and speaker_map is None else []),
            ],
            "warnings": warnings,
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
              "handles": [{"kind": "audio", "role": "parse"}, {"kind": "video", "role": "parse"}],
              "requirements": {"min_ram_mb": 4096, "gpu": "optional", "backend": ["subprocess"], "network": False}})
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
