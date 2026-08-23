"""信号探测与利用率（spec 11）。

利用率回答「这份文件里的信息提出来了多少」，与完整性（文件级覆盖）正交。
分母必须是**探测到的事实**：每个信号取 present / absent / unknown 三态，
探测器拿不准一律记 unknown 并排除出分母——默认 absent 会把分母压小、利用率虚高。

信号清单是本模块的权威（spec 11 §3.1 的代码化）。探测由各 kind 的 preflight
在 packs 侧完成（gateway 不反向依赖 packs），本模块只负责补齐未探测项与度量。
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
    一律 UNKNOWN。kind 不在清单里则返回空字典——没有清单就没有分母，不臆造。
    """
    names = catalog_for(kind)
    if not names:
        return {}
    probed = probed or {}
    return {name: probed.get(name, UNKNOWN) if probed.get(name) in (PRESENT, ABSENT) else UNKNOWN
            for name in names}


def probe(kind: str, source_path: Path, detected: Any = None) -> dict[str, str]:
    """scan 阶段探测：走该 kind 的 preflight 取信号，补齐后返回三态字典。

    没有信号清单的 kind（可执行文件、未识别等）返回空——没有分母，不参与利用率。
    preflight 缺席或抛错时 normalize 会把全部信号补成 unknown：探测不了就说不知道。
    """
    if not catalog_for(kind):
        return {}
    from . import routing  # 延迟导入，避免 gateway 包内的模块初始化顺序耦合

    features = routing.preflight(kind, source_path, detected)
    return normalize(kind, features.get("signals"))


def denominator(signals: dict[str, str]) -> list[str]:
    """利用率分母：探测为 present 的信号。absent 与 unknown 都不在其中。"""
    return [name for name, state in signals.items() if state == PRESENT]


def unknown_signals(signals: dict[str, str]) -> list[str]:
    """既不进分子也不进分母，但必须单独计数并在报告中列出。"""
    return [name for name, state in signals.items() if state == UNKNOWN]


def utilization(signals: dict[str, str], extracted: set[str] | None) -> float | None:
    """item.utilization = 已成功提取的信号数 / 探测为 present 的信号数。

    分母为空（无 present 信号，或该 kind 无信号清单）时返回 None——
    「没有可提取的信号」和「一个都没提出来」是两件事，不能都记 0。
    """
    present = denominator(signals)
    if not present:
        return None
    got = extracted or set()
    return sum(1 for name in present if name in got) / len(present)


def gaps(signals: dict[str, str], extracted: set[str] | None) -> list[str]:
    """利用率缺口：探测到但尚未提取的信号。任何档位下都必须能报出来。"""
    got = extracted or set()
    return [name for name in denominator(signals) if name not in got]
