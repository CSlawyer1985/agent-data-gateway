"""持久化事实边界：版本、Schema、哈希与引用必须在读取时验证。"""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from gateway.errors import ConcurrentWriteError
from gateway.hashing import payload_hash_of, source_id_of_bytes
from gateway.lineage import make_entry
from gateway.manifest import (
    OutputWriteLock,
    atomic_write_json,
    atomic_write_text,
    load_manifest,
    load_rendition,
    reconcile_renditions,
    rendition_json_path,
    save_manifest,
)
from gateway.models import EvidenceDocument, Item, PackageManifest, empty_annotations, new_run_id
from gateway.quality import DIMENSIONS, finalize_quality


def _manifest() -> PackageManifest:
    source_id = source_id_of_bytes(b"hello")
    manifest = PackageManifest(
        package_root_name="package",
        items=[Item(source_id=source_id, package_relative_path="a.txt", kind="text", bytes=5)],
    )
    manifest.refresh_stats()
    manifest.compute_package_id()
    manifest.ensure_integrity_shape()
    manifest.ensure_quality_shape()
    return manifest


def _rendition(source_id: str) -> EvidenceDocument:
    doc = EvidenceDocument(
        source_id=source_id,
        run_id=new_run_id(),
        source={
            "package_relative_path": "a.txt",
            "detected_type": {"mime": "text/plain", "magic": "text", "method": "adg-magic", "confidence": "high"},
            "bytes": 5,
            "origin": None,
            "derived_from": None,
        },
        parser={"name": "test", "version": "1", "worker_id": "inproc:test", "fell_back_from": None},
        provenance={"coordinate_system": {"name": "paragraph", "units": "index"}},
        content={"blocks": []},
        annotations=empty_annotations(),
        quality=finalize_quality("text", {name: "pass" for name in DIMENSIONS}),
        lineage=[],
    )
    doc.finalize_identity()
    return doc


class ManifestIntegrityTest(unittest.TestCase):
    def test_lineage_accepts_actual_runtime_versions(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            source_id = source_id_of_bytes(b"hello")
            rendition = _rendition(source_id)
            rendition.lineage.append(make_entry(
                "parse",
                input_sha256=source_id.split(":", 1)[1],
                output_sha256=rendition.rendition_id.split(":", 1)[1],
                tool="fixture",
                tool_version="1",
                runtime_versions={"parser-lib": "2.3.4"},
                started_at="2026-08-23T00:00:00.000+00:00",
                finished_at="2026-08-23T00:00:01.000+00:00",
            ))
            path = rendition_json_path(out, source_id, rendition.rendition_id)
            atomic_write_json(path, rendition.to_dict())
            loaded = load_rendition(out, source_id, rendition.rendition_id)
            self.assertEqual({"parser-lib": "2.3.4"}, loaded["lineage"][0]["runtime_versions"])

    def test_payload_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            save_manifest(_manifest(), out)
            path = out / "manifest.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["package_root_name"] = "tampered"
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_manifest(out)

    def test_rendition_payload_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            source_id = source_id_of_bytes(b"hello")
            rendition = _rendition(source_id)
            path = rendition_json_path(out, source_id, rendition.rendition_id)
            atomic_write_json(path, rendition.to_dict())
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["source"]["bytes"] = 999
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_rendition(out, source_id, rendition.rendition_id)

    def test_rendition_identity_tampering_is_rejected_with_valid_payload_hash(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            source_id = source_id_of_bytes(b"hello")
            rendition = _rendition(source_id)
            path = rendition_json_path(out, source_id, rendition.rendition_id)
            doc = rendition.to_dict()
            doc["content"]["blocks"] = [{"block_type": "section", "index": 0, "elements": []}]
            doc["integrity"]["payload_sha256"] = payload_hash_of(doc)
            atomic_write_json(path, doc)
            with self.assertRaises(ValueError):
                load_rendition(out, source_id, rendition.rendition_id)

    def test_missing_manifest_rendition_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            manifest = _manifest()
            manifest.items[0].renditions.append("sha256:" + "f" * 64)
            save_manifest(manifest, out)
            with self.assertRaises(ValueError):
                load_manifest(out)

    def test_orphan_rendition_is_reconciled(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            manifest = _manifest()
            rendition = _rendition(manifest.items[0].source_id)
            atomic_write_json(
                rendition_json_path(out, rendition.source_id, rendition.rendition_id),
                rendition.to_dict(),
            )
            adopted = reconcile_renditions(manifest, out)
            self.assertEqual(adopted, 1)
            self.assertEqual(manifest.items[0].renditions, [rendition.rendition_id])
            self.assertEqual(manifest.items[0].best_quality, "pass")


class ManifestWriteSafetyTest(unittest.TestCase):
    def test_output_lock_rejects_second_process(self):
        with tempfile.TemporaryDirectory() as d, OutputWriteLock(Path(d)):
            script = (
                "import sys\n"
                "from pathlib import Path\n"
                "from gateway.errors import ConcurrentWriteError\n"
                "from gateway.manifest import OutputWriteLock\n"
                "try:\n"
                "    with OutputWriteLock(Path(sys.argv[1])):\n"
                "        pass\n"
                "except ConcurrentWriteError:\n"
                "    raise SystemExit(0)\n"
                "raise SystemExit(1)\n"
            )
            completed = subprocess.run([sys.executable, "-c", script, d], cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(completed.returncode, 0)

    def test_atomic_writers_do_not_share_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state.txt"
            values = [f"writer-{i}-" + (str(i) * 50_000) for i in range(8)]
            errors: list[Exception] = []

            def write(value: str) -> None:
                try:
                    atomic_write_text(target, value)
                except Exception as e:  # pragma: no cover - 断言应保持为空
                    errors.append(e)

            threads = [threading.Thread(target=write, args=(value,)) for value in values]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertIn(target.read_text(encoding="utf-8"), values)

    def test_stale_manifest_writer_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            save_manifest(_manifest(), out)
            first = load_manifest(out)
            stale = load_manifest(out)
            first.package_root_name = "first"
            save_manifest(first, out)
            stale.package_root_name = "stale"
            with self.assertRaises(ConcurrentWriteError):
                save_manifest(stale, out)

    def test_unsupported_schema_version_is_rejected_even_with_valid_hash(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            save_manifest(_manifest(), out)
            path = out / "manifest.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["schema_version"] = "99.0"
            doc["integrity"]["payload_sha256"] = payload_hash_of(doc)
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_manifest(out)

    def test_unknown_manifest_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            save_manifest(_manifest(), out)
            path = out / "manifest.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["unexpected"] = True
            doc["integrity"]["payload_sha256"] = payload_hash_of(doc)
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_manifest(out)


if __name__ == "__main__":
    unittest.main()
