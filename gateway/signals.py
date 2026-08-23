"""信号探测（spec 11）。

回答「这份材料里有哪些种类的信息」，与完整性（文件级覆盖）正交。
每个信号取 present / absent / unknown 三态；探测器拿不准一律记 unknown——
把 unknown 折叠进 absent 就是在假装知道。

**不计算比值。** 「提出来了多少」需要 ground truth，而 ADG 若掌握 ground truth
就不必调用这些工具，原理上不可知（ADR 0004）。本模块只陈述探测到的事实；
够不够、要不要再跑一个工具，由调用方判断。

信号清单是本模块的权威（spec 11 §3.1 的代码化）。探测由各 kind 的 preflight
在 packs 侧完成（gateway 不反向依赖 packs），本模块负责补齐未探测项。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"

# 各 kind 的可提取信号全集。探测器未覆盖的条目一律补 UNKNOWN。
SIGNAL_CATALOG: dict[str, tuple[str, ...]] = {
    "pdf": ("text_layer", "tables", "page_images", "annotations", "form_fields",
            "attachments", "bookmarks", "metadata", "signature_signals"),
    "docx": ("body_text", "tables", "comments", "revisions", "headers_footers",
             "footnotes", "embedded_objects", "images", "metadata"),
    "xlsx": ("cells", "formulas", "extra_sheets", "cell_comments", "charts", "hidden_content", "metadata"),
    "xls": ("cells", "formulas", "extra_sheets", "cell_comments", "charts", "hidden_content", "metadata"),
    "pptx": ("slide_text", "speaker_notes", "tables", "slide_images", "metadata"),
    "ppt": ("slide_text", "speaker_notes", "tables", "slide_images", "metadata"),
    "image": ("ocr_text", "exif", "seal_regions"),
    "audio": ("transcript", "speaker_turns", "metadata"),
    "video": ("transcript", "speaker_turns", "frame_text", "metadata"),
    "email": ("body", "headers", "attachments", "inline_images"),
    "dwg": ("entities", "layers", "text_labels", "thumbnail"),
    "dxf": ("entities", "layers", "text_labels", "thumbnail"),
}


def catalog_for(kind: str) -> tuple[str, ...]:
    return SIGNAL_CATALOG.get(kind, ())


def normalize(kind: str, probed: dict | None) -> dict[str, str]:
    """把探测器的部分结果补齐成该 kind 的完整三态字典。

    探测器只需报它真正判定了的信号；其余（含探测器缺席、探测失败、加密打不开）
    一律 UNKNOWN。kind 不在清单里则返回空字典——没有清单就无从陈述，不臆造。
    """
    names = catalog_for(kind)
    if not names:
        return {}
    probed = probed or {}
    return {name: probed.get(name, UNKNOWN) if probed.get(name) in (PRESENT, ABSENT) else UNKNOWN
            for name in names}


def probe(kind: str, source_path: Path, detected: Any = None) -> dict[str, str]:
    """scan 阶段探测：走该 kind 的 preflight 取信号，补齐后返回三态字典。

    没有信号清单的 kind（可执行文件、未识别等）返回空。
    preflight 缺席或抛错时 normalize 会把全部信号补成 unknown：探测不了就说不知道。
    """
    if not catalog_for(kind):
        return {}
    from . import routing  # 延迟导入，避免 gateway 包内的模块初始化顺序耦合

    features = routing.preflight(kind, source_path, detected)
    return normalize(kind, features.get("signals"))


def present_signals(signals: dict[str, str]) -> list[str]:
    """探测到确实存在的信号。这是可提取信息的种类清单，不是任何比值的分母。"""
    return [name for name, state in signals.items() if state == PRESENT]


def unknown_signals(signals: dict[str, str]) -> list[str]:
    """探测不了的信号。必须单独可数并在报告中列出，不得折叠进 absent。"""
    return [name for name, state in signals.items() if state == UNKNOWN]
