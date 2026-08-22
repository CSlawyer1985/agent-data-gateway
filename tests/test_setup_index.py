"""adg setup 镜像解析测试。

resolve_index_url 决定 pip 安装是否走国内镜像：
CLI --index-url > 环境变量 ADG_PIP_INDEX_URL > pip 默认（None）。
"""

import unittest
from unittest import mock

from gateway.commands.setup import PIP_INDEX_ENV, resolve_index_url

TUNA = "https://pypi.tuna.tsinghua.edu.cn/simple"
ALIYUN = "https://mirrors.aliyun.com/pypi/simple/"


class ResolveIndexUrlTest(unittest.TestCase):
    def test_cli_overrides_env(self):
        with mock.patch.dict("os.environ", {PIP_INDEX_ENV: TUNA}):
            self.assertEqual(resolve_index_url(ALIYUN), ALIYUN)

    def test_env_used_when_no_cli(self):
        with mock.patch.dict("os.environ", {PIP_INDEX_ENV: TUNA}):
            self.assertEqual(resolve_index_url(None), TUNA)

    def test_default_none(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            saved = os.environ.pop(PIP_INDEX_ENV, None)
            try:
                self.assertIsNone(resolve_index_url(None))
            finally:
                if saved is not None:
                    os.environ[PIP_INDEX_ENV] = saved

    def test_empty_strings_fall_through(self):
        with mock.patch.dict("os.environ", {PIP_INDEX_ENV: ""}):
            self.assertIsNone(resolve_index_url(""))


if __name__ == "__main__":
    unittest.main()
