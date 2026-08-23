"""缺口诊断：按 manifest 实际 kind 分布 → 路由所需 worker → 对照本机环境列出缺口。

驱动 adg doctor 与 adg setup --package/--missing。核心思想：
不问"装了哪些工具"，问"这批材料需要哪些工具、本机缺哪些、怎么补"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import magic, routing
from .hardware import Environment, check_requirements
from .models import PackageManifest
from .workers import WorkerRegistry


@dataclass
class KindGap:
    kind: str
    count: int  # 该类型文件数
    role: str  # parse / transform
    chain: list[str]  # 路由出的候选 worker 链（按优先级）
    available: list[str] = field(default_factory=list)  # 本机可用的
    missing: list[str] = field(default_factory=list)  # 链内但本机不可用的
    installable: list[str] = field(default_factory=list)  # missing 中可由 adg setup 装的
    binary_hints: dict[str, str] = field(default_factory=dict)  # missing worker → 手动安装提示
    verdict: str = "ok"  # ok / partial / no_parser / needs_install
    detail: str = ""

    @property
    def has_available(self) -> bool:
        return bool(self.available)


@dataclass
class GapReport:
    package_root_name: str
    total_items: int
    by_kind: dict[str, KindGap] = field(default_factory=dict)
    summary: str = ""
    install_plan: list[str] = field(default_factory=list)  # 建议安装的 worker 名（去重、按权重）

    @property
    def blocked_kinds(self) -> list[KindGap]:
        return [g for g in self.by_kind.values() if not g.has_available]


def analyze_manifest_gaps(
    m: PackageManifest,
    registry: WorkerRegistry,
    env: Environment | None = None,
    *,
    policy: dict | None = None,
) -> GapReport:
    """按 manifest 的 kind 分布算每类的 worker 缺口。"""
    from .security import load_policy

    env = env or _detect_env()
    policy = policy or load_policy()
    counts: dict[str, int] = {}
    for it in m.items:
        if it.kind in (magic.EXECUTABLE, magic.UNKNOWN) and it.status == "unsupported":
            continue
        counts[it.kind] = counts.get(it.kind, 0) + 1

    report = GapReport(package_root_name=m.package_root_name, total_items=sum(counts.values()))
    for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if kind in ("archive",):
            continue  # 压缩包 scan 阶段已展开，无需 parse worker
        chain, _reason = routing.resolve_chain(kind, None, {})
        # 容器/transform 类（dwg）取其链
        role = "transform" if kind in ("dwg",) else "parse"
        gap = KindGap(kind=kind, count=n, role=role, chain=list(chain))
        for wname in chain:
            w = registry.get(wname)
            if w is None:
                gap.missing.append(wname)
                continue
            ok, reason = registry.availability(w, env)
            req_ok, _ = check_requirements(w.requirements, env)
            if ok and req_ok:
                gap.available.append(wname)
            else:
                gap.missing.append(wname)
                # 判断可装性
                if w.backend == "inproc":
                    continue  # lite 不可用不该发生（stdlib）
                if w.python_requires or w.backend == "subprocess":
                    gap.installable.append(wname)
                if not w.python_requires and w.probe_command:
                    gap.binary_hints[wname] = _binary_hint(wname)
        if not chain:
            gap.verdict = "no_parser"
            gap.detail = "该类型无解析链（MVP 暂不支持，属 Extended Pack 或长尾格式）"
        elif gap.available:
            # 是否只有元数据级 lite 兜底（装重 worker 才能读内容）
            metadata_only = all(w in _METADATA_ONLY_LITE for w in gap.available)
            content_installable = [w for w in gap.installable if w not in _METADATA_ONLY_LITE]
            if metadata_only and content_installable:
                gap.verdict = "upgrade"
                gap.detail = f"仅元数据；装 {', '.join(content_installable)} 可读取内容"
            else:
                gap.verdict = "ok"
        elif gap.installable:
            gap.verdict = "needs_install"
            gap.detail = f"可装: {', '.join(gap.installable)}"
        else:
            gap.verdict = "no_parser"
            gap.detail = "链内 worker 均不可用且无法自动安装"
        report.by_kind[kind] = gap

    # 安装计划：按需安装，light 优先
    seen = set()
    for g in report.by_kind.values():
        for wname in g.installable:
            if wname not in seen:
                seen.add(wname)
                report.install_plan.append(wname)
    blocked = report.blocked_kinds
    upgrade_kinds = [g for g in report.by_kind.values() if g.verdict == "upgrade"]
    affected_blocked = sum(g.count for g in blocked)
    affected_upgrade = sum(g.count for g in upgrade_kinds)
    if not blocked and not upgrade_kinds:
        report.summary = f"全部 {report.total_items} 个文件均有可用 parser 且内容可读"
    else:
        parts = []
        if blocked:
            parts.append(f"{len(blocked)} 种类型完全无 parser（{affected_blocked} 个文件）")
        if upgrade_kinds:
            parts.append(f"{len(upgrade_kinds)} 种类型仅元数据、装重 worker 可读内容（{affected_upgrade} 个文件）")
        report.summary = "；".join(parts) + f" → 建议安装: {', '.join(report.install_plan) or '（无自动项）'}"
    return report


# 仅提取元数据、需重 worker 才能读内容的 lite parser
_METADATA_ONLY_LITE = {"image_lite", "av_lite"}


def _detect_env() -> Environment:
    from .hardware import detect_environment

    return detect_environment()


def _binary_hint(name: str) -> str:
    hints = {
        "officecli": "brew install officecli",
        "qpdf": "brew install qpdf",
        "libredwg": "brew install libredwg",
        "libpff": "brew install libpff（提供 pffexport）",
        "tika": "安装 Java + 下载 tika-server JAR，设 ADG_TIKA_JAR",
        "whisper": "brew install ffmpeg；pip install faster-whisper（模型首次运行下载）",
    }
    return hints.get(name, "见 workers/%s/worker.json" % name)


def render_doctor_md(report: GapReport) -> str:
    lines = [
        f"# 材料缺口诊断：{report.package_root_name}",
        "",
        report.summary,
        "",
        "| 类型 | 文件数 | 状态 | 可用 parser | 缺失(可装) | 缺失(手动) |",
        "|------|--------|------|-------------|-----------|-----------|",
    ]
    icon = {"ok": "✅", "needs_install": "⏬", "no_parser": "❌", "partial": "⚠️", "upgrade": "⏫"}
    for kind, g in report.by_kind.items():
        av = ", ".join(g.available) or "—"
        inst = ", ".join(g.installable) or "—"
        manual = ", ".join(f"{w}（{g.binary_hints.get(w, '')}）" for w in g.missing if w not in g.installable) or "—"
        lines.append(f"| {kind} | {g.count} | {icon.get(g.verdict, g.verdict)} {g.verdict} | {av} | {inst} | {manual} |")
    lines.append("")
    if report.install_plan:
        lines.append("## 一键安装")
        lines.append("")
        lines.append("```bash")
        lines.append("adg setup --package <材料包目录>   # 装本包所需、本机缺失的 worker")
        lines.append("```")
        lines.append("")
        lines.append("安装后重跑 `adg parse <材料包目录>` 即可提升覆盖率。")
        lines.append("")
    blocked = report.blocked_kinds
    if blocked:
        lines.append("## 装完后仍读不到的（需关注）")
        lines.append("")
        for g in blocked:
            if g.verdict == "no_parser" and not g.installable:
                lines.append(f"- **{g.kind}**（{g.count} 个）：{g.detail}")
        lines.append("")
    return "\n".join(lines)
