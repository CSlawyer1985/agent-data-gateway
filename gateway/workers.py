"""Worker 注册与执行（spec 04）。

三类执行体：
- LiteWorker      主仓库内纯 stdlib 解析器（inproc），由 packs.legal 注册
- SubprocessWorker 本地子进程 worker（worker.json + adapter.py，各自 venv）
- DockerWorker     容器 worker（默认沙箱形态）

编排器与外部 worker 只讲 Worker Protocol（JSON Lines over stdio）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import PROTOCOL_VERSION
from .errors import WorkerFailed, WorkerUnavailable
from .hardware import Environment
from .security import load_policy

REPO_ROOT = Path(__file__).resolve().parent.parent

# 解析函数签名：(source_path: Path, *, kind: str, format: str|None, options: dict,
#                 limits: dict, work_dir: Path) -> fragment dict
LiteParseFn = Callable[..., dict]


@dataclass
class WorkerInfo:
    name: str
    version: str
    role: str  # parse / transform / enrich
    kinds: list[str]
    formats: list[str] | None
    requirements: dict
    backend: str  # inproc / subprocess / docker
    maturity: str = "experimental"
    license: str | None = None
    weight: str = "heavy"  # light / heavy
    notes: str = ""
    # inproc
    parse_fn: LiteParseFn | None = None
    # subprocess
    adapter_path: Path | None = None
    venv_python: Path | None = None
    python_requires: list[str] = field(default_factory=list)
    probe_import: str | None = None
    probe_command: list[str] | None = None
    # docker
    docker_image: str | None = None
    manifest_path: Path | None = None

    @property
    def worker_id(self) -> str:
        return f"{self.backend}:{self.name}"


class WorkerRegistry:
    def __init__(self) -> None:
        self.workers: dict[str, WorkerInfo] = {}
        self._availability_cache: dict[str, tuple[bool, str]] = {}

    # ---------- 注册 ----------

    def register_lite(self, info: WorkerInfo) -> None:
        info.backend = "inproc"
        info.weight = "light"
        self.workers[info.name] = info

    def load_external(self, extra_dirs: list[Path] | None = None) -> None:
        """扫描 workers/*/worker.json（repo + $ADG_WORKERS_DIR + ~/.adg/workers）。"""
        dirs: list[Path] = [REPO_ROOT / "workers"]
        env_dir = os.environ.get("ADG_WORKERS_DIR")
        if env_dir:
            dirs.append(Path(env_dir))
        dirs.append(Path.home() / ".adg" / "workers")
        dirs.extend(extra_dirs or [])
        for base in dirs:
            if not base.is_dir():
                continue
            for manifest_path in sorted(base.glob("*/worker.json")):
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        spec = json.load(f)
                    info = self._info_from_spec(spec, manifest_path)
                    if info.name in self.workers:
                        continue  # lite 优先；先注册者胜
                    self.workers[info.name] = info
                except (json.JSONDecodeError, OSError, KeyError):
                    continue

    def _info_from_spec(self, spec: dict, manifest_path: Path) -> WorkerInfo:
        handles = spec.get("handles", [])
        kinds = [h["kind"] for h in handles]
        formats = None
        for h in handles:
            if h.get("formats"):
                formats = (formats or []) + h["formats"]
        role = handles[0].get("role", "parse") if handles else "parse"
        sub = spec.get("subprocess") or {}
        dock = spec.get("docker") or {}
        backend = "docker" if dock.get("image") else "subprocess"
        return WorkerInfo(
            name=spec["name"],
            version=spec.get("version", "0.0.0"),
            role=role,
            kinds=kinds,
            formats=formats,
            requirements=spec.get("requirements", {}),
            backend=backend,
            maturity=spec.get("maturity", "experimental"),
            license=spec.get("license"),
            weight=spec.get("weight", "heavy"),
            notes=spec.get("notes", ""),
            adapter_path=(manifest_path.parent / sub.get("adapter", "adapter.py")) if sub else None,
            venv_python=(manifest_path.parent / sub.get("venv", ".venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python")) if sub else None,
            python_requires=spec.get("python_requires", []),
            probe_import=(sub.get("probe_import") if sub else None),
            probe_command=(sub.get("probe_command") if sub else None),
            docker_image=dock.get("image"),
            manifest_path=manifest_path,
        )

    # ---------- 查询 ----------

    def workers_for(self, kind: str, *, role: str = "parse") -> list[WorkerInfo]:
        return [w for w in self.workers.values() if w.role == role and kind in w.kinds]

    def get(self, name: str) -> WorkerInfo | None:
        return self.workers.get(name)

    # ---------- 可用性 ----------

    def availability(self, w: WorkerInfo, env: Environment | None = None, *, refresh: bool = False) -> tuple[bool, str]:
        key = f"{w.name}:{w.backend}"
        if not refresh and key in self._availability_cache:
            return self._availability_cache[key]
        ok, reason = self._check_availability(w, env)
        self._availability_cache[key] = (ok, reason)
        return ok, reason

    def _check_availability(self, w: WorkerInfo, env: Environment | None) -> tuple[bool, str]:
        if w.backend == "inproc":
            return True, "stdlib 内置"
        if w.backend == "docker":
            if not shutil.which("docker"):
                return False, "docker 不在 PATH"
            image = w.docker_image or ""
            rc = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=15,
            ).returncode
            if rc != 0:
                return False, f"docker 镜像未构建: {image}（见 workers/{w.name}/README）"
            return True, "docker 镜像可用"
        # subprocess
        if not w.adapter_path or not w.adapter_path.exists():
            return False, "adapter 缺失"
        if w.probe_command:
            exe = w.probe_command[0]
            if not shutil.which(exe):
                return False, f"二进制不在 PATH: {exe}（adg setup --worker {w.name} 查看安装指引）"
            try:
                rc = subprocess.run(w.probe_command, capture_output=True, timeout=15).returncode
            except (OSError, subprocess.TimeoutExpired):
                return False, f"探测命令执行失败: {' '.join(w.probe_command)}"
            if rc != 0:
                return False, f"探测命令返回非零: {' '.join(w.probe_command)}"
        if w.venv_python and w.venv_python.exists():
            py = str(w.venv_python)
        else:
            py = sys.executable  # 允许装进当前解释器（adg setup --into-current）
        if w.probe_import:
            rc = subprocess.run(
                [py, "-c", f"import {w.probe_import}"],
                capture_output=True,
                timeout=30,
            ).returncode
            if rc != 0:
                return False, f"依赖未安装: {w.probe_import}（adg setup --worker {w.name}）"
        return True, "subprocess 可用"


# ---------- 协议执行 ----------


def _jsonl_send(fp, obj: dict) -> None:
    fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
    fp.flush()


def run_subprocess_protocol(
    w: WorkerInfo,
    request: dict,
    *,
    timeout: int,
    max_output_bytes: int,
    env_passthrough: list[str],
) -> dict:
    """以 JSONL stdio 协议驱动 subprocess worker，读到终态 parse_result。"""
    py = str(w.venv_python) if (w.venv_python and w.venv_python.exists()) else sys.executable
    env = {k: os.environ[k] for k in env_passthrough if k in os.environ}
    env["ADG_PROTOCOL_VERSION"] = PROTOCOL_VERSION
    proc = subprocess.Popen(
        [py, str(w.adapter_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(w.adapter_path.parent),
    )
    assert proc.stdin and proc.stdout

    state = {"timed_out": False}

    def _watchdog():
        state["timed_out"] = True
        if proc.poll() is None:
            proc.kill()

    timer = threading.Timer(timeout, _watchdog)

    result: dict | None = None
    produced = 0
    try:
        timer.start()
        _jsonl_send(proc.stdin, request)
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            produced += len(line.encode("utf-8"))
            if produced > max_output_bytes:
                result = {"type": "parse_result", "status": "failed", "error": {"kind": "resource_limit", "message": "worker 输出超限"}}
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "parse_result":
                result = msg
                break
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
        stderr_tail = ""
        if proc.stderr:
            try:
                stderr_tail = proc.stderr.read()[-2000:]
            except OSError:
                pass
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
    if result is None:
        kind = "timeout" if state["timed_out"] else "crash"
        return {
            "type": "parse_result",
            "status": "failed",
            "error": {"kind": kind, "message": f"worker 未返回终态（{kind}）。stderr: {stderr_tail[-500:]}"},
        }
    return result


def run_docker_protocol(w: WorkerInfo, request: dict, *, source_path: Path, work_dir: Path, policy: dict) -> dict:
    """docker 后端：只读挂载原件、network=none、资源限额、tmpfs work_dir。"""
    sb = policy["worker_sandbox"]["docker"]
    timeout = policy["worker_sandbox"]["subprocess"]["timeout_seconds"]
    image = w.docker_image or ""
    container_work = "/adg-work"
    container_source = "/adg-source/input"
    req = dict(request)
    req["source_path"] = container_source + "/" + source_path.name
    req["work_dir"] = container_work
    cmd = [
        "docker", "run", "--rm", "-i",
        "--network", sb.get("network", "none"),
        "--memory", f"{sb.get('memory_limit_mb', 8192)}m",
        "--cpus", str(sb.get("cpus", 4)),
        "--pids-limit", str(sb.get("pids_limit", 256)),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "-v", f"{source_path}:{container_source}/{source_path.name}:ro",
        "-v", f"{work_dir}:{container_work}",
    ]
    if sb.get("read_only_root"):
        cmd.append("--read-only")
    if sb.get("tmpfs_work_dir"):
        cmd += ["--tmpfs", "/tmp:rw,noexec,nosuid,size=512m"]
    cmd.append(image)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdin and proc.stdout
    result: dict | None = None
    try:
        _jsonl_send(proc.stdin, req)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "parse_result":
                result = msg
                break
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    if result is None:
        return {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": "容器 worker 未返回终态"}}
    # 路径翻译回主机
    if result.get("fragment_path"):
        result["fragment_path"] = str(work_dir / Path(result["fragment_path"]).name)
    for of in result.get("output_files") or []:
        of["path"] = str(work_dir / Path(of["path"]).name)
    return result


def execute_worker(
    w: WorkerInfo,
    request: dict,
    *,
    source_path: Path,
    work_dir: Path,
    policy: dict | None = None,
) -> dict:
    """统一执行入口。返回协议 parse_result（含 fragment 或 fragment_path）。"""
    policy = policy or load_policy()
    if w.backend == "inproc":
        assert w.parse_fn is not None
        limits = policy.get("parser_limits", {})
        try:
            fragment = w.parse_fn(
                source_path,
                kind=request.get("kind", ""),
                format=request.get("format"),
                options=request.get("options") or {},
                limits=limits,
                work_dir=work_dir,
            )
            return {"type": "parse_result", "status": "ok", "fragment": fragment}
        except WorkerUnavailable:
            raise
        except WorkerFailed as e:
            return {"type": "parse_result", "status": "failed", "error": {"kind": e.kind, "message": str(e)}}
        except Exception as e:  # lite parser 异常不得击穿编排器
            return {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
    timeout = policy["worker_sandbox"]["subprocess"]["timeout_seconds"]
    max_out = policy["worker_sandbox"]["subprocess"]["max_output_bytes"]
    env_pass = policy["worker_sandbox"]["subprocess"]["env_passthrough"]
    if w.backend == "docker":
        ok, reason = shutil.which("docker") is not None, "docker 不在 PATH"
        if not ok:
            raise WorkerUnavailable(reason)
        return run_docker_protocol(w, request, source_path=source_path, work_dir=work_dir, policy=policy)
    if w.backend == "subprocess":
        if not w.adapter_path or not w.adapter_path.exists():
            raise WorkerUnavailable("adapter 缺失")
        return run_subprocess_protocol(w, request, timeout=timeout, max_output_bytes=max_out, env_passthrough=env_pass)
    raise WorkerUnavailable(f"未知后端 {w.backend}")


def make_work_dir(base: Path, job_id: str) -> Path:
    from .manifest import internal_dir

    d = internal_dir(base, "work", job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def temp_work_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="adg-worker-"))
