"""worker 协议：subprocess 超时/崩溃隔离 + fragment 定稿。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gateway.errors import WorkerUnavailable
from gateway.hardware import Environment
from gateway.workers import WorkerInfo, WorkerRegistry, execute_worker, run_subprocess_protocol


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
                w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "crash")
        self.assertIn("退出码 3", r["error"]["message"])

    def test_timeout_isolated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            w = self._make("import time; time.sleep(60)", Path(d))
            r = run_subprocess_protocol(
                w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=1, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "timeout")

    def test_output_limit_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            # 持续吐非终态 JSON 行，触发输出上限
            body = "import sys\nwhile True:\n    sys.stdout.write('{\"type\":\"progress\",\"job_id\":\"1\"}\\n'); sys.stdout.flush()\n"
            w = self._make(body, Path(d))
            r = run_subprocess_protocol(
                w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=4096, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "resource_limit")

    def test_wrong_job_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            body = (
                "import json, sys\n"
                "json.loads(sys.stdin.readline())\n"
                "print(json.dumps({'type':'parse_result','job_id':'other','status':'ok','fragment':{}}), flush=True)\n"
            )
            w = self._make(body, Path(d))
            r = run_subprocess_protocol(
                w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "expected", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "rejected")

    def test_unsupported_protocol_version_is_rejected_before_spawn(self):
        with tempfile.TemporaryDirectory() as d:
            missing_adapter = Path(d) / "does-not-exist.py"
            w = WorkerInfo(
                name="t", version="0", role="parse", kinds=["text"], formats=None,
                requirements={}, backend="subprocess", adapter_path=missing_adapter,
            )
            r = run_subprocess_protocol(
                w, {"protocol_version": "99.0", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "rejected")

    def test_malformed_terminal_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            body = (
                "import json, sys\n"
                "req=json.loads(sys.stdin.readline())\n"
                "print(json.dumps({'type':'parse_result','job_id':req['job_id'],'status':'ok','unexpected':True}), flush=True)\n"
            )
            w = self._make(body, Path(d))
            r = run_subprocess_protocol(
                w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "rejected")

    def test_non_json_stdout_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            body = (
                "import json, sys\n"
                "req=json.loads(sys.stdin.readline())\n"
                "print('third-party log', flush=True)\n"
                "print(json.dumps({'type':'parse_result','job_id':req['job_id'],'status':'ok','fragment':{}}), flush=True)\n"
            )
            w = self._make(body, Path(d))
            r = run_subprocess_protocol(
                w, {"protocol_version": "0.1", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": d},
                timeout=10, max_output_bytes=1 << 20, env_passthrough=["PATH"],
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "rejected")

    def test_worker_cannot_return_file_outside_work_dir(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            work = root / "work"
            work.mkdir()
            outside = root / "outside.txt"
            outside.write_text("not worker output", encoding="utf-8")
            body = (
                "import json, sys\n"
                "req=json.loads(sys.stdin.readline())\n"
                f"print(json.dumps({{'type':'parse_result','job_id':req['job_id'],'status':'ok','output_files':[{{'path':{str(outside)!r},'format':'text/plain'}}]}}), flush=True)\n"
            )
            w = self._make(body, root)
            r = execute_worker(
                w,
                {"protocol_version": "0.1", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": str(work)},
                source_path=outside,
                work_dir=work,
                policy={
                    "worker_sandbox": {
                        "subprocess": {"timeout_seconds": 10, "max_output_bytes": 1 << 20, "env_passthrough": ["PATH"]}
                    },
                    "parser_limits": {},
                },
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "rejected")

    def test_worker_cannot_escape_through_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            work = root / "work"
            work.mkdir()
            outside = root / "outside.txt"
            outside.write_text("not worker output", encoding="utf-8")
            (work / "link.txt").symlink_to(outside)
            body = (
                "import json, sys\n"
                "req=json.loads(sys.stdin.readline())\n"
                "print(json.dumps({'type':'parse_result','job_id':req['job_id'],'status':'ok','output_files':[{'path':'link.txt','format':'text/plain'}]}), flush=True)\n"
            )
            w = self._make(body, root)
            r = execute_worker(
                w,
                {"protocol_version": "0.1", "type": "parse_request", "job_id": "1", "source_path": "x", "source_id": "", "kind": "text", "work_dir": str(work)},
                source_path=outside,
                work_dir=work,
                policy={
                    "worker_sandbox": {
                        "subprocess": {"timeout_seconds": 10, "max_output_bytes": 1 << 20, "env_passthrough": ["PATH"]}
                    },
                    "parser_limits": {},
                },
            )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"]["kind"], "rejected")


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
        self.assertIn("paddleocr_vl", names)
        self.assertIn("ovisocr2", names)

    def test_workers_for_kind(self):
        from packs.legal import register_lite_parsers

        r = WorkerRegistry()
        register_lite_parsers(r)
        pdf_workers = r.workers_for("pdf")
        self.assertIn(r.get("pdf_lite"), pdf_workers)

    def test_hardware_requirements_are_part_of_runtime_availability(self):
        r = WorkerRegistry()
        worker = WorkerInfo(
            name="memory-hungry", version="1", role="parse", kinds=["text"], formats=None,
            requirements={"min_ram_mb": 8192}, backend="inproc",
        )
        low = Environment(total_ram_mb=1024, gpu={"available": False}, container={})
        high = Environment(total_ram_mb=16384, gpu={"available": False}, container={})
        blocked = r.availability_status(worker, low)
        allowed = r.availability_status(worker, high)
        self.assertFalse(blocked.available)
        self.assertEqual("hardware_unmet", blocked.reason)
        self.assertIn("内存不足", blocked.detail)
        self.assertTrue(allowed.available, "运行时缓存不得把另一台/另一次环境的硬件结论带过来")

    def test_probe_import_checks_presence_without_executing_heavy_module(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adapter = root / "adapter.py"
            adapter.write_text("raise SystemExit(0)", encoding="utf-8")
            (root / "cold_module.py").write_text("raise RuntimeError('module body must not run')", encoding="utf-8")
            worker = WorkerInfo(
                name="cold", version="1", role="parse", kinds=["text"], formats=None,
                requirements={}, backend="subprocess", adapter_path=adapter,
                venv_python=Path(sys.executable), probe_import="cold_module",
            )
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(root)}):
                available, detail = WorkerRegistry().availability(worker, refresh=True)
        self.assertTrue(available, detail)

    def _declared_worker(self, root: Path, **kw) -> WorkerInfo:
        adapter = root / "adapter.py"
        adapter.write_text("raise SystemExit(0)", encoding="utf-8")
        return WorkerInfo(
            name="declared", version="1", role="parse", kinds=["text"], formats=None,
            requirements={}, backend="subprocess", adapter_path=adapter,
            venv_python=Path(sys.executable), probe_import="json", **kw,
        )

    def test_declared_dependency_missing_is_not_available(self):
        """probe_import 只验一个模块；声明的其余依赖缺失同样是运行时必然失败。"""
        with tempfile.TemporaryDirectory() as d:
            worker = self._declared_worker(Path(d), python_requires=["json", "no-such-pkg-xyz"])
            status = WorkerRegistry().availability_status(worker, refresh=True)
        self.assertFalse(status.available)
        self.assertIn("no-such-pkg-xyz", status.detail)

    def test_declared_dependencies_present_stay_available(self):
        with tempfile.TemporaryDirectory() as d:
            worker = self._declared_worker(Path(d), python_requires=["json"])
            status = WorkerRegistry().availability_status(worker, refresh=True)
        self.assertTrue(status.available, status.detail)

    def test_missing_model_weights_are_not_available(self):
        """声明了预取的 worker，权重缺失必须显性不可用，而不是运行时才炸。"""
        with tempfile.TemporaryDirectory() as d:
            worker = self._declared_worker(Path(d), model_verify="import sys; sys.exit(1)")
            status = WorkerRegistry().availability_status(worker, refresh=True)
        self.assertFalse(status.available)
        self.assertEqual("model_missing", status.reason)

    def test_undeterminable_model_check_does_not_claim_missing(self):
        """探测脚本自身失败时只能说「查不出来」，不能反过来诬告用户没装模型。"""
        with tempfile.TemporaryDirectory() as d:
            worker = self._declared_worker(Path(d), model_verify="import sys; sys.exit(3)")
            status = WorkerRegistry().availability_status(worker, refresh=True)
        self.assertTrue(status.available, status.detail)

    def test_availability_borrowed_from_current_interpreter_is_disclosed(self):
        """没有 worker 专属 venv 时依赖来自当前解释器，detail 必须说出来。"""
        with tempfile.TemporaryDirectory() as d:
            adapter = Path(d) / "adapter.py"
            adapter.write_text("raise SystemExit(0)", encoding="utf-8")
            worker = WorkerInfo(
                name="borrowed", version="1", role="parse", kinds=["text"], formats=None,
                requirements={}, backend="subprocess", adapter_path=adapter,
                venv_python=Path(d) / "absent/bin/python", probe_import="json",
            )
            status = WorkerRegistry().availability_status(worker, refresh=True)
        self.assertTrue(status.available, status.detail)
        self.assertIn("当前解释器", status.detail)

    def test_declared_model_verify_snippets_are_executable(self):
        """随包发布的 verify 脚本必须能跑并给出三态之一，不能是坏脚本。"""
        import json as _json
        import subprocess as _sp

        manifests = sorted(Path("workers").glob("*/worker.json"))
        self.assertTrue(manifests, "未找到 worker 清单")
        for path in manifests:
            spec = _json.loads(path.read_text(encoding="utf-8"))
            verify = (spec.get("prefetch") or {}).get("verify")
            if not verify:
                continue
            with self.subTest(worker=spec["name"]):
                rc = _sp.run([sys.executable, "-c", verify], capture_output=True, timeout=120).returncode
                self.assertIn(rc, (0, 1, 3), f"{spec['name']}: verify 退出码 {rc} 不在三态内")

    def test_network_worker_cannot_use_subprocess_backend(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adapter = root / "w.py"
            adapter.write_text("raise SystemExit(0)", encoding="utf-8")
            worker = WorkerInfo(
                name="networked", version="1", role="enrich", kinds=["text"], formats=None,
                requirements={"network": True}, backend="subprocess", adapter_path=adapter,
            )
            work = root / "work"
            work.mkdir()
            with self.assertRaises(WorkerUnavailable):
                execute_worker(
                    worker,
                    {
                        "protocol_version": "0.1", "type": "parse_request", "job_id": "1",
                        "source_path": str(root / "source"), "source_id": "", "kind": "text",
                        "work_dir": str(work),
                    },
                    source_path=root / "source",
                    work_dir=work,
                    policy={
                        "worker_sandbox": {
                            "subprocess": {
                                "timeout_seconds": 10, "max_output_bytes": 1 << 20,
                                "env_passthrough": ["PATH"],
                            }
                        },
                        "parser_limits": {},
                    },
                )


if __name__ == "__main__":
    unittest.main()
