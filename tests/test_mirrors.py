"""线路优化：源探测、优先级、缓存与不可达时的诚实降级。

这些测试一律不联网——探测被 mock 掉。真实测速在 adg setup 运行时进行。
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gateway import mirrors
from gateway.commands.setup import PIP_INDEX_ENV, resolve_index_url

TUNA = "https://pypi.tuna.tsinghua.edu.cn/simple"
ALIYUN = "https://mirrors.aliyun.com/pypi/simple/"


def _probe(name, url, ms):
    return mirrors.Probe(name, url, ms)


class PriorityTest(unittest.TestCase):
    """显式指定永远压过自动探测——用户说了算，工具不许自作主张。"""

    def test_cli_beats_env_and_probe(self):
        with mock.patch.dict("os.environ", {mirrors.PIP_INDEX_ENV: TUNA}):
            with mock.patch.object(mirrors, "fastest") as probe:
                route = mirrors.resolve_pip_index(ALIYUN)
                self.assertEqual(ALIYUN, route.url)
                probe.assert_not_called()

    def test_env_beats_probe(self):
        with mock.patch.dict("os.environ", {mirrors.PIP_INDEX_ENV: TUNA}):
            with mock.patch.object(mirrors, "fastest") as probe:
                self.assertEqual(TUNA, mirrors.resolve_pip_index(None).url)
                probe.assert_not_called()

    def test_no_probe_flag_keeps_pip_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "fastest") as probe:
                route = mirrors.resolve_pip_index(None, probe=False)
                self.assertIsNone(route.url)
                probe.assert_not_called()

    def test_setup_resolver_never_touches_network(self):
        """setup.resolve_index_url 是纯优先级函数：测试套件不该因为它去连网。"""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "fastest") as probe:
                self.assertIsNone(resolve_index_url(None))
                probe.assert_not_called()
        self.assertEqual(PIP_INDEX_ENV, mirrors.PIP_INDEX_ENV)


class ProbeSelectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(mirrors, "cache_path",
                                    return_value=Path(self._tmp.name) / "mirrors.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_picks_the_fastest_reachable(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[
                _probe("官方", mirrors.PIP_MIRRORS[0][1], 900.0),
                _probe("清华", TUNA, 120.0),
            ]):
                route = mirrors.resolve_pip_index(None, use_cache=False)
                self.assertEqual(TUNA, route.url)
                self.assertIn("清华", route.source)

    def test_official_fastest_means_pip_default(self):
        """官方最快时不该硬塞一个镜像 URL——用 pip 自己的默认源即可。"""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[
                _probe("官方", mirrors.PIP_MIRRORS[0][1], 80.0),
                _probe("清华", TUNA, 300.0),
            ]):
                self.assertIsNone(mirrors.resolve_pip_index(None, use_cache=False).url)

    def test_all_unreachable_is_reported_not_hidden(self):
        """全不通必须 reachable=False：安装大概率失败，用户有权提前知道。"""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[
                mirrors.Probe("官方", mirrors.PIP_MIRRORS[0][1], None, "timeout"),
                mirrors.Probe("清华", TUNA, None, "timeout"),
            ]):
                route = mirrors.resolve_pip_index(None, use_cache=False)
                self.assertFalse(route.reachable)
                self.assertIsNone(route.url)

    def test_cache_avoids_reprobing(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[_probe("清华", TUNA, 100.0)]) as probe:
                mirrors.resolve_pip_index(None)
                mirrors.resolve_pip_index(None)
                self.assertEqual(1, probe.call_count, "同一 TTL 内不应重复测速")

    def test_expired_cache_is_reprobed(self):
        cache = mirrors.cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"pip": {"name": "旧", "url": ALIYUN, "latency_ms": 1.0,
                                             "at": time.time() - mirrors.CACHE_TTL_SECONDS - 1}}),
                         encoding="utf-8")
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[_probe("清华", TUNA, 100.0)]):
                self.assertEqual(TUNA, mirrors.resolve_pip_index(None).url)


class ModelRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(mirrors, "cache_path",
                                    return_value=Path(self._tmp.name) / "mirrors.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_mirror_endpoint_enters_child_env(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[
                mirrors.Probe("官方", mirrors.HF_MIRRORS[0][1], None, "timeout"),
                _probe("hf-mirror", mirrors.HF_MIRRORS[1][1], 200.0),
            ]):
                env, route = mirrors.model_download_env({}, use_cache=False)
                self.assertEqual(mirrors.HF_MIRRORS[1][1], env[mirrors.HF_ENDPOINT_ENV])
                self.assertTrue(route.reachable)

    def test_env_carries_no_credentials(self):
        """只加线路提示。往子进程塞凭据是另一回事，这里永远不做。"""
        with mock.patch.dict("os.environ", {"SECRET_TOKEN": "x"}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[_probe("hf-mirror", mirrors.HF_MIRRORS[1][1], 10.0)]):
                env, _ = mirrors.model_download_env({}, use_cache=False)
                self.assertEqual({mirrors.HF_ENDPOINT_ENV}, set(env))

    def test_unreachable_model_route_is_explicit(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(mirrors, "probe_all", return_value=[
                mirrors.Probe("官方", mirrors.HF_MIRRORS[0][1], None, "timeout"),
                mirrors.Probe("hf-mirror", mirrors.HF_MIRRORS[1][1], None, "timeout"),
            ]):
                route = mirrors.resolve_hf_endpoint(use_cache=False)
                self.assertFalse(route.reachable)
                self.assertIn("ModelScope", mirrors.MODELSCOPE_HINT)


if __name__ == "__main__":
    unittest.main()
