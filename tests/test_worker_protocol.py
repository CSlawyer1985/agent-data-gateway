"""worker 协议：subprocess 超时/崩溃隔离 + fragment 定稿。"""

import unittest
from pathlib import Path

from gateway.workers import WorkerInfo, WorkerRegistry, run_subprocess_protocol


class WorkerIsolationTest(unittest.TestCase):
    def _make(self, body: str, tmp: Path) -> WorkerInfo:
        adapter = tmp / "w.py"
        adapter.write_text(body, encoding="utf-8")
        return WorkerInfo(
            name="t", version="0", role="parse", kinds=["text"], formats=None,
            requirements={}, backend="subprocess", adapter_path=adapter,
        )

    def test_crash_isolated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            w = self._make("import sys; sys.exit(3)", Path(d))
            r = run_subprocess_protocol(
                w, {"type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "crash")

    def test_timeout_isolated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            w = self._make("import time; time.sleep(60)", Path(d))
            r = run_subprocess_protocol(
                w, {"type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=1, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "timeout")

    def test_output_limit_isolated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            # 持续吐非终态 JSON 行，触发输出上限
            body = "import sys\nwhile True:\n    sys.stdout.write('{\"type\":\"progress\"}\\n'); sys.stdout.flush()\n"
            w = self._make(body, Path(d))
            r = run_subprocess_protocol(
                w, {"type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=4096, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "resource_limit")


class RegistryTest(unittest.TestCase):
    def test_lite_and_external_registered(self):
        from packs.legal import register_lite_parsers

        r = WorkerRegistry()
        register_lite_parsers(r)
        r.load_external()
        names = set(r.workers)
        # lite
        self.assertIn("pdf_lite", names)
        self.assertIn("docx_lite", names)
        # external（清单存在即注册，可用性另算）
        self.assertIn("docling", names)
        self.assertIn("mineru", names)

    def test_workers_for_kind(self):
        from packs.legal import register_lite_parsers

        r = WorkerRegistry()
        register_lite_parsers(r)
        pdf_workers = r.workers_for("pdf")
        self.assertIn(r.get("pdf_lite"), pdf_workers)


if __name__ == "__main__":
    unittest.main()
