"""特征路由 + 降级链（spec 08）。

路由依据是文档特征和任务目标；硬件/可用性降级是二级机制。
preflight 钩子由 packs.legal 注册（插件自注册，gateway 不反向依赖 packs）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

# preflight: (source_path, detected) -> features dict
PreflightFn = Callable[[Path, Any], dict]

_PREFLIGHTS: dict[str, PreflightFn] = {}

# 格式簇默认链：元素为 worker 名（lite 或外部）。
DEFAULT_CHAINS: dict[str, list[str]] = {
    "docx": ["docx_lite", "officecli", "docling"],
    "doc": ["doc_lite", "docling"],  # textutil lite 优先，老格式降级 docling
    "xlsx": ["xlsx_lite", "docling"],
    "xls": ["xls", "docling"],
    "csv": ["csv_lite"],
    "text": ["text_lite"],
    "html": ["trafilatura", "html_lite"],
    "image": ["paddleocr", "rapidocr", "image_lite"],
    "email": ["eml_lite"],  # msg/pst/ost 由 format 细分覆盖
    "chat": ["chat_lite"],
    "audio": ["whisper", "av_lite"],
    "video": ["video_frames", "whisper", "av_lite"],
    "dxf": ["ezdxf", "dxf_lite"],
    "dwg": ["libredwg", "dwg_render"],  # 转 dxf(结构) + 渲染 bmp(可视化+OCR 标注)
    "pdf": ["docling", "pdfplumber", "pypdf", "pdf_lite"],  # 默认（数字简单）；预检可改写
    "presentation": ["pptx_lite", "ppt_old", "docling"],
    "database": [],
    "archive": [],
    "executable": [],
    "unknown": [],
}

# email format 细分
EMAIL_FORMAT_CHAINS: dict[str, list[str]] = {
    "eml": ["eml_lite"],
    "msg": ["extract_msg"],
    "pst": ["libpff"],
    "ost": ["libpff"],
}

# PDF 按预检特征分流
PDF_CHAINS: dict[str, list[str]] = {
    "digital_simple": ["docling", "pdfplumber", "pypdf", "pdf_lite"],
    "scanned": ["mineru"],
    "mixed_complex": ["mineru", "docling", "pdfplumber", "pypdf", "pdf_lite"],
    "encrypted": ["qpdf"],
}


def register_preflight(kind: str, fn: PreflightFn) -> None:
    _PREFLIGHTS[kind] = fn


def preflight(kind: str, source_path: Path, detected: Any) -> dict:
    fn = _PREFLIGHTS.get(kind)
    if fn is None:
        return {}
    try:
        return fn(source_path, detected) or {}
    except Exception as e:
        return {"preflight_error": f"{type(e).__name__}: {e}"}


def resolve_chain(kind: str, format_: str | None, features: dict) -> tuple[list[str], str]:
    """返回 (候选 worker 链, 路由理由)。"""
    if kind == "pdf":
        cls = features.get("pdf_class")
        if cls == "encrypted":
            return list(PDF_CHAINS["encrypted"]), "PDF 加密 → qpdf（需密码）"
        if features.get("possibly_corrupt"):
            return ["pypdf", "pdf_lite"], "PDF 结构不完整（缺 %%EOF）→ 容错链"
        if cls == "scanned":
            return list(PDF_CHAINS["scanned"]), "PDF 扫描/无文字层 → MinerU"
        if cls == "mixed_complex":
            return list(PDF_CHAINS["mixed_complex"]), "PDF 混合/复杂版面 → MinerU 优先"
        return list(PDF_CHAINS["digital_simple"]), "PDF 数字简单 → Docling 优先"
    if kind == "email":
        chain = EMAIL_FORMAT_CHAINS.get(format_ or "eml")
        if chain:
            return list(chain), f"邮件格式 {format_ or 'eml'} 专用链"
    chain = DEFAULT_CHAINS.get(kind, [])
    return list(chain), f"{kind} 默认链"


def fallback_reason_from_result(result: dict, *, available: bool, availability_reason: str = "") -> dict:
    """把一次失败/不可用转成 fell_back_from 记录元素。"""
    if not available:
        return {"reason": "unavailable", "detail": availability_reason}
    err = (result or {}).get("error") or {}
    kind_map = {
        "timeout": "timeout",
        "crash": "error",
        "dependency_missing": "unavailable",
        "unsupported": "rejected_by_preflight",
        "rejected": "rejected_by_preflight",
        "resource_limit": "error",
    }
    return {"reason": kind_map.get(err.get("kind", ""), "error"), "detail": err.get("message", "")[:200]}
