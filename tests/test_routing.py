from __future__ import annotations

import contextlib
import io
import json
import unittest

from gateway import routing
from gateway.cli import main
from gateway.commands._common import build_registry
from gateway.magic import extension_catalog


class RoutingTableTest(unittest.TestCase):
    def test_pdf_feature_routes_preserve_priority_and_chains(self) -> None:
        cases = [
            ({"pdf_class": "encrypted", "possibly_corrupt": True}, ["qpdf"]),
            ({"possibly_corrupt": True}, ["pypdf", "pdf_lite"]),
            ({"pdf_class": "scanned"}, ["mineru"]),
            (
                {"pdf_class": "mixed_complex"},
                ["mineru", "docling", "pdfplumber", "pypdf", "pdf_lite"],
            ),
            ({"pdf_class": "digital_simple"}, ["docling", "pdfplumber", "pypdf", "pdf_lite"]),
        ]
        for features, expected in cases:
            with self.subTest(features=features):
                chain, reason = routing.resolve_chain("pdf", None, features)
                self.assertEqual(expected, chain)
                self.assertTrue(reason)

    def test_email_format_routes(self) -> None:
        expected = {
            None: ["eml_lite"],
            "eml": ["eml_lite"],
            "msg": ["extract_msg"],
            "pst": ["libpff"],
            "ost": ["libpff"],
        }
        for format_, chain in expected.items():
            with self.subTest(format=format_):
                self.assertEqual(chain, routing.resolve_chain("email", format_, {})[0])

    def test_every_recognized_extension_and_kind_has_a_catalog_route(self) -> None:
        rows = routing.route_catalog()
        routed_kinds = {row["kind"] for row in rows}
        routed_extensions = {ext for row in rows for ext in row["extensions"]}
        hints = extension_catalog()
        recognized_kinds = {hint["kind"] for hint in hints}
        recognized_extensions = {hint["extension"] for hint in hints}
        self.assertFalse(recognized_kinds - routed_kinds)
        self.assertFalse(recognized_extensions - routed_extensions)

    def test_every_parse_worker_is_registered(self) -> None:
        registry = build_registry()
        mismatches = [
            (rule.rule_id, worker)
            for rule in routing.ROUTE_RULES
            if rule.action == "parse"
            for worker in rule.workers
            if registry.get(worker) is None or rule.kind not in registry.get(worker).kinds
        ]
        self.assertFalse(mismatches)

    def test_rules_have_unique_ids_valid_actions_and_no_shadowing(self) -> None:
        ids = [rule.rule_id for rule in routing.ROUTE_RULES]
        self.assertEqual(len(ids), len(set(ids)))
        for index, rule in enumerate(routing.ROUTE_RULES):
            self.assertIn(rule.action, {"parse", "expand", "unresolved"})
            if rule.action == "parse":
                self.assertTrue(rule.workers, rule.rule_id)
            if not rule.formats and not rule.features:
                later_same_kind = [
                    candidate.rule_id
                    for candidate in routing.ROUTE_RULES[index + 1 :]
                    if candidate.kind == rule.kind
                ]
                self.assertFalse(later_same_kind, f"{rule.rule_id} 会遮蔽 {later_same_kind}")

    def test_archive_support_is_explicit(self) -> None:
        rows = {row["rule_id"]: row for row in routing.route_catalog(kind="archive")}
        self.assertEqual("expand", rows["archive-zip"]["action"])
        self.assertEqual(["stdlib:zipfile"], rows["archive-zip"]["tools"])
        self.assertEqual("unresolved", rows["archive-rar"]["action"])
        self.assertFalse(rows["archive-rar"]["processable"])

    def test_cli_json_is_machine_readable_and_filterable(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["routes", "--format", ".msg", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("gateway.routing.ROUTE_RULES", payload["authority"])
        self.assertEqual(["email-msg"], [row["rule_id"] for row in payload["routes"]])
        self.assertEqual(["extract_msg"], payload["routes"][0]["workers"])


if __name__ == "__main__":
    unittest.main()
