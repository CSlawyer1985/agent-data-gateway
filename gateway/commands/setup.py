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

import os
import subprocess
import sys
from pathlib import Path

from .. import mirrors
from ..gap import analyze_manifest_gaps
from ..hardware import detect_environment
from ..manifest import load_manifest
from ._common import build_registry, out_hint, resolve_out_dir, summary_line

PIP_INDEX_ENV = mirrors.PIP_INDEX_ENV

_BINARY_HINTS = {
    "officecli": "brew install officecli",
    "qpdf": "brew install qpdf",
    "libredwg": "brew install libredwg（LibreDWG，提供 dwg2dxf）",
    "libpff": "brew install libpff（提供 pffexport）",
    "tika": "安装 Java + 下载 tika-server JAR，并设 ADG_TIKA_JAR 环境变量",
    "whisper": (
        "brew install ffmpeg（ffmpeg 二进制）。模型国内路径：huggingface.co 直连慢/易断，"
        "建议从 ModelScope 下载 Systran/faster-whisper-small 后，"
        "parse 时 --worker-option model=<模型目录> 注入（详见 workers/whisper/worker.json notes）"
    ),
}


def resolve_index_url(cli_value: str | None) -> str | None:
    """纯优先级解析：CLI --index-url > 环境变量 > None。**不联网**。

    自动测速在 run() 里进行（见 mirrors.resolve_pip_index），这样这个函数保持可单测、
    测试套件也不会去连网。只影响 setup 安装动作，不写全局 pip 配置、不影响 worker 沙箱。
    """
    return mirrors.resolve_pip_index(cli_value, probe=False).url


def build(sub) -> None:
    p = sub.add_parser("setup", help="安装/构建 worker（支持按材料包诊断后一键补装）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--worker", help="指定 worker 名")
    g.add_argument("--all", action="store_true", help="全部外部 worker")
    g.add_argument("--lite", action="store_true", help="仅轻量 worker（weight=light）")
    g.add_argument("--package", help="按材料包诊断缺口，只装本包需要且缺失的（推荐）")
    g.add_argument("--missing", action="store_true", help="用最近一次 scan 的 manifest 诊断缺口安装")
    g.add_argument("--auto", action="store_true",
                   help="一键装齐所有免系统依赖的 worker（推荐给不熟悉命令行的用户）")
    p.add_argument("--out", help="输出目录（--package 时定位 manifest）")
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的操作")
    p.add_argument("--yes", action="store_true", help="跳过确认提示")
    p.add_argument(
        "--index-url",
        help=f"pip 索引 URL。未指定时读环境变量 {PIP_INDEX_ENV}；两者皆空则自动测速选最快的源",
    )
    p.add_argument("--no-probe", action="store_true", help="不自动测速选源，直接用 pip 默认源")
    p.add_argument("--no-models", action="store_true", help="跳过模型预热（音视频转写首次使用时会现下）")
    p.set_defaults(handler=run)


def run(args) -> int:
    route = mirrors.resolve_pip_index(
        getattr(args, "index_url", None), probe=not getattr(args, "no_probe", False)
    )
    index_url = route.url
    print(f"  软件源: {index_url or 'pip 默认（pypi.org）'}（{route.source}）")
    if not route.reachable:
        print("  ⚠️ 所有候选源都探测失败——网络可能不通，安装大概率会失败")

    if getattr(args, "auto", False):
        return _setup_auto(args, index_url)

    # ---- 包级缺口安装 ----
    if args.package or args.missing:
        return _setup_from_gap(args, index_url)

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
    rc = _install_targets(targets, args.dry_run, args.yes, index_url)
    # 模型预热同样适用于 --worker/--all/--lite：装完就把模型下好，
    # 免得第一次解析在一个声称 network=false 的 worker 里静默等下载
    if not args.dry_run and not getattr(args, "no_models", False):
        _prefetch_models(build_registry(), detect_environment(), only={w.name for w in targets})
    return rc


def _setup_auto(args, index_url: str | None) -> int:
    """一键装齐所有免系统依赖的 worker。

    面向不熟悉命令行的用户：不用先搞懂 --lite/--all 的区别，也不用知道哪些 worker 需要
    brew/Docker。装完给一张「现在能读什么、还差什么」的表——装到什么程度就诚实报到什么程度。
    """
    registry = build_registry()
    installable, needs_system, opt_in = [], [], []
    for w in sorted(registry.workers.values(), key=lambda x: x.name):
        if w.backend == "inproc":
            continue
        if _in_auto_bundle(w):
            installable.append(w)
        elif w.python_requires and w.auto_install is False:
            opt_in.append(w)   # 能 pip 装，但体积/依赖不适合默认档
        else:
            needs_system.append(w)

    env = detect_environment()
    todo = [w for w in installable if not registry.availability(w, env)[0]]
    already = len(installable) - len(todo)
    print(f"  免系统依赖的 worker: {len(installable)} 个（已可用 {already}，待装 {len(todo)}）")
    if not todo:
        summary_line("所有免系统依赖的 worker 都已可用")
    else:
        rc = _install_targets(todo, args.dry_run, args.yes, index_url)
        if rc != 0 and not args.dry_run:
            print("  ⚠️ 部分 worker 安装失败，下方能力表已反映实际结果")

    if not args.dry_run and not getattr(args, "no_models", False):
        _prefetch_models(registry, detect_environment())

    _print_capability_summary(registry, needs_system, opt_in)
    return 0


def _prefetch_models(registry, env, only: set[str] | None = None) -> None:
    """把「首次运行才下模型」提前到安装阶段。

    whisper 等 worker 声明 network=false，但模型是运行时下载的——不预热的话，第一次解析
    音视频会在一个声称不联网的 worker 里静默等待下载。预热让那句声明成真。
    """
    import json as _json

    targets = []
    for w in sorted(registry.workers.values(), key=lambda x: x.name):
        if not w.manifest_path:
            continue
        manifest = _json.loads(w.manifest_path.read_text(encoding="utf-8"))
        prefetch = manifest.get("prefetch")
        if only is not None and w.name not in only:
            continue
        if prefetch and registry.availability(w, env)[0]:
            targets.append((w, prefetch))
    if not targets:
        return

    model_env, route = mirrors.model_download_env()
    print(f"\n  模型线路: {route.url or 'HuggingFace 官方'}（{route.source}）")
    if not route.reachable:
        print(f"  ⚠️ {mirrors.MODELSCOPE_HINT}")
        return
    for w, prefetch in targets:
        venv_python = w.manifest_path.parent / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
        if not venv_python.is_file():
            continue
        size = prefetch.get("approx_size") or "未知大小"
        print(f"  {w.name}: 预热模型（{size}），首次较慢...")
        try:
            subprocess.run([str(venv_python), "-c", prefetch["python"]],
                           env=model_env, check=True, timeout=3600)
            print(f"  {w.name}: 模型就绪")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"  ⚠️ {w.name}: 模型预热失败（{type(e).__name__}）——首次解析时会重试下载")


def _in_auto_bundle(w) -> bool:
    """一键档的准入：worker.json 显式声明优先，未声明时按「无系统依赖且轻量」推导。

    显式声明放在 worker.json 而不是这里硬编码——与路由表同样的纪律：能力事实归清单，
    代码只执行规则。
    """
    if w.auto_install is not None:
        return bool(w.auto_install)
    if not w.python_requires:
        return False          # 需要系统二进制
    if w.backend == "docker":
        return False
    return w.weight == "light"


def _print_capability_summary(registry, needs_system, opt_in=()) -> None:
    env = detect_environment()
    missing = [(w, _BINARY_HINTS.get(w.name, "见 worker.json notes")) for w in needs_system
               if not registry.availability(w, env)[0]]
    print("\n  ── 本机能力 ──")
    usable = [w.name for w in registry.workers.values() if registry.availability(w, env)[0]]
    print(f"  可用 worker {len(usable)} 个；内置 lite parser 无需安装即可读 txt/md/csv/html/docx/xlsx/pptx/邮件/聊天记录")
    if missing:
        print(f"  还差 {len(missing)} 个需要系统工具的 worker（不装也能用，只是这些格式读不了）：")
        for w, hint in missing:
            print(f"    · {w.name}: {hint}")
    if opt_in:
        print(f"  另有 {len(opt_in)} 个重型 worker 未进一键档（体积大或需 Docker），需要时单独装：")
        for w in opt_in:
            reason = _auto_install_reason(w)
            print(f"    · adg setup --worker {w.name}{('  —— ' + reason) if reason else ''}")
    print("  用 adg check 看完整清单，adg doctor <材料包> 按实际材料诊断该补哪个")


def _auto_install_reason(w) -> str:
    import json as _json

    if not w.manifest_path:
        return ""
    try:
        return _json.loads(w.manifest_path.read_text(encoding="utf-8")).get("auto_install_reason", "")
    except (OSError, ValueError):
        return ""


def _setup_from_gap(args, index_url: str | None) -> int:
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
    rc = _install_targets(targets, args.dry_run, args.yes, index_url)
    if rc == 0 and not args.dry_run:
        summary_line(f"补装完成。重跑: adg parse {args.package}")
    return rc


def _install_targets(targets, dry_run: bool, yes: bool, index_url: str | None = None) -> int:
    if not targets:
        summary_line("无可安装目标")
        return 0
    failures: list[str] = []
    for w in targets:
        venv_dir = w.manifest_path.parent / ".venv" if w.manifest_path else None
        if w.python_requires:
            if dry_run:
                rel = str(venv_dir.relative_to(Path.cwd())) if venv_dir and Path.cwd() in venv_dir.parents else str(venv_dir)
                idx = f"（镜像: {index_url}）" if index_url else ""
                print(f"  [dry-run] {w.name}: venv {rel} + pip install{idx} {' '.join(w.python_requires)}")
                continue
            ok = _install_python_worker(w.name, venv_dir, w.python_requires, index_url)
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


def _install_python_worker(name: str, venv_dir: Path | None, requires: list[str], index_url: str | None = None) -> bool:
    if venv_dir is None:
        return False
    print(f"  {name}: 创建 venv {venv_dir.name} ...")
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  {name}: venv 创建失败: {e}")
        return False
    pip = venv_dir / ("Scripts/pip.exe" if sys.platform == "win32" else "bin/pip")
    mirror_args = ["-i", index_url] if index_url else []
    idx_note = f" -i {index_url}" if index_url else ""
    print(f"  {name}: pip install{idx_note} {' '.join(requires)} ...")
    try:
        subprocess.run(
            [str(pip), "install", "--disable-pip-version-check", *mirror_args, *requires],
            check=True, timeout=1800,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(f"  {name}: pip install 失败（可能是 Python {sys.version_info.major}.{sys.version_info.minor} 无预编译 wheel）")
        if index_url:
            manual = f"{pip} install -i {index_url} {' '.join(requires)}"
        else:
            manual = f"{pip} install {' '.join(requires)}（网络慢/哈希不匹配时：设 {PIP_INDEX_ENV}=<国内镜像> 或加 --index-url）"
        print(f"  {name}: 可手动: {manual}")
        return False
