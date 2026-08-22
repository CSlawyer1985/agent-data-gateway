"""硬件与环境检测：CPU/内存/GPU/容器运行时/Java/.NET（adg check 的数据源）。"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


def _run(cmd: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
        out = (p.stdout or p.stderr or "").strip()
        return p.returncode, out.split("\n")[0] if out else ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def total_ram_mb() -> int | None:
    try:
        if sys.platform == "darwin":
            rc, out = _run(["sysctl", "-n", "hw.memsize"])
            if rc == 0 and out.isdigit():
                return int(out) // (1024 * 1024)
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * page_size) // (1024 * 1024)
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def detect_gpu() -> dict:
    """返回 {"available": bool, "kind": str, "detail": str}。轻探测，不初始化驱动。"""
    if shutil.which("nvidia-smi"):
        rc, out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        if rc == 0 and out:
            return {"available": True, "kind": "nvidia", "detail": out}
        return {"available": True, "kind": "nvidia", "detail": "nvidia-smi 可用"}
    if sys.platform == "darwin":
        machine = platform.machine()
        if machine == "arm64":
            return {"available": True, "kind": "metal", "detail": f"Apple Silicon ({machine})，Metal 可用"}
        return {"available": False, "kind": "none", "detail": "Intel Mac，无可用 GPU 加速"}
    return {"available": False, "kind": "none", "detail": "未检测到 GPU"}


def detect_container_runtime() -> dict:
    for name in ("docker", "podman"):
        path = shutil.which(name)
        if not path:
            continue
        rc, out = _run([name, "info", "--format", "{{.ServerVersion}}"], timeout=10)
        if rc == 0 and out:
            return {"name": name, "path": path, "running": True, "version": out}
        rc2, out2 = _run([name, "--version"])
        return {"name": name, "path": path, "running": False, "version": out2 if rc2 == 0 else "unknown"}
    return {"name": None, "path": None, "running": False, "version": None}


def detect_java() -> dict:
    if not shutil.which("java"):
        return {"available": False, "version": None}
    rc, out = _run(["java", "-version"])
    return {"available": rc in (0, 1) and bool(out), "version": out or None}


def detect_dotnet() -> dict:
    if not shutil.which("dotnet"):
        return {"available": False, "version": None}
    rc, out = _run(["dotnet", "--version"])
    return {"available": rc == 0, "version": out or None}


@dataclass
class Environment:
    python_version: str = ""
    python_executable: str = ""
    os: str = ""
    cpu_count: int = 0
    total_ram_mb: int | None = None
    gpu: dict = field(default_factory=dict)
    container: dict = field(default_factory=dict)
    java: dict = field(default_factory=dict)
    dotnet: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "python_version": self.python_version,
            "python_executable": self.python_executable,
            "os": self.os,
            "cpu_count": self.cpu_count,
            "total_ram_mb": self.total_ram_mb,
            "gpu": self.gpu,
            "container": self.container,
            "java": self.java,
            "dotnet": self.dotnet,
        }


def detect_environment() -> Environment:
    env = Environment(
        python_version=platform.python_version(),
        python_executable=sys.executable,
        os=f"{platform.system()} {platform.release()} ({platform.machine()})",
        cpu_count=os.cpu_count() or 0,
        total_ram_mb=total_ram_mb(),
        gpu=detect_gpu(),
        container=detect_container_runtime(),
        java=detect_java(),
        dotnet=detect_dotnet(),
    )
    return env


def check_requirements(requirements: dict, env: Environment | None = None) -> tuple[bool, list[str]]:
    """worker requirements vs 环境 → (是否满足, 不满足原因列表)。"""
    env = env or detect_environment()
    problems: list[str] = []
    min_ram = requirements.get("min_ram_mb")
    if min_ram and env.total_ram_mb is not None and env.total_ram_mb < min_ram:
        problems.append(f"内存不足: 需要 {min_ram}MB，实际 {env.total_ram_mb}MB")
    gpu_req = requirements.get("gpu")
    if gpu_req == "required" and not env.gpu.get("available"):
        problems.append("worker 要求 GPU，未检测到")
    backends = requirements.get("backend") or []
    if "docker" in backends and not env.container.get("running"):
        # docker 声明但不可用不致命——可能还有 subprocess 后端
        if "subprocess" not in backends:
            problems.append("worker 仅支持 docker 后端，但容器运行时不可用")
    return (not problems, problems)
