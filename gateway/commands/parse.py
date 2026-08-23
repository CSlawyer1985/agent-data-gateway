"""adg parse — 解析：特征路由 + 降级链 → EvidenceDocument → 回写 manifest。"""

from __future__ import annotations

from pathlib import Path

from .. import routing
from ..dispatcher import Dispatcher, ParseOptions
from ..errors import ADGError
from ..index import build_index
from ..manifest import load_manifest
from ._common import build_registry, get_env, out_hint, print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("parse", help="解析材料包 → EvidenceDocument（自动特征路由+降级）")
    p.add_argument("path", help="材料包目录（或单文件，须先 adg scan）")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--only-kind", help="只解析指定类型（如 pdf）")
    p.add_argument("--resume", action="store_true", help="跳过已有成功 rendition 的项")
    p.add_argument("--force", action="store_true", help="全部重解析")
    p.add_argument("--no-abs-log", action="store_true", help="关闭绝对路径私有日志")
    p.add_argument("--keep-work", action="store_true", help="保留 worker 临时目录（排障用）")
    p.add_argument("--worker-option", action="append", default=[], help="worker 选项 k=v（可多次），如 password=xxx")
    p.add_argument("--chain", action="append", default=[], help="强制解析链 kind=w1,w2（可多次），跳过特征路由")
    p.add_argument(
        "--profile",
        choices=routing.PROFILES,
        default=routing.DEFAULT_PROFILE,
        help="fast 挑链上轻的工具，尽快出一个能用的结果；balanced 整条链、用效果最好的可用工具；"
             "quality 多条工作流并跑，各自出结果。ADG 不判断哪个更好——多跑只是多给你几份材料",
    )
    p.add_argument("--no-index", action="store_true", help="解析后不建检索索引（之后可单独 adg index）")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def _parse_worker_options(pairs: list[str]) -> dict:
    out: dict = {}
    for pair in pairs:
        if "=" in pair:
            k, _, v = pair.partition("=")
            out[k.strip()] = v
    return out


def _parse_chain_override(pairs: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for pair in pairs:
        if "=" in pair:
            k, _, v = pair.partition("=")
            out[k.strip()] = [w.strip() for w in v.split(",") if w.strip()]
    return out


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    manifest = load_manifest(out_dir)
    if manifest is None:
        summary_line(f"未找到 manifest（{out_hint(out_dir)}/manifest.json）——请先运行 adg scan {args.path}")
        return 2

    registry = build_registry()
    options = ParseOptions(
        only_kind=args.only_kind,
        resume=args.resume,
        force=args.force,
        no_abs_log=args.no_abs_log,
        keep_work=args.keep_work,
        worker_options=_parse_worker_options(args.worker_option),
        chain_override=_parse_chain_override(args.chain),
        profile=args.profile,
    )
    dispatcher = Dispatcher(package_path, out_dir, manifest, registry, env=get_env(), options=options)
    summary = dispatcher.run()

    overall = summary.exit_overall

    # 解析后顺手建索引：让 adg search / anchor 立刻可用（失败不影响解析结论）
    index_stats = None
    index_error = None
    if not args.no_index:
        try:
            index_stats = build_index(out_dir, manifest=load_manifest(out_dir))
        except ADGError as e:
            index_error = str(e)

    if args.json:
        print_json(
            {
                "run_id": summary.run_id,
                "parsed": summary.parsed,
                "failed": summary.failed,
                "unresolved": summary.unresolved,
                "skipped": summary.skipped,
                "new_items": summary.new_items,
                "alignments": summary.alignments,
                "overall": overall,
                "index": index_stats.to_dict() if index_stats else None,
                "index_error": index_error,
            }
        )
    else:
        summary_line(
            f"parse 完成: 解析 {summary.parsed}，失败 {summary.failed}，未解决 {summary.unresolved}，"
            f"跳过 {summary.skipped}，回灌新项 {summary.new_items}，对齐 {summary.alignments}"
            f" → {out_hint(out_dir)}（{overall}）"
        )
        if index_stats is not None:
            print(f"  🔎 索引就绪: {index_stats.elements} 个元素可检索（adg search）")
        elif index_error:
            print(f"  ⚠️ 索引未建立: {index_error}（可稍后 adg index）")
    return {"pass": 0, "pass_with_warnings": 1, "fail": 2}.get(overall, 2)
