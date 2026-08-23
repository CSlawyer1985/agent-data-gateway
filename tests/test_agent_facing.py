"""面向 Agent 的可理解性（ADR 0003 决策 7）。

--help 里的承诺属于契约：Agent 依据它判断退出码、流程顺序与检索范围。
文本与实际行为漂移不会让别的测试变红，却会让 Agent 误判，故在此钉住。
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from gateway import cli
from gateway.agent_api import COVERAGE_MEANING
from gateway.selftest import build_package


def _run(argv: list[str]) -> int:
    """跑一次 adg，吞掉 stdout/stderr，只要退出码。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return cli.main(argv)


class TestAgentNotes(unittest.TestCase):
    def test_help_carries_the_three_promises(self):
        text = cli.build_parser().format_help()
        self.assertIn("退出码", text)
        self.assertIn("不是失败", text)          # 1 的语义，最容易被误读的一条
        self.assertIn("adg scan", text)          # 流程顺序
        self.assertIn("adg parse", text)
        self.assertIn("adg search", text)
        self.assertIn("coverage_caveat", text)   # 检索范围警告的落点
        self.assertIn("不等于", text)            # 「没搜到」不等于「不存在」

    def test_notes_are_reachable_from_top_level_help(self):
        """epilog 必须真的出现在 --help 输出里，而不只是模块里有个常量。"""
        text = cli.build_parser().format_help()
        for line in cli.AGENT_NOTES.strip().splitlines():
            if line.strip():
                self.assertIn(line.strip()[:20], text)


class TestExitCodeMatchesItsDocumentation(unittest.TestCase):
    """把 --help 里承诺的退出码语义与真实行为绑在一起。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.out = root / "out"
        # 干净包：只放一份纯文本，扫完不该有任何未解决项
        cls.clean = root / "clean"
        cls.clean.mkdir()
        (cls.clean / "说明.txt").write_text("这是一份可以被完整读取的材料。", encoding="utf-8")
        # 混合包：selftest 的标准包，含读不了的样本
        cls.pkg = root / "pkg"
        cls.pkg.mkdir()
        build_package(cls.pkg, with_threats=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_zero_means_nothing_left_unread(self):
        out = self.out / "clean"
        self.assertEqual(_run(["scan", str(self.clean), "--out", str(out)]), 0)

    def test_one_means_incomplete_not_failure(self):
        """--help 承诺 1 = 完成但有未读全项。

        scan 之后尚未 parse，材料都还没被可靠读取，status 必须报 1——
        既不是 0（全部读到了），也不是 2（失败）。Agent 把 1 当失败就会误判。
        """
        out = self.out / "mixed"
        _run(["scan", str(self.pkg), "--out", str(out)])
        self.assertEqual(_run(["status", str(self.pkg), "--out", str(out)]), 1)

    def test_missing_package_is_two(self):
        rc = _run(["status", str(self.pkg / "不存在"), "--out", str(self.out / "mixed")])
        self.assertEqual(rc, 2)


class TestCoverageDisambiguation(unittest.TestCase):
    def test_meaning_separates_completeness_from_utilization(self):
        """extraction_coverage 是文件级完整性，说明里必须明确排除利用率读法。"""
        self.assertIn("完整性", COVERAGE_MEANING)
        self.assertIn("不是", COVERAGE_MEANING)
        self.assertIn("提取比例", COVERAGE_MEANING)
        self.assertIn("signals", COVERAGE_MEANING)


if __name__ == "__main__":
    unittest.main()
