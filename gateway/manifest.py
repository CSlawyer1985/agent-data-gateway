"""manifest 与 rendition 的落盘/读取 + Markdown 渲染。

输出目录布局（--out 指定，默认 ./adg-output/<包名>/）：
  manifest.json / manifest.md
  documents/<source_id前16>/<rendition_id前16>.json / .md
  documents/<source_id前16>/alignment-<a前16>-<b前16>.json
  expanded/{archives,email,office-embed,transform}/<source_id前16>/...
  reports/report.md / report.json
  logs/run-<run_id>.log
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import PackageManifest

MANIFEST_JSON = "manifest.json"
MANIFEST_MD = "manifest.md"

# 程序内部工作目录（隐藏）：documents 哈希寻址、expanded 展开、work worker 临时、logs 运行日志
# 用户可见的产物（镜像视图 .md/.json + 可看二进制）一律放输出目录顶层，按原件结构布局。
INTERNAL_DIR = ".adg"


def internal_dir(out_dir: Path, *parts: str) -> Path:
    """程序内部路径（expanded/work/logs/documents 的父目录）。"""
    return out_dir.joinpath(INTERNAL_DIR, *parts)


def default_out_dir(package_root: Path) -> Path:
    """默认输出位置：与原件**同级并行**，命名 `<原名>（ADG）`。

    原件 /Users/甲/Downloads/案件 → 输出 /Users/甲/Downloads/案件（ADG）/。
    原件文件夹保持绝对纯净（程序不写入任何东西）；输出在隔壁并行，便于对比。
    """
    base = package_root.parent
    name = package_root.name if package_root.is_dir() else package_root.stem
    return base / (name + "（ADG）")


def is_adg_output_name(name: str) -> bool:
    """识别 ADG 输出目录/文件名（防止对父目录跑时把输出当材料扫入）。"""
    return name.endswith("（ADG）") or name.endswith("(ADG)")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: dict) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def save_manifest(m: PackageManifest, out_dir: Path) -> Path:
    path = out_dir / MANIFEST_JSON
    atomic_write_json(path, m.to_dict())
    return path


def load_manifest(out_dir: Path) -> PackageManifest | None:
    path = out_dir / MANIFEST_JSON
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return PackageManifest.from_dict(json.load(f))


def documents_dir(out_dir: Path, source_id: str) -> Path:
    """程序内部：全量 rendition + 对齐表，按身份哈希寻址（稳定，供程序跨 rendition 用）。"""
    return internal_dir(out_dir, "documents", source_id.split(":", 1)[1][:16])


def rendition_json_path(out_dir: Path, source_id: str, rendition_id: str) -> Path:
    return documents_dir(out_dir, source_id) / (rendition_id.split(":", 1)[1][:16] + ".json")


def rendition_md_path(out_dir: Path, source_id: str, rendition_id: str) -> Path:
    return documents_dir(out_dir, source_id) / (rendition_id.split(":", 1)[1][:16] + ".md")


# ---------------- 镜像视图（顶层，人类定位用） ----------------


def mirror_relpath(package_relative_path: str) -> str:
    """容器路径分隔符 :: → /，使产物目录结构镜像原包相对结构。

    "合同.pdf" → "合同.pdf"；"bundle.zip::a.txt" → "bundle.zip/a.txt"。
    """
    return package_relative_path.replace("::", "/")


def rendering_md_path(out_dir: Path, package_relative_path: str) -> Path:
    """最优 rendition 的人类可读视图，**顶层**，路径镜像原件（与原件并排可对比）。"""
    return out_dir / (mirror_relpath(package_relative_path) + ".md")


def rendering_json_path(out_dir: Path, package_relative_path: str) -> Path:
    return out_dir / (mirror_relpath(package_relative_path) + ".json")


def viewable_path(out_dir: Path, package_relative_path: str) -> Path:
    """派生物（渲染图/帧/dxf/svg 等）的**实际可看文件**在顶层镜像的同名位置。

    用于把 .adg/expanded 里的派生二进制复制到顶层，让人直接打开。
    """
    return out_dir / mirror_relpath(package_relative_path)


def alignment_path(out_dir: Path, source_id: str, rendition_a: str, rendition_b: str) -> Path:
    a = rendition_a.split(":", 1)[1][:16]
    b = rendition_b.split(":", 1)[1][:16]
    return documents_dir(out_dir, source_id) / f"alignment-{a}-{b}.json"


def load_rendition(out_dir: Path, source_id: str, rendition_id: str) -> dict | None:
    path = rendition_json_path(out_dir, source_id, rendition_id)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_rendition_docs(out_dir: Path, source_id: str) -> list[dict]:
    d = documents_dir(out_dir, source_id)
    docs = []
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            if p.name.startswith("alignment-"):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    docs.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
    return docs


# ---------------- Markdown 渲染 ----------------

_KIND_LABEL = {
    "pdf": "PDF",
    "docx": "DOCX",
    "doc": "DOC",
    "xlsx": "XLSX",
    "xls": "XLS",
    "csv": "CSV",
    "image": "图片",
    "text": "文本",
    "html": "HTML",
    "email": "邮件",
    "chat": "聊天",
    "audio": "音频",
    "video": "视频",
    "archive": "压缩包",
    "dxf": "DXF",
    "dwg": "DWG",
    "presentation": "演示文稿",
    "database": "数据库",
    "executable": "可执行文件",
    "unknown": "未知",
}

_STATUS_LABEL = {
    "discovered": "已发现",
    "parsed": "已解析",
    "parsed_with_warnings": "已解析（有警告）",
    "failed": "解析失败",
    "unresolved": "未解决",
    "unsupported": "不支持",
    "duplicate": "重复",
}

_OVERALL_ICON = {"pass": "✅", "pass_with_warnings": "⚠️", "fail": "❌"}


def render_manifest_md(m: PackageManifest) -> str:
    lines: list[str] = []
    q = m.quality
    overall = q.get("overall", "pass")
    lines.append(f"# 材料包清单：{m.package_root_name}")
    lines.append("")
    lines.append(f"- 包身份：`{m.package_id[:23]}…`")
    lines.append(f"- 生成时间：{m.created_at}")
    lines.append(f"- 总体状态：{_OVERALL_ICON.get(overall, overall)} {overall}")
    lines.append(f"- 完整入账：{'是' if m.integrity.get('all_items_accounted') else '否'}")
    lines.append(
        f"- 条目总数：{m.stats.get('total_items', 0)}（去重节省 {m.stats.get('dedup_saved', 0)}，总字节 {m.stats.get('total_bytes', 0)}）"
    )
    cov = q.get("extraction_coverage", 0.0)
    lines.append(f"- 可靠读取覆盖率：{cov * 100:.1f}%")
    lines.append("")
    by_kind = m.stats.get("by_kind", {})
    if by_kind:
        lines.append("## 类型分布")
        lines.append("")
        lines.append("| 类型 | 数量 |")
        lines.append("|------|------|")
        for k, n in by_kind.items():
            lines.append(f"| {_KIND_LABEL.get(k, k)} | {n} |")
        lines.append("")

    unresolved = m.integrity.get("unresolved_items", [])
    if unresolved:
        lines.append(f"## 未可靠读取（{len(unresolved)} 项）")
        lines.append("")
        lines.append("| 文件 | 阶段 | 原因 | 建议 |")
        lines.append("|------|------|------|------|")
        for u in unresolved:
            action = u.get("suggested_action") or "—"
            lines.append(f"| `{u['package_relative_path']}` | {u.get('stage', '')} | {u['reason']} | {action} |")
        lines.append("")

    for key, title in (
        ("duplicates", "重复文件"),
        ("empties", "空文件"),
        ("encrypted", "加密文件"),
        ("corrupted", "损坏文件"),
        ("unsupported", "不支持格式"),
        ("low_confidence", "低置信识别"),
    ):
        entries = q.get(key, [])
        if not entries:
            continue
        lines.append(f"## {title}（{len(entries)}）")
        lines.append("")
        if key == "duplicates":
            for g in entries:
                lines.append(f"- `{g['paths'][0]}` ⇔ {', '.join('`' + p + '`' for p in g['paths'][1:])}")
        else:
            for p in entries:
                lines.append(f"- `{p}`")
        lines.append("")

    lines.append(f"## 全部条目（{len(m.items)}）")
    lines.append("")
    lines.append("| # | 文件 | 类型 | 状态 | 最优质量 |")
    lines.append("|---|------|------|------|----------|")
    for i, it in enumerate(m.items, 1):
        bq = it.best_quality or "—"
        lines.append(
            f"| {i} | `{it.package_relative_path}` | {_KIND_LABEL.get(it.kind, it.kind)} | {_STATUS_LABEL.get(it.status, it.status)} | {bq} |"
        )
    lines.append("")
    return "\n".join(lines)
