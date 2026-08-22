"""DOC 解析（macOS textutil）：旧版 .doc（CFB 二进制）经系统 textutil 转 txt 提取正文。

旧 .doc 无 stdlib lite parser（非 zip/OOXML，是 CFB 二进制）。macOS 自带 textutil 可
解码为纯文本，作为 docling 的轻量前置兜底。依赖 macOS（非跨平台），无 textutil 时 raise
（dependency_missing），编排器沿降级链转 docling。
textutil 输出纯文本，不保留表格/版面/元数据（layout_sensitive_analysis=False）。
锚点：paragraph index（由 textutil 输出行近似为段落）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PARSER_VERSION = "0.1.0"


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    notes: list[str] = []
    truncated = False
    max_lines = limits.get("doc_max_lines", 200_000)
    try:
        proc = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(source_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        from gateway.errors import WorkerFailed

        raise WorkerFailed("textutil 不存在（非 macOS 环境）", kind="dependency_missing")
    except subprocess.TimeoutExpired:
        from gateway.errors import WorkerFailed

        raise WorkerFailed("textutil 转换超时", kind="crash")
    if proc.returncode != 0:
        from gateway.errors import WorkerFailed

        raise WorkerFailed(f"textutil 失败 rc={proc.returncode}: {(proc.stderr or '')[-200:]}", kind="crash")

    raw_lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if len(raw_lines) > max_lines:
        truncated = True
        notes.append(f"行数超过上限 {max_lines}，已截断")
        raw_lines = raw_lines[:max_lines]

    elements = [
        {"type": "paragraph", "text": ln, "locator": {"system": "paragraph", "index": i}}
        for i, ln in enumerate(raw_lines, start=1)
    ]
    block = {"block_type": "section", "index": 0, "label": "正文", "elements": elements}

    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if raw_lines else "warning",
        "layout_preservation": "warning",  # 纯文本，丢表格/版面
        "attachments": "pass",
        "metadata": "warning",  # textutil -convert txt 不输出元数据
        "source_anchoring": "pass",
    }
    return {
        "parser_name": "doc_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": [block]},
        "quality_dims": dims,
        "fitness": {"layout_sensitive_analysis": False},
        "notes": notes + ["doc_lite 经 macOS textutil 提取纯文本，不保留表格/版面/元数据"],
        "truncated": truncated,
    }
