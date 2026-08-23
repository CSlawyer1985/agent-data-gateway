"""Legal Pack：法律高频格式的 stdlib lite parser 与预检钩子。

lite parser 只提供无依赖兜底能力，产物必须带诚实降级标注；
重格式首选外部 worker（spec 08 路由表）。
"""

from __future__ import annotations

from gateway import routing
from gateway.workers import WorkerInfo, WorkerRegistry

from . import (
    av_lite,
    chat_lite,
    csv_lite,
    doc_lite,
    docx_lite,
    dxf_lite,
    eml_lite,
    html_lite,
    image_lite,
    pdf_lite,
    ppt_old,
    pptx_lite,
    text_lite,
    xlsx_lite,
)

_LITE_PARSERS = [
    (text_lite, "text_lite", ["text"]),
    (csv_lite, "csv_lite", ["csv"]),
    (html_lite, "html_lite", ["html"]),
    (doc_lite, "doc_lite", ["doc"]),
    (docx_lite, "docx_lite", ["docx"]),
    (xlsx_lite, "xlsx_lite", ["xlsx"]),
    (eml_lite, "eml_lite", ["email"]),
    (chat_lite, "chat_lite", ["chat"]),
    (image_lite, "image_lite", ["image"]),
    (pdf_lite, "pdf_lite", ["pdf"]),
    (ppt_old, "ppt_old", ["presentation"]),
    (pptx_lite, "pptx_lite", ["presentation"]),
    (dxf_lite, "dxf_lite", ["dxf"]),
    (av_lite, "av_lite", ["audio", "video"]),
]

_registered = False


def register_lite_parsers(registry: WorkerRegistry) -> None:
    """把 lite parser 与 preflight 钩子注册进编排器。幂等。"""
    global _registered
    for module, name, kinds in _LITE_PARSERS:
        if registry.get(name) is None:
            registry.register_lite(
                WorkerInfo(
                    name=name,
                    version=getattr(module, "PARSER_VERSION", "0.1.0"),
                    role="parse",
                    kinds=list(kinds),
                    formats=None,
                    requirements={},
                    backend="inproc",
                    maturity="stable",
                    license="same-as-repo",
                    parse_fn=module.parse,
                )
            )
    register_preflights()
    _registered = True


def register_preflights() -> None:
    """注册路由/信号探测钩子。

    与 worker 注册分开：signals 是 scan 阶段的产物（spec 11 §3.2），
    而 scan 不构建 worker registry。重复调用无副作用。
    """
    routing.register_preflight("pdf", pdf_lite.preflight_pdf)


def chat_kind_refiner():
    """给 scan 用的 kind 精化器：txt 命中聊天导出模式时 kind → chat。"""
    from gateway import magic

    def refine(path, detected, head: bytes):
        if detected.kind != magic.TEXT or (detected.format or "") not in (None, ""):
            return None
        sample = head.decode("utf-8", errors="ignore")
        if chat_lite.detect_chat_format(sample) is not None:
            return magic.DetectedType(
                mime="text/plain",
                magic="chat-pattern",
                method="adg-magic",
                confidence="medium",
                kind=magic.CHAT,
                format=chat_lite.detect_chat_format(sample),
            )
        return None

    return refine
