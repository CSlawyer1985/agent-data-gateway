"""处理谱系（spec 02 字段；语义：每次转换一条，回答谁提取/哪个版本/是否经 OCR/输入输出哈希）。"""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_entry(
    operation: str,
    *,
    input_sha256: str,
    output_sha256: str,
    tool: str,
    tool_version: str,
    model_version: str | None = None,
    config_hash: str | None = None,
    runtime_versions: dict[str, str] | None = None,
    started_at: str,
    finished_at: str,
    exit_code: int = 0,
    warnings: list[str] | None = None,
) -> dict:
    entry = {
        "operation": operation,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "tool": tool,
        "tool_version": tool_version,
        "model_version": model_version,
        "config_hash": config_hash,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "warnings": list(warnings or []),
    }
    if runtime_versions:
        entry["runtime_versions"] = dict(sorted(runtime_versions.items()))
    return entry
