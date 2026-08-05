"""adg setup — 引导安装/构建 worker。

三种形态：
- adg setup --worker <名>           装/查单个 worker
- adg setup --all / --lite          按权重批量
- adg setup --package <材料包>       按实际材料诊断缺口，只装本包需要且本机缺失的（推荐）
- adg setup --missing                同上，但用最近一次 scan 的 manifest

python 依赖类：建 workers/<name>/.venv 并 pip install。
二进制工具类：打印 brew/apt 指引（不自动 brew，避免改动系统）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..gap import analyze_manifest_gaps
from ..hardware import detect_environment
from ..manifest import load_manifest
from ._common import build_registry, out_hint, resolve_out_dir, summary_line

_BINARY_HINTS = {
    "officecli": "brew install officecli",
    "qpdf": "brew install qpdf",
    "libredwg": "brew install libredwg（LibreDWG，提供 dwg2dxf）",
    "libpff": "brew install libpff（提供 pffexport）",
    "tika": "安装 Java + 下载 tika-server JAR，并设 ADG_TIKA_JAR 环境变量",
    "whisper": "brew install ffmpeg（ffmpeg 二进制），模型首次运行下载",
}


def build(sub) -> None:
    p = sub.add_parser("setup", help="安装/构建 worker（支持按材料包诊断后一键补装）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--worker", help="指定 worker 名")
    g.add_argument("--all", action="store_true", help="全部外部 worker")
    g.add_argument("--lite", action="store_true", help="仅轻量 worker（weight=light）")
    g.add_argument("--package", help="按材料包诊断缺口，只装本包需要且缺失的（推荐）")
    g.add_argument("--missing", action="store_true", help="用最近一次 scan 的 manifest 诊断缺口安装")
    p.add_argument("--out", help="输出目录（--package 时定位 manifest）")
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的操作")
    p.add_argument("--yes", action="store_true", help="跳过确认提示")
    p.set_defaults(handler=run)


def run(args) -> int:
    # ---- 包级缺口安装 ----
    if args.package or args.missing:
        return _setup_from_gap(args)

    # ---- 传统形态：--worker / --all / --lite ----
    registry = build_registry()
    targets = []
    for w in sorted(registry.workers.values(), key=lambda x: x.name):
        if w.backend == "inproc":
            continue
        if args.worker and w.name != args.worker:
            continue
        if args.lite and w.weight != "light":
            continue
        targets.append(w)
    if args.worker and not targets:
        summary_line(f"未找到外部 worker: {args.worker}")
        return 2
    return _install_targets(targets, args.dry_run, args.yes)


def _setup_from_gap(args) -> int:
    if args.package:
        package_path = Path(args.package).resolve()
        out_dir = resolve_out_dir(args, package_path)
    else:
        # --missing：找最近一次的 out（无 package 无法定位）
        summary_line("--missing 需配合 --package <目录>（或先用 adg doctor <目录> 诊断）")
        return 2
    manifest = load_manifest(out_dir)
    if manifest is None:
        summary_line(f"未找到 manifest——先 adg doctor {args.package}（会自动 scan 并诊断）")
        return 2
    registry = build_registry()
    env = detect_environment()
    report = analyze_manifest_gaps(manifest, registry, env)
    if not report.install_plan:
        summary_line("诊断完成：无本机可装的缺口 worker（剩余缺口见 adg doctor 报告）")
        return 0

    print(f"adg: 本包需要、本机缺失的 worker：{', '.join(report.install_plan)}")
    affected = sum(g.count for g in report.by_kind.values() if g.installable)
    print(f"     补装后可多覆盖 {affected} 个文件（当前覆盖率 {(manifest.quality.get('extraction_coverage') or 0) * 100:.0f}%）")
    if not args.yes and not args.dry_run:
        print("     确认安装？加 --yes 跳过此提示；本过程建独立 venv，不污染系统 Python。")
        try:
            ans = input("     [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans not in ("y", "yes"):
            summary_line("已取消。可用 --dry-run 查看将执行的操作。")
            return 1

    targets = [w for w in registry.workers.values() if w.name in report.install_plan]
    rc = _install_targets(targets, args.dry_run, args.yes)
    if rc == 0 and not args.dry_run:
        summary_line(f"补装完成。重跑: adg parse {args.package}")
    return rc


def _install_targets(targets, dry_run: bool, yes: bool) -> int:
    if not targets:
        summary_line("无可安装目标")
        return 0
    failures: list[str] = []
    for w in targets:
        venv_dir = w.manifest_path.parent / ".venv" if w.manifest_path else None
        if w.python_requires:
            if dry_run:
                rel = str(venv_dir.relative_to(Path.cwd())) if venv_dir and Path.cwd() in venv_dir.parents else str(venv_dir)
                print(f"  [dry-run] {w.name}: venv {rel} + pip install {' '.join(w.python_requires)}")
                continue
            ok = _install_python_worker(w.name, venv_dir, w.python_requires)
            if not ok:
                failures.append(w.name)
        else:
            hint = _BINARY_HINTS.get(w.name, "见 worker.json notes")
            print(f"  {w.name}: 二进制/环境依赖，请手动安装: {hint}")
    if dry_run:
        summary_line(f"dry-run 完成（{len(targets)} 个目标）")
        return 0
    if failures:
        summary_line(f"完成，{len(failures)} 个失败: {', '.join(failures)}（多半是 Python 版本无预编译 wheel，见上方 pip 错误）")
        return 1
    summary_line(f"完成（{len(targets)} 个目标）。adg check 查看可用性。")
    return 0


def _install_python_worker(name: str, venv_dir: Path | None, requires: list[str]) -> bool:
    if venv_dir is None:
        return False
    print(f"  {name}: 创建 venv {venv_dir.name} ...")
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  {name}: venv 创建失败: {e}")
        return False
    pip = venv_dir / ("Scripts/pip.exe" if sys.platform == "win32" else "bin/pip")
    print(f"  {name}: pip install {' '.join(requires)} ...")
    try:
        subprocess.run([str(pip), "install", "--disable-pip-version-check", *requires], check=True, timeout=1800)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(f"  {name}: pip install 失败（可能是 Python {sys.version_info.major}.{sys.version_info.minor} 无预编译 wheel）")
        print(f"  {name}: 可手动: {pip} install {' '.join(requires)}")
        return False
