"""线路优化：自动挑最快的软件源与模型镜像（安装阶段专用）。

为什么在安装阶段做：whisper / paddleocr / docling / mineru 的模型是**首次运行时**下载的，
而这些 worker 都声明 `network: false`。正确的解法是在 `adg setup` 把模型下好，让运行时
禁网的声明成真——而不是放宽 worker 沙箱的环境变量白名单（安全基线只收不放）。

纪律：
- **不静默**：测速失败、全部不可用、回落官方源，都要能被调用方拿到并显示。
- **不写全局配置**：只影响 setup 起的子进程，绝不动用户的 pip.conf / 环境。
- **可关闭**：显式 `--index-url` 或环境变量优先于自动探测；`--no-probe` 完全关掉。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

PIP_INDEX_ENV = "ADG_PIP_INDEX_URL"
HF_ENDPOINT_ENV = "HF_ENDPOINT"

# 候选源。官方放在首位：能直连就用官方，镜像只是备选而非默认偏好。
PIP_MIRRORS: tuple[tuple[str, str], ...] = (
    ("pypi 官方", "https://pypi.org/simple"),
    ("清华 TUNA", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里云", "https://mirrors.aliyun.com/pypi/simple"),
    ("腾讯云", "https://mirrors.cloud.tencent.com/pypi/simple"),
    ("中科大", "https://pypi.mirrors.ustc.edu.cn/simple"),
)

# HuggingFace 镜像：faster-whisper / docling 经 huggingface_hub 下载模型，认 HF_ENDPOINT
HF_MIRRORS: tuple[tuple[str, str], ...] = (
    ("HuggingFace 官方", "https://huggingface.co"),
    ("hf-mirror 国内镜像", "https://hf-mirror.com"),
)

PROBE_TIMEOUT = 3.0
PROBE_ATTEMPTS = 2  # 单次测速抖动可达 2 倍，取多次最小值
CACHE_TTL_SECONDS = 7 * 24 * 3600

# 模型下载全线不通时给用户的手动路径（ModelScope 不是 HF_ENDPOINT 的直接替代，
# 需要先下到本地再注入，所以只能作为指引而非自动路由）
MODELSCOPE_HINT = (
    "模型线路全部不通。手动路径：从 ModelScope 下载 Systran/faster-whisper-small "
    "到本地目录，再用 adg parse --worker-option model=<该目录> 注入"
)


@dataclass
class Route:
    """一次线路决策的结果。reachable=False 表示所有候选都不通——调用方必须显性告知用户。"""

    url: str | None  # None = 用工具自带的默认端点
    source: str
    reachable: bool = True


@dataclass
class Probe:
    name: str
    url: str
    latency_ms: float | None  # None = 不可用
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.latency_ms is not None


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "adg" / "mirrors.json"


def _read_cache() -> dict:
    p = cache_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(data: dict) -> None:
    p = cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # 缓存写不了不影响功能，下次重测即可


def clear_cache() -> bool:
    try:
        cache_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _probe_one(name: str, url: str, timeout: float, attempts: int = PROBE_ATTEMPTS) -> Probe:
    """测到首字节的耗时，取 attempts 次最小值（单次抖动可达 2 倍）。只读 1 字节就断开。"""
    request = urllib.request.Request(url + "/", headers={"User-Agent": "adg-mirror-probe"})
    best: float | None = None
    last_error: str | None = None
    for _ in range(max(1, attempts)):
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                resp.read(1)
        except (urllib.error.URLError, OSError, ValueError) as e:
            last_error = f"{type(e).__name__}: {e}"
            continue
        elapsed = (time.monotonic() - started) * 1000
        best = elapsed if best is None else min(best, elapsed)
    return Probe(name, url, best, None if best is not None else last_error)


def probe_all(candidates=PIP_MIRRORS, *, timeout: float = PROBE_TIMEOUT) -> list[Probe]:
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        futures = [pool.submit(_probe_one, name, url, timeout) for name, url in candidates]
        return [f.result() for f in futures]


def fastest(candidates=PIP_MIRRORS, *, timeout: float = PROBE_TIMEOUT) -> Probe | None:
    usable = [p for p in probe_all(candidates, timeout=timeout) if p.ok]
    return min(usable, key=lambda p: p.latency_ms) if usable else None


def _cached_or_probe(key: str, candidates, *, timeout: float, use_cache: bool) -> Probe | None:
    cache = _read_cache()
    entry = cache.get(key) if use_cache else None
    if isinstance(entry, dict) and time.time() - entry.get("at", 0) < CACHE_TTL_SECONDS:
        return Probe(entry.get("name", "?"), entry["url"], entry.get("latency_ms"))
    best = fastest(candidates, timeout=timeout)
    if best is not None:
        cache[key] = {"name": best.name, "url": best.url, "latency_ms": best.latency_ms, "at": time.time()}
        _write_cache(cache)
    return best


def resolve_pip_index(
    cli_value: str | None = None,
    *,
    probe: bool = True,
    use_cache: bool = True,
    timeout: float = PROBE_TIMEOUT,
) -> Route:
    """pip 源决策。优先级：CLI > 环境变量 > 自动测速 > pip 默认。"""
    if cli_value:
        return Route(cli_value, "命令行 --index-url")
    env_value = os.environ.get(PIP_INDEX_ENV)
    if env_value:
        return Route(env_value, f"环境变量 {PIP_INDEX_ENV}")
    if not probe:
        return Route(None, "pip 默认源（未探测）")
    best = _cached_or_probe("pip", PIP_MIRRORS, timeout=timeout, use_cache=use_cache)
    if best is None:
        return Route(None, "所有候选源都探测失败，回落 pip 默认源", reachable=False)
    latency = f"{best.latency_ms:.0f}ms" if best.latency_ms is not None else "命中缓存"
    if best.url == PIP_MIRRORS[0][1]:
        return Route(None, f"自动探测：{best.name} 最快（{latency}）")
    return Route(best.url, f"自动探测：{best.name} 最快（{latency}）")


def resolve_hf_endpoint(
    *, probe: bool = True, use_cache: bool = True, timeout: float = PROBE_TIMEOUT
) -> Route:
    """模型下载线路。全部候选不通时 reachable=False——调用方必须把它显性告诉用户，
    否则第一次解析音视频会莫名其妙地卡住。"""
    env_value = os.environ.get(HF_ENDPOINT_ENV)
    if env_value:
        return Route(env_value, f"环境变量 {HF_ENDPOINT_ENV}")
    if not probe:
        return Route(None, "HuggingFace 官方（未探测）")
    best = _cached_or_probe("hf", HF_MIRRORS, timeout=timeout, use_cache=use_cache)
    if best is None:
        return Route(None, "模型线路全部不通", reachable=False)
    latency = f"{best.latency_ms:.0f}ms" if best.latency_ms is not None else "命中缓存"
    if best.url == HF_MIRRORS[0][1]:
        return Route(None, f"自动探测：{best.name} 最快（{latency}）")
    return Route(best.url, f"自动探测：{best.name} 最快（{latency}）")


def model_download_env(base: dict | None = None, *, probe: bool = True, use_cache: bool = True) -> tuple[dict, Route]:
    """给「下载模型」的子进程用的环境变量。只加线路提示，不含任何凭据。"""
    env = dict(base if base is not None else os.environ)
    route = resolve_hf_endpoint(probe=probe, use_cache=use_cache)
    if route.url:
        env[HF_ENDPOINT_ENV] = route.url
    return env, route
