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
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from . import PROTOCOL_VERSION
from .errors import ContractViolation, SecurityViolation, WorkerFailed, WorkerUnavailable
from .hardware import Environment, check_requirements, detect_environment
from .schema import SchemaValidationError, validate_model
from .security import load_policy, resolve_worker_output

REPO_ROOT = Path(__file__).resolve().parent.parent

# 可写状态根。清单与 adapter 随包只读分发，venv 必须落在包外：
# uv tool/pipx 升级会重建环境，装在 site-packages 里的 worker 依赖会连带丢失。
ADG_HOME = Path(os.environ.get("ADG_HOME") or (Path.home() / ".adg"))
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9_.-]+")


def worker_venv_dir(name: str, manifest_path: Path | None, venv_name: str = ".venv") -> Path | None:
    """worker venv 的落点：清单旁已有则沿用，否则落在 ADG_HOME/workers/<name>/。"""
    if manifest_path is None:
        return None
    existing = manifest_path.parent / venv_name
    return existing if existing.is_dir() else ADG_HOME / "workers" / name / venv_name


def venv_python_of(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


_RESOLVE_SCRIPT = """
import importlib.metadata as meta, importlib.util, json, sys
out = {}
for name in json.loads(sys.argv[1]):
    try:
        out[name] = meta.version(name)
    except Exception:
        # 没有分发元数据不等于没装：再按模块名找一次，只查发现不执行模块顶层（spec 04）
        try:
            found = importlib.util.find_spec(name.replace("-", "_")) is not None
        except Exception:
            found = False
        out[name] = "" if found else None
print(json.dumps(out, sort_keys=True))
"""


def _resolve_requirements(python: str, requirements: list[str]) -> tuple[dict[str, str], list[str]]:
    """解析声明依赖的安装版本，返回 (已解析版本, 确定缺失的包)。

    只读包元数据、必要时只做模块发现，不执行模块顶层代码——重型 ML 包冷导入可能超过一分钟，
    不能把冷启动误判成依赖缺失（spec 04）。版本查不到但模块能找到的记为已装、不记版本，
    不把浮动安装伪装成可复现环境。子进程本身失败时返回 ({}, [])：「查不出来」不等于「没装」。
    """
    names = [match.group(0) for spec in requirements if (match := _REQUIREMENT_NAME.match(spec))]
    if not names:
        return {}, []
    try:
        result = subprocess.run(
            [python, "-c", _RESOLVE_SCRIPT, json.dumps(names)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=True,
        )
        resolved = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}, []
    if not isinstance(resolved, dict):
        return {}, []
    versions = {name: v for name, v in resolved.items() if v}
    missing = sorted(name for name, v in resolved.items() if v is None)
    return versions, missing


def _model_ready(python: str, verify: str) -> bool | None:
    """模型就绪探测：True=就绪，False=确定缺失，None=查不出来。

    退出码 1 表示确定缺失（脚本自己判定的），其余非零一律当作查不出来——
    探测脚本自身出错时不能反过来诬告用户没装模型。
    """
    try:
        rc = subprocess.run([python, "-c", verify], capture_output=True, timeout=120).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None
    if rc == 0:
        return True
    return False if rc == 1 else None


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
    # 是否进 `adg setup --auto` 的一键档：面向不熟悉命令行的用户，只收无系统依赖、
    # 体积可接受、装了就直接提升可读格式的 worker。缺省由 weight/docker/二进制依赖推导。
    auto_install: bool | None = None
    notes: str = ""
    # inproc
    parse_fn: LiteParseFn | None = None
    # subprocess
    adapter_path: Path | None = None
    venv_python: Path | None = None
    python_requires: list[str] = field(default_factory=list)
    probe_import: str | None = None
    probe_command: list[str] | None = None
    model_verify: str | None = None
    # docker
    docker_image: str | None = None
    manifest_path: Path | None = None
    runtime_versions: dict[str, str] = field(default_factory=dict)

    @property
    def worker_id(self) -> str:
        return f"{self.backend}:{self.name}"

    @property
    def venv_dir(self) -> Path | None:
        """venv 根目录（venv_python 是 <venv>/bin/python 或 <venv>/Scripts/python.exe）。"""
        return self.venv_python.parent.parent if self.venv_python else None


@dataclass(frozen=True)
class WorkerAvailability:
    available: bool
    reason: str  # available / unavailable / model_missing / hardware_unmet
    detail: str
    requirement_problems: tuple[str, ...] = ()


class WorkerRegistry:
    def __init__(self) -> None:
        self.workers: dict[str, WorkerInfo] = {}
        self.manifest_errors: list[tuple[Path, str]] = []  # 不合规清单：不注册，但必须能被看见
        self._availability_cache: dict[str, tuple[bool, str, str]] = {}
        self._environment: Environment | None = None

    # ---------- 注册 ----------

    def register_lite(self, info: WorkerInfo) -> None:
        info.backend = "inproc"
        info.weight = "light"
        self.workers[info.name] = info

    def load_external(self, extra_dirs: list[Path] | None = None) -> None:
        """扫描 workers/*/worker.json（repo + $ADG_WORKERS_DIR + ~/.adg/workers）。

        清单先过 Schema 再注册。不合规的清单不注册但会记进 manifest_errors——
        静默跳过等于这个 worker 从没存在过，用户只会看到"格式不支持"而不知道是清单写坏了。
        """
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
                    validate_model(spec, "worker_manifest.v0.1.json")
                    info = self._info_from_spec(spec, manifest_path)
                except (json.JSONDecodeError, OSError, KeyError, ContractViolation) as e:
                    self.manifest_errors.append((manifest_path, f"{type(e).__name__}: {e}"))
                    continue
                if info.name in self.workers:
                    continue  # lite 优先；先注册者胜
                self.workers[info.name] = info

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
        venv_dir = worker_venv_dir(spec["name"], manifest_path, sub.get("venv", ".venv")) if sub else None
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
            auto_install=spec.get("auto_install"),
            notes=spec.get("notes", ""),
            adapter_path=(manifest_path.parent / sub.get("adapter", "adapter.py")) if sub else None,
            venv_python=venv_python_of(venv_dir) if venv_dir else None,
            python_requires=spec.get("python_requires", []),
            probe_import=(sub.get("probe_import") if sub else None),
            probe_command=(sub.get("probe_command") if sub else None),
            model_verify=(spec.get("prefetch") or {}).get("verify"),
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
        status = self.availability_status(w, env, refresh=refresh)
        return status.available, status.detail

    def availability_status(
        self, w: WorkerInfo, env: Environment | None = None, *, refresh: bool = False
    ) -> WorkerAvailability:
        """运行时存在性与硬件要求的单一判定入口。"""
        key = f"{w.name}:{w.backend}"
        if refresh or key not in self._availability_cache:
            self._availability_cache[key] = self._check_availability(w, env)
        runtime_ok, runtime_detail, runtime_reason = self._availability_cache[key]
        if not runtime_ok:
            return WorkerAvailability(False, runtime_reason, runtime_detail)
        actual_env = env
        if actual_env is None:
            if self._environment is None:
                self._environment = detect_environment()
            actual_env = self._environment
        requirements_ok, problems = check_requirements(w.requirements, actual_env)
        if not requirements_ok:
            return WorkerAvailability(False, "hardware_unmet", "；".join(problems), tuple(problems))
        return WorkerAvailability(True, "available", runtime_detail)

    def _check_availability(self, w: WorkerInfo, env: Environment | None) -> tuple[bool, str, str]:
        if w.backend == "subprocess" and w.requirements.get("network") is True:
            return False, "需要联网的 worker 必须使用可执行网络策略的容器后端", "unavailable"
        if w.backend == "inproc":
            return True, "stdlib 内置", "available"
        if w.backend == "docker":
            if not shutil.which("docker"):
                return False, "docker 不在 PATH", "unavailable"
            image = w.docker_image or ""
            rc = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=15,
            ).returncode
            if rc != 0:
                return False, f"docker 镜像未构建: {image}（见 workers/{w.name}/README）", "unavailable"
            return True, "docker 镜像可用", "available"
        # subprocess
        if not w.adapter_path or not w.adapter_path.exists():
            return False, "adapter 缺失", "unavailable"
        if w.probe_command:
            exe = w.probe_command[0]
            if not shutil.which(exe):
                return False, f"二进制不在 PATH: {exe}（adg setup --worker {w.name} 查看安装指引）", "unavailable"
            try:
                rc = subprocess.run(w.probe_command, capture_output=True, timeout=15).returncode
            except (OSError, subprocess.TimeoutExpired):
                return False, f"探测命令执行失败: {' '.join(w.probe_command)}", "unavailable"
            if rc != 0:
                return False, f"探测命令返回非零: {' '.join(w.probe_command)}", "unavailable"
        own_venv = bool(w.venv_python and w.venv_python.exists())
        py = str(w.venv_python) if own_venv else sys.executable  # 允许装进当前解释器（adg setup --into-current）
        if w.probe_import:
            script = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
            try:
                rc = subprocess.run(
                    [py, "-c", script, w.probe_import],
                    capture_output=True,
                    timeout=15,
                ).returncode
            except (OSError, subprocess.TimeoutExpired):
                return False, f"依赖探测失败: {w.probe_import}（adg setup --worker {w.name}）", "unavailable"
            if rc != 0:
                return False, f"依赖未安装: {w.probe_import}（adg setup --worker {w.name}）", "unavailable"
        # probe_import 只验一个模块；声明的其余依赖同样决定能否真的跑起来。
        # 缺一个就是运行时必然失败，不能报「可用」（版本采集本来就要跑这一趟，不额外开销）。
        if w.python_requires:
            versions, missing = _resolve_requirements(py, w.python_requires)
            if missing:
                return False, f"依赖未安装: {'、'.join(missing)}（adg setup --worker {w.name}）", "unavailable"
            if versions and not w.runtime_versions:
                w.runtime_versions = versions
        # 声明了模型预取的 worker，权重缺失同样是运行时必然失败，且多数上游不在运行时补下载
        if w.model_verify:
            ready = _model_ready(py, w.model_verify)
            if ready is False:
                return False, f"模型未就绪: 权重未预取（adg setup --worker {w.name}）", "model_missing"
        detail = "subprocess 可用" if own_venv else "subprocess 可用（依赖在当前解释器，非 worker 专属 venv）"
        return True, detail, "available"


# ---------- 协议执行 ----------


def _jsonl_send(fp, obj: dict) -> None:
    fp.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    fp.flush()


def _protocol_failure(request: dict, kind: str, message: str) -> dict:
    return {
        "type": "parse_result",
        "job_id": str(request.get("job_id", "")),
        "status": "failed",
        "error": {"kind": kind, "message": message},
    }


def _validate_request(request: dict) -> str | None:
    try:
        validate_model(request, "worker_protocol.v0.1.json")
    except (SchemaValidationError, OSError, json.JSONDecodeError) as e:
        return f"parse_request 不符合 Worker Protocol: {e}"
    if request.get("type") != "parse_request":
        return f"期望 parse_request，实际 {request.get('type')!r}"
    if request.get("protocol_version") != PROTOCOL_VERSION:
        return f"不支持的协议版本 {request.get('protocol_version')!r}"
    return None


def _validate_worker_message(message: object, request: dict, *, terminal: bool) -> str | None:
    try:
        validate_model(message, "worker_protocol.v0.1.json")
    except (SchemaValidationError, OSError, json.JSONDecodeError) as e:
        return f"worker 消息不符合 Worker Protocol: {e}"
    assert isinstance(message, dict)
    expected_type = "parse_result" if terminal else "progress"
    if message.get("type") != expected_type:
        return f"解析期间不接受消息类型 {message.get('type')!r}"
    if message.get("job_id") != request.get("job_id"):
        return f"job_id 不匹配: {message.get('job_id')!r} != {request.get('job_id')!r}"
    return None


def _run_jsonl_command(
    command: list[str],
    request: dict,
    *,
    timeout: int,
    max_output_bytes: int,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict:
    """统一驱动 subprocess/docker，流式限制 stdout+stderr 并执行真实超时。"""
    request_error = _validate_request(request)
    if request_error:
        return _protocol_failure(request, "rejected", request_error)

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    events: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=16)
    stop_readers = threading.Event()

    def _emit(event: tuple[str, bytes | None]) -> bool:
        while not stop_readers.is_set():
            try:
                events.put(event, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _reader(name: str, stream) -> None:
        try:
            while not stop_readers.is_set():
                chunk = os.read(stream.fileno(), 64 * 1024)
                if not chunk:
                    break
                if not _emit((name, chunk)):
                    break
        finally:
            _emit((name, None))

    readers = [
        threading.Thread(target=_reader, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=_reader, args=("stderr", proc.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    result: dict | None = None
    failure: dict | None = None
    stdout_buffer = bytearray()
    stderr_tail = bytearray()
    produced = 0
    eof: set[str] = set()
    import time

    deadline = time.monotonic() + timeout
    try:
        try:
            _jsonl_send(proc.stdin, request)
            proc.stdin.close()
        except OSError as e:
            failure = _protocol_failure(request, "crash", f"worker 在接收请求前退出: {e}")
        while failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = _protocol_failure(request, "timeout", "worker 未在时限内返回终态")
                break
            try:
                channel, chunk = events.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if proc.poll() is not None and eof == {"stdout", "stderr"}:
                    break
                continue
            if chunk is None:
                eof.add(channel)
                if channel == "stdout" and stdout_buffer:
                    failure = _protocol_failure(request, "rejected", "worker stdout 末行不是完整 JSONL（缺少换行）")
                    break
                if proc.poll() is not None and eof == {"stdout", "stderr"}:
                    break
                continue

            produced += len(chunk)
            if produced > max_output_bytes:
                failure = _protocol_failure(request, "resource_limit", "worker stdout/stderr 合计输出超限")
                break
            if channel == "stderr":
                stderr_tail.extend(chunk)
                if len(stderr_tail) > 2000:
                    del stderr_tail[:-2000]
                continue

            stdout_buffer.extend(chunk)
            while b"\n" in stdout_buffer:
                raw_line, _, rest = stdout_buffer.partition(b"\n")
                stdout_buffer = bytearray(rest)
                if not raw_line.strip():
                    continue
                try:
                    message = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    failure = _protocol_failure(request, "rejected", f"worker stdout 不是合法 JSONL: {e}")
                    break
                terminal = isinstance(message, dict) and message.get("type") == "parse_result"
                message_error = _validate_worker_message(message, request, terminal=terminal)
                if message_error:
                    failure = _protocol_failure(request, "rejected", message_error)
                    break
                if terminal:
                    result = message
                    break
            if failure is not None or result is not None:
                break
    finally:
        stop_readers.set()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=10)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=1)

    if failure is not None:
        return failure
    if result is not None:
        return result
    tail = stderr_tail.decode("utf-8", errors="replace")[-500:]
    return_code = proc.returncode
    if return_code is None:
        exit_detail = "退出状态未知"
    elif return_code < 0:
        exit_detail = f"被信号 {-return_code} 终止"
    else:
        exit_detail = f"退出码 {return_code}"
    return _protocol_failure(
        request,
        "crash",
        f"worker 未返回终态（{exit_detail}）。stderr: {tail}",
    )


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
    assert w.adapter_path is not None
    return _run_jsonl_command(
        [py, str(w.adapter_path)],
        request,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
        env=env,
        cwd=w.adapter_path.parent,
    )


def run_docker_protocol(w: WorkerInfo, request: dict, *, source_path: Path, work_dir: Path, policy: dict) -> dict:
    """docker 后端：只读挂载原件、network=none、资源限额、tmpfs work_dir。"""
    sb = policy["worker_sandbox"]["docker"]
    timeout = policy["worker_sandbox"]["subprocess"]["timeout_seconds"]
    max_output_bytes = policy["worker_sandbox"]["subprocess"]["max_output_bytes"]
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
    result = _run_jsonl_command(cmd, req, timeout=timeout, max_output_bytes=max_output_bytes)
    if result.get("status") != "ok":
        return result
    # 容器路径翻译为 work_dir 相对路径；最终宿主机 containment 由统一录取层执行。
    path_holders: list[dict] = []
    if result.get("fragment_path"):
        path_holders.append(result)
    path_holders.extend(result.get("output_files") or [])
    fragment = result.get("fragment") or {}
    path_holders.extend(fragment.get("attachments") or [])
    path_holders.extend(fragment.get("output_files") or [])
    for holder in path_holders:
        key = "fragment_path" if holder is result else "path"
        holder[key] = _container_relative_path(holder[key], container_work)
    return result


def _container_relative_path(raw_path: str, container_work: str) -> str:
    path = PurePosixPath(raw_path)
    work = PurePosixPath(container_work)
    if path.is_absolute():
        try:
            path = path.relative_to(work)
        except ValueError as e:
            raise SecurityViolation(f"容器 worker 产物逃逸 {container_work}: {raw_path!r}") from e
    if not path.parts or any(part in ("..", "") for part in path.parts):
        raise SecurityViolation(f"容器 worker 产物路径非法: {raw_path!r}")
    return path.as_posix()


def _validate_fragment(fragment: dict) -> None:
    content = fragment.get("content")
    if content is not None:
        if not isinstance(content, dict) or not isinstance(content.get("blocks", []), list):
            raise ContractViolation("worker fragment.content.blocks 必须是数组")
        for block in content.get("blocks", []):
            if not isinstance(block, dict) or not isinstance(block.get("elements", []), list):
                raise ContractViolation("worker fragment block/elements 结构非法")
            for element in block.get("elements", []):
                if (
                    not isinstance(element, dict)
                    or not isinstance(element.get("type"), str)
                    or not isinstance(element.get("locator"), dict)
                ):
                    raise ContractViolation("worker fragment element 必须包含字符串 type 与 locator 对象")
    for key in ("quality_dims", "fitness", "annotations"):
        if key in fragment and not isinstance(fragment[key], dict):
            raise ContractViolation(f"worker fragment.{key} 必须是对象")
    for key in ("notes", "warnings"):
        if key in fragment and (
            not isinstance(fragment[key], list) or not all(isinstance(v, str) for v in fragment[key])
        ):
            raise ContractViolation(f"worker fragment.{key} 必须是字符串数组")
    attachments = fragment.get("attachments") or []
    if not isinstance(attachments, list):
        raise ContractViolation("worker fragment.attachments 必须是数组")
    for attachment in attachments:
        if not isinstance(attachment, dict) or not isinstance(attachment.get("name"), str) or not isinstance(attachment.get("path"), str):
            raise ContractViolation("worker attachment 必须包含字符串 name/path")


def _admit_worker_result(result: dict, request: dict, work_dir: Path, *, worker_role: str) -> dict:
    error = _validate_worker_message(result, request, terminal=True)
    if error:
        return _protocol_failure(request, "rejected", error)
    if result.get("status") != "ok":
        return result
    try:
        fragment = result.get("fragment")
        if result.get("fragment_path"):
            fragment_path = resolve_worker_output(work_dir, result["fragment_path"])
            with open(fragment_path, encoding="utf-8") as f:
                fragment = json.load(f)
            result["fragment"] = fragment
            result.pop("fragment_path", None)
        if fragment is not None:
            if not isinstance(fragment, dict):
                raise ContractViolation("worker fragment 必须是对象")
            _validate_fragment(fragment)
        if worker_role in ("parse", "enrich") and not isinstance(fragment, dict):
            raise ContractViolation(f"{worker_role} worker 成功终态必须包含 fragment")

        output_groups = [result.get("output_files") or []]
        if isinstance(fragment, dict):
            output_groups.extend([fragment.get("attachments") or [], fragment.get("output_files") or []])
        if worker_role == "transform" and not output_groups[0]:
            raise ContractViolation("transform worker 成功终态必须包含 output_files")
        for group in output_groups:
            if not isinstance(group, list):
                raise ContractViolation("worker output_files/attachments 必须是数组")
            for output in group:
                if not isinstance(output, dict) or not isinstance(output.get("path"), str):
                    raise ContractViolation("worker 产物必须包含字符串 path")
                resolved = resolve_worker_output(work_dir, output["path"])
                output["path"] = resolved.relative_to(work_dir.resolve()).as_posix()
    except (ContractViolation, SecurityViolation, OSError, json.JSONDecodeError) as e:
        return _protocol_failure(request, "rejected", f"worker 产物未通过录取: {e}")
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
    request_error = _validate_request(request)
    if request_error:
        return _protocol_failure(request, "rejected", request_error)
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
            result = {"type": "parse_result", "job_id": request["job_id"], "status": "ok", "fragment": fragment}
        except WorkerUnavailable:
            raise
        except WorkerFailed as e:
            result = _protocol_failure(request, e.kind, str(e))
        except Exception as e:  # lite parser 异常不得击穿编排器
            result = _protocol_failure(request, "crash", f"{type(e).__name__}: {e}")
    else:
        timeout = policy["worker_sandbox"]["subprocess"]["timeout_seconds"]
        max_out = policy["worker_sandbox"]["subprocess"]["max_output_bytes"]
        env_pass = policy["worker_sandbox"]["subprocess"]["env_passthrough"]
        if w.backend == "docker":
            if shutil.which("docker") is None:
                raise WorkerUnavailable("docker 不在 PATH")
            result = run_docker_protocol(w, request, source_path=source_path, work_dir=work_dir, policy=policy)
        elif w.backend == "subprocess":
            if not w.adapter_path or not w.adapter_path.exists():
                raise WorkerUnavailable("adapter 缺失")
            if w.requirements.get("network") is True:
                raise WorkerUnavailable("需要联网的 worker 必须使用可执行网络策略的容器后端")
            result = run_subprocess_protocol(w, request, timeout=timeout, max_output_bytes=max_out, env_passthrough=env_pass)
        else:
            raise WorkerUnavailable(f"未知后端 {w.backend}")
    return _admit_worker_result(result, request, work_dir, worker_role=w.role)


def make_work_dir(base: Path, job_id: str) -> Path:
    from .manifest import internal_dir

    d = internal_dir(base, "work", job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
