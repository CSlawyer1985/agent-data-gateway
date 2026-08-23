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
            # 加密：先试能本地解开的（多数只是所有者限制、用户口令为空），qpdf 兜底
            ({"pdf_class": "encrypted", "possibly_corrupt": True}, ["pypdf", "pdfplumber", "qpdf"]),
            ({"possibly_corrupt": True}, ["pypdf", "pdf_lite"]),
            # 扫描：MinerU 需要 Docker，无容器机器上 pdf_render 渲染位图交给 image 链 OCR
            ({"pdf_class": "scanned"}, ["mineru", "pdf_render"]),
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

    def test_office_chains_include_anydoc_after_lite_parsers(self) -> None:
        """anydoc 是零系统依赖的第二意见，应排在 lite parser 之后、重型工具之前。"""
        for kind, lite in (("docx", "docx_lite"), ("doc", "doc_lite"), ("xlsx", "xlsx_lite"), ("xls", "xls")):
            with self.subTest(kind=kind):
                chain = routing.resolve_chain(kind, None, {})[0]
                self.assertIn("anydoc", chain)
                self.assertLess(chain.index(lite), chain.index("anydoc"))
                for heavy in ("docling", "officecli"):
                    if heavy in chain:
                        self.assertLess(chain.index("anydoc"), chain.index(heavy))

    def test_formats_without_builtin_parser_route_to_anydoc_first(self) -> None:
        """RTF/EPUB/ODS/ODP 此前无 parser——现在必须有明确的处理链，且不再靠 lite parser 空跑。"""
        for kind, fmt in (("doc", "rtf"), ("doc", "epub"), ("xlsx", "ods"), ("presentation", "odp")):
            with self.subTest(kind=kind, format=fmt):
                chain = routing.resolve_chain(kind, fmt, {})[0]
                self.assertEqual("anydoc", chain[0])

    def test_dwg_prefers_direct_read_over_lossy_dxf_conversion(self) -> None:
        """实测：dwg2dxf 转出的 DXF 常结构性损坏，连 ezdxf.recover 都读不了——直读必须排在前面。"""
        chain = routing.resolve_chain("dwg", None, {})[0]
        self.assertEqual("dwgread", chain[0])
        self.assertLess(chain.index("dwgread"), chain.index("libredwg"))

    def test_caj_is_recognized_but_honestly_unresolved(self) -> None:
        """知网 CAJ 无开源解析器：必须从"类型无法识别"升级为"知道是什么 + 怎么办"。"""
        rows = [row for row in routing.route_catalog() if row["kind"] == "caj"]
        self.assertEqual(1, len(rows))
        self.assertEqual("unresolved", rows[0]["action"])
        self.assertIn("CAJViewer", rows[0]["reason"] + str(rows[0].get("tools") or ""))

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
                # 并跑目标规则（ADR 0005）不参与首条命中，被默认规则排在后面是设计如此。
                # 判据用「是否被某条 parallel_with 引用」而不是自报标记：被引用是自动的，
                # 而「标了只作并跑却没人引用」照样是死规则，必须仍被这条守卫抓到。
                parallel_targets = {
                    target.rule_id
                    for candidate in routing.ROUTE_RULES
                    for target in candidate.parallel_with
                }
                later_same_kind = [
                    candidate.rule_id
                    for candidate in routing.ROUTE_RULES[index + 1 :]
                    if candidate.kind == rule.kind and candidate.rule_id not in parallel_targets
                ]
                self.assertFalse(later_same_kind, f"{rule.rule_id} 会遮蔽 {later_same_kind}")

    def test_shadowing_check_still_catches_a_truly_dead_rule(self) -> None:
        """上一条放行了并跑目标，这条证明它没被放宽成摆设。

        植入一条既不会被首条命中、也没有任何 parallel_with 引用的规则——
        它必须仍被判为遮蔽。
        """
        dead = routing.RouteRule("probe-dead-rule", "pdf", "测试用", ("pypdf",))
        original = routing.ROUTE_RULES
        routing.ROUTE_RULES = original + (dead,)
        try:
            with self.assertRaises(AssertionError):
                self.test_rules_have_unique_ids_valid_actions_and_no_shadowing()
        finally:
            routing.ROUTE_RULES = original

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
