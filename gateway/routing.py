"""特征路由 + 降级链（spec 08）。

路由依据是文档特征和任务目标；硬件/可用性降级是二级机制。
preflight 钩子由 packs.legal 注册（插件自注册，gateway 不反向依赖 packs）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .magic import extension_catalog

# preflight: (source_path, detected) -> features dict
PreflightFn = Callable[[Path, Any], dict]

_PREFLIGHTS: dict[str, PreflightFn] = {}


PROFILES = ("fast", "balanced", "quality")
DEFAULT_PROFILE = "balanced"


@dataclass(frozen=True)
class ParallelChain:
    """可与某条规则并跑的另一条链。

    requires_signal 是探测门控：该信号不是 present 就不跑——探测到没有图就不必
    启动 OCR，这是成本控制里最大的一笔（spec 11 §6.1）。
    """

    rule_id: str
    requires_signal: str | None = None


@dataclass(frozen=True)
class PlannedChain:
    """一条计划执行的链。顺序只表示执行先后，不表示结果优劣（ADR 0005 决策 4）。"""

    rule_id: str
    workers: tuple[str, ...]
    reason: str
    parallel: bool = False  # False=首选链；True=并跑链


@dataclass(frozen=True)
class RouteRule:
    """一条可执行、可查询的路由规则；顺序即优先级。"""

    rule_id: str
    kind: str
    reason: str
    workers: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    features: tuple[tuple[str, object], ...] = ()
    action: str = "parse"  # parse / expand / unresolved
    tools: tuple[str, ...] = ()  # 非 dispatcher worker，例如 stdlib 展开器
    # 可与本规则并跑的其他链（ADR 0005）。跑不跑由 profile 决定，不是本规则说了算。
    parallel_with: tuple["ParallelChain", ...] = ()

    def matches(self, kind: str, format_: str | None, features: dict) -> bool:
        if self.kind != kind:
            return False
        normalized_format = "eml" if kind == "email" and not format_ else format_
        if self.formats and normalized_format not in self.formats:
            return False
        return all(features.get(key) == value for key, value in self.features)

    @property
    def processing_tools(self) -> tuple[str, ...]:
        return self.tools or self.workers


# 操作路由的唯一权威。Discoverer、Dispatcher 与 `adg routes` 都读取；列表顺序即命中优先级。
ROUTE_RULES: tuple[RouteRule, ...] = (
    RouteRule(
        "pdf-encrypted",
        "pdf",
        "PDF 加密 → 先试空用户口令/给定口令（多数只是所有者限制），仍打不开再走 qpdf（需密码）",
        ("pypdf", "pdfplumber", "qpdf"),
        features=(("pdf_class", "encrypted"),),
    ),
    RouteRule(
        "pdf-corrupt",
        "pdf",
        "PDF 结构不完整（缺 %%EOF）→ 容错链",
        ("pypdf", "pdf_lite"),
        features=(("possibly_corrupt", True),),
    ),
    RouteRule(
        "pdf-scanned",
        "pdf",
        "PDF 扫描/无文字层 → MinerU；无容器/无 GPU 时用 pdf_render 渲染页面位图，回灌 image 链做 OCR",
        ("mineru", "pdf_render"),
        features=(("pdf_class", "scanned"),),
    ),
    RouteRule(
        "pdf-mixed-complex",
        "pdf",
        "PDF 混合/复杂版面 → MinerU 优先",
        ("mineru", "docling", "pdfplumber", "pypdf", "pdf_lite"),
        features=(("pdf_class", "mixed_complex"),),
    ),
    RouteRule(
        "pdf-digital-simple",
        "pdf",
        "PDF 数字简单 → Docling 优先",
        ("docling", "pdfplumber", "pypdf", "pdf_lite"),
        # 文字层链看不见图里的东西。探测到 page_images 时并跑视觉链，
        # 让印章/手写/图内文字有机会被读到（ADR 0002 的原型场景）。
        parallel_with=(ParallelChain("pdf-visual", requires_signal="page_images"),),
    ),
    # 只作并跑目标，不参与首条命中——排在 pdf 默认规则之后，resolve_rule 永远走不到这里。
    RouteRule(
        "pdf-visual",
        "pdf",
        "视觉视角：渲染页位图回灌 image 链做 OCR，读文字层看不到的印章与手写",
        ("pdf_render",),
    ),
    RouteRule("email-eml", "email", "RFC 822/MIME 结构可直接解析正文与附件", ("eml_lite",), formats=("eml",)),
    RouteRule("email-msg", "email", "MSG 是 OLE Outlook 容器，需要专用属性流解析", ("extract_msg",), formats=("msg",)),
    RouteRule("email-pst", "email", "PST 是 Outlook 邮箱存储，需要 libpff 展开后回灌邮件", ("libpff",), formats=("pst",)),
    RouteRule("email-ost", "email", "OST 与 PST 同属 Outlook 存储族，使用 libpff 展开", ("libpff",), formats=("ost",)),
    RouteRule("email-default", "email", "未细分的邮件格式按 EML 兼容路径尝试", ("eml_lite",)),
    RouteRule("docx-default", "docx", "OOXML Word 可保留段落结构与批注修订，内置解析优先，anydoc/Office/Docling 依次回退", ("docx_lite", "anydoc", "officecli", "docling")),
    RouteRule(
        "doc-anydoc-formats",
        "doc",
        "RTF/EPUB 无内置 parser，anydoc（Rust，无系统依赖）直接解析，Docling 兜底",
        ("anydoc", "docling"),
        formats=("rtf", "epub"),
    ),
    RouteRule("doc-default", "doc", "旧 Word/ODT 先走本机轻量提取，anydoc 与 Docling 依次回退", ("doc_lite", "anydoc", "docling")),
    RouteRule(
        "xlsx-ods",
        "xlsx",
        "ODS 无内置 parser，anydoc 解析并保留合并单元格的 origin/span",
        ("anydoc", "docling"),
        formats=("ods",),
    ),
    RouteRule("xlsx-default", "xlsx", "OOXML 表格可直接保留工作表与单元格锚点，anydoc/Docling 作为回退", ("xlsx_lite", "anydoc", "docling")),
    RouteRule("xls-default", "xls", "旧版二进制工作簿需要专用 xls 解析，anydoc/Docling 作为回退", ("xls", "anydoc", "docling")),
    RouteRule("csv-default", "csv", "分隔文本可确定性解析并保留行列结构", ("csv_lite",)),
    RouteRule("text-default", "text", "纯文本、Markdown、JSON、XML 可直接解码为可检索文本", ("text_lite",)),
    RouteRule("html-default", "html", "先提取网页正文，失败时由内置 HTML 解析器兜底", ("trafilatura", "html_lite")),
    RouteRule("image-default", "image", "图片需要 OCR 才能获得正文；无 OCR 时只保留诚实的元数据兜底", ("paddleocr", "rapidocr", "image_lite")),
    RouteRule("chat-default", "chat", "聊天导出先按消息边界、参与者和时间结构解析", ("chat_lite",)),
    RouteRule("audio-default", "audio", "音频先用 Whisper 生成带时间码的转录，失败时仅保留媒体元数据", ("whisper", "av_lite")),
    RouteRule("video-default", "video", "视频先抽帧并尝试语音转录，最后仅保留媒体元数据", ("video_frames", "whisper", "av_lite")),
    RouteRule("dxf-default", "dxf", "DXF 是结构化 CAD 文本，优先完整解析，内置解析器兜底", ("ezdxf", "dxf_lite")),
    RouteRule(
        "dwg-default",
        "dwg",
        "DWG 优先直读（dwgread 取图纸文字/图层/范围，避开 dwg2dxf 中转常见的结构损坏），"
        "再退转 DXF 取几何，最后渲染位图保留可视证据",
        ("dwgread", "libredwg", "dwg_render"),
    ),
    RouteRule(
        "presentation-odp",
        "presentation",
        "ODP 无内置 parser，anydoc 直接解析",
        ("anydoc", "docling"),
        formats=("odp",),
    ),
    RouteRule("presentation-default", "presentation", "演示文稿优先保留幻灯片文本与页级锚点，旧格式转换、anydoc 与 Docling 依次回退", ("pptx_lite", "ppt_old", "anydoc", "docling")),
    RouteRule("archive-zip", "archive", "ZIP 由发现阶段安全审查并展开，成员重新识别和路由", formats=("zip",), action="expand", tools=("stdlib:zipfile",)),
    RouteRule("archive-tar", "archive", "TAR 由发现阶段安全审查并展开，成员重新识别和路由", formats=("tar",), action="expand", tools=("stdlib:tarfile",)),
    RouteRule("archive-targz", "archive", "tar+gzip 逐层展开，成员重新识别和路由", formats=("tar+gzip",), action="expand", tools=("stdlib:gzip", "stdlib:tarfile")),
    RouteRule("archive-gzip", "archive", "gzip 解压为单个成员后重新识别和路由", formats=("gzip",), action="expand", tools=("stdlib:gzip",)),
    RouteRule("archive-7z", "archive", "7z 需要隔离的 py7zr worker 展开，成员再走总路由", formats=("7z",), action="expand", tools=("py7zr",)),
    RouteRule("archive-rar", "archive", "RAR 已可识别，但默认尚未接入安全 expander", formats=("rar",), action="unresolved", tools=("unrar（待接入）",)),
    RouteRule("archive-unknown", "archive", "未知压缩格式无法选择安全展开器，显式保留 unresolved", action="unresolved"),
    RouteRule(
        "caj-default",
        "caj",
        "CAJ/KDH/NH 是中国知网专有格式，无开源解析器；建议用 CAJViewer 或知网客户端导出 PDF 后重新入包",
        action="unresolved",
        tools=("CAJViewer 导出 PDF（人工）",),
    ),
    RouteRule("database-default", "database", "数据库/Revit 目前只识别类型，默认不读取业务内容", action="unresolved"),
    RouteRule("executable-default", "executable", "可执行文件不作为 Agent 证据内容执行或解析", action="unresolved"),
    RouteRule("unknown-default", "unknown", "类型识别不足时不猜测 parser，显式保留 unresolved", action="unresolved"),
)


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


def resolve_rule(kind: str, format_: str | None, features: dict) -> RouteRule | None:
    """按声明顺序返回首条命中规则。"""
    return next((rule for rule in ROUTE_RULES if rule.matches(kind, format_, features)), None)


def resolve_chain(kind: str, format_: str | None, features: dict) -> tuple[list[str], str]:
    """返回 (候选 worker 链, 路由理由)。"""
    rule = resolve_rule(kind, format_, features)
    if rule is None:
        return [], f"{kind} 默认链"
    return list(rule.workers), rule.reason


def resolve_plan(
    kind: str,
    format_: str | None,
    features: dict,
    signals: dict | None = None,
    *,
    profile: str = DEFAULT_PROFILE,
) -> tuple[list[PlannedChain], list[dict]]:
    """按 profile 决定跑哪几条链，并返回被跳过的声明。

    fast 只跑首选链；balanced / quality 追加规则声明的并跑链，逐条过信号门控。
    第二个返回值是跳过记录——少跑必须显性，静默省略等同于假装跑过了（ADR 0005 决策 5）。
    """
    rule = resolve_rule(kind, format_, features)
    if rule is None:
        return [], []
    plan = [PlannedChain(rule.rule_id, tuple(rule.workers), rule.reason)]
    skipped: list[dict] = []
    for candidate in rule.parallel_with:
        target = next((r for r in ROUTE_RULES if r.rule_id == candidate.rule_id), None)
        if target is None:
            skipped.append({"rule_id": candidate.rule_id, "reason": "unknown_rule"})
            continue
        if profile == "fast":
            skipped.append({"rule_id": candidate.rule_id, "reason": "profile_fast"})
            continue
        needed = candidate.requires_signal
        if needed:
            state = (signals or {}).get(needed, "unknown")
            # balanced 只在信号确认在场时跑；quality 连 unknown 也跑——探测不到不等于
            # 不存在，重点材料宁可多跑一次。absent 在任何档都不跑：确认没有图还去 OCR
            # 是纯浪费，这正是探测门控省钱的地方（spec 11 §6.1）。
            if not (state == "present" or (profile == "quality" and state == "unknown")):
                skipped.append({"rule_id": candidate.rule_id, "reason": f"signal_{needed}_{state}"})
                continue
        plan.append(PlannedChain(target.rule_id, tuple(target.workers), target.reason, parallel=True))
    return plan, skipped


def _extensions_for_rule(rule: RouteRule, hints: list[dict[str, str | None]]) -> list[str]:
    claimed_formats = {
        format_name
        for candidate in ROUTE_RULES
        if candidate.kind == rule.kind and candidate.formats
        for format_name in candidate.formats
    }
    matched = []
    for hint in hints:
        if hint["kind"] != rule.kind:
            continue
        if rule.formats and hint["format"] not in rule.formats:
            continue
        if not rule.formats and claimed_formats and hint["format"] in claimed_formats:
            continue
        matched.append(str(hint["extension"]))
    return matched


def route_catalog(*, kind: str | None = None, format_: str | None = None) -> list[dict]:
    """返回总路由表投影；识别扩展名来自 magic，执行规则来自 ROUTE_RULES。"""
    hints = extension_catalog()
    rows = []
    for priority, rule in enumerate(ROUTE_RULES, start=1):
        if kind and rule.kind != kind:
            continue
        extensions = _extensions_for_rule(rule, hints)
        if format_:
            wanted = format_.lower()
            if wanted.startswith("."):
                if wanted not in extensions:
                    continue
            elif rule.formats:
                if wanted not in rule.formats:
                    continue
            elif wanted != rule.kind:
                continue
        feature_match = {key: value for key, value in rule.features}
        # 谁会并跑本规则的链。只作并跑目标的规则不参与首条命中，若不说明，
        # 它在总表里会显示成「默认」，读者会以为同一 kind 有两条默认规则。
        parallel_from = [
            {"rule_id": candidate.rule_id, "requires_signal": target.requires_signal}
            for candidate in ROUTE_RULES
            for target in candidate.parallel_with
            if target.rule_id == rule.rule_id
        ]
        rows.append(
            {
                "priority": priority,
                "rule_id": rule.rule_id,
                "kind": rule.kind,
                "extensions": extensions,
                "formats": list(rule.formats),
                "when": {
                    "format_in": list(rule.formats),
                    "features": feature_match,
                    "otherwise": not rule.formats and not rule.features,
                },
                "action": rule.action,
                "processable": rule.action in {"parse", "expand"},
                "workers": list(rule.workers),
                "tools": list(rule.processing_tools),
                "reason": rule.reason,
                "parallel_from": parallel_from,
                "parallel_with": [
                    {"rule_id": t.rule_id, "requires_signal": t.requires_signal} for t in rule.parallel_with
                ],
            }
        )
    return rows


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
