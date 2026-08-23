"""执行计划（ADR 0005）：profile 选工作流，探测门控决定值不值得多跑一条。

三档的语义：
  fast      挑链上轻的工具，尽快拿到一个能用的结果；不并跑
  balanced  整条链、先成功先得，拿到效果最好的可用工具；不并跑
  quality   整条链 + 并跑声明的其他链，多个工作流各自出结果
"""

import unittest

from gateway import routing

# 测试里把 docling 当重工具（真实判断由 dispatcher 查 worker.json 的 weight/backend）
_SLOW = {"docling", "mineru", "paddleocr", "rapidocr"}


def _plan(profile, state=None, *, is_slow=lambda n: n in _SLOW):
    signals = {"page_images": state} if state else None
    return routing.resolve_plan("pdf", None, {}, signals, profile=profile, is_slow=is_slow)


class TestProfileSelectsTheWorkflow(unittest.TestCase):
    """fast 与 balanced 的区别在主轨用什么工具，不在跑几条。"""

    def test_fast_drops_heavy_tools_from_the_primary_chain(self):
        workers = _plan("fast", "present")[0][0].workers
        self.assertNotIn("docling", workers)
        self.assertEqual(workers[0], "pdfplumber")

    def test_balanced_keeps_the_whole_chain(self):
        workers = _plan("balanced", "present")[0][0].workers
        self.assertEqual(workers[0], "docling")

    def test_dropped_tools_are_recorded_with_scope_worker(self):
        """跳过工具≠跳过链：前者是「看了但没用最好的工具」，后者是「这个角度没看过」。"""
        _, skipped = _plan("fast", "present")
        dropped = [row for row in skipped if row.get("scope") == "worker"]
        self.assertEqual([row["worker"] for row in dropped], ["docling"])
        self.assertEqual(dropped[0]["reason"], "profile_fast_heavy")
        self.assertEqual(dropped[0]["rule_id"], "pdf-digital-simple")

    def test_all_heavy_chain_is_not_emptied(self):
        """全链都重时不能把主轨清空——宁可慢，也不能什么都不做。"""
        plan, _ = _plan("fast", "present", is_slow=lambda n: True)
        self.assertEqual(plan[0].workers[0], "docling")


class TestParallelIsQualityOnly(unittest.TestCase):
    def test_fast_and_balanced_never_run_parallel_chains(self):
        for profile in ("fast", "balanced"):
            for state in ("present", "unknown", "absent"):
                plan, _ = _plan(profile, state)
                self.assertEqual([p.rule_id for p in plan], ["pdf-digital-simple"], f"{profile}/{state}")

    def test_quality_runs_it_on_present_and_unknown(self):
        """探测不到不等于不存在——重点材料宁可多跑一次。"""
        for state in ("present", "unknown"):
            self.assertEqual(len(_plan("quality", state)[0]), 2, state)

    def test_absent_blocks_even_quality(self):
        """确认没有图还去 OCR 是纯浪费，这正是探测门控省钱的地方。"""
        plan, skipped = _plan("quality", "absent")
        self.assertEqual([p.rule_id for p in plan], ["pdf-digital-simple"])
        self.assertIn("absent", skipped[0]["reason"])


class TestSkippingIsAlwaysRecorded(unittest.TestCase):
    """ADR 0005 决策 5：少跑必须显性，静默省略等同于假装跑过了。"""

    def _chain_skips(self, profile, state):
        return [row for row in _plan(profile, state)[1] if row.get("scope") == "chain"]

    def test_every_unrun_chain_leaves_a_reason(self):
        for profile, state in [("fast", "present"), ("balanced", "present"), ("quality", "absent")]:
            rows = self._chain_skips(profile, state)
            self.assertEqual(len(rows), 1, f"{profile}/{state} 少跑了却没记录")
            self.assertEqual(rows[0]["rule_id"], "pdf-visual")
            self.assertTrue(rows[0]["reason"])

    def test_reason_distinguishes_why(self):
        """「你让我跑快的」「你没要多跑」「探测到没有图」是三种不同的少跑。"""
        self.assertEqual(self._chain_skips("fast", "present")[0]["reason"], "profile_fast")
        self.assertEqual(self._chain_skips("balanced", "present")[0]["reason"], "profile_balanced")
        self.assertEqual(self._chain_skips("quality", "absent")[0]["reason"], "signal_page_images_absent")

    def test_nothing_skipped_means_empty_record(self):
        plan, skipped = _plan("quality", "present")
        self.assertEqual(len(plan), 2)
        self.assertEqual(skipped, [])

    def test_dangling_declaration_is_recorded_not_ignored(self):
        """并跑声明指向不存在的规则时，不能悄悄当无事发生。"""
        rule = routing.RouteRule(
            "probe-dangling", "pdf", "测试用", ("pypdf",),
            parallel_with=(routing.ParallelChain("no-such-rule"),),
        )
        original = routing.ROUTE_RULES
        routing.ROUTE_RULES = (rule,) + original
        try:
            plan, skipped = routing.resolve_plan(
                "pdf", None, {}, {"page_images": "present"}, profile="quality"
            )
        finally:
            routing.ROUTE_RULES = original
        self.assertEqual([p.rule_id for p in plan], ["probe-dangling"])
        self.assertEqual(skipped, [{"scope": "chain", "rule_id": "no-such-rule", "reason": "unknown_rule"}])


class TestPlanShape(unittest.TestCase):
    def test_primary_chain_is_first_and_not_marked_parallel(self):
        plan, _ = _plan("quality", "present")
        self.assertFalse(plan[0].parallel)
        self.assertTrue(plan[1].parallel)

    def test_plan_carries_reason_for_each_chain(self):
        for chain in _plan("quality", "present")[0]:
            self.assertTrue(chain.reason.strip(), chain.rule_id)

    def test_order_is_execution_order_not_ranking(self):
        """顺序只表示先后，不表示优劣（ADR 0005 决策 4）。

        钉住数据形状：PlannedChain 不带任何分数/排名字段，想加的人得先修订 ADR。
        """
        chain = _plan("quality", "present")[0][0]
        for banned in ("score", "rank", "confidence", "quality"):
            self.assertFalse(hasattr(chain, banned), f"PlannedChain.{banned} 会让 ADG 变成裁判")

    def test_unrouted_kind_yields_empty_plan(self):
        plan, skipped = routing.resolve_plan("no-such-kind", None, {}, None)
        self.assertEqual(plan, [])
        self.assertEqual(skipped, [])


class TestRoutesOutputIsHonest(unittest.TestCase):
    """`adg routes` 是 spec 08 声明的查询入口，属于对外可见契约。"""

    def setUp(self):
        self.rows = {row["rule_id"]: row for row in routing.route_catalog(kind="pdf")}

    def test_parallel_target_declares_who_triggers_it(self):
        target = self.rows["pdf-visual"]
        self.assertEqual(target["parallel_from"], [{"rule_id": "pdf-digital-simple", "requires_signal": "page_images"}])

    def test_declaring_rule_lists_what_it_runs_alongside(self):
        self.assertEqual(
            self.rows["pdf-digital-simple"]["parallel_with"],
            [{"rule_id": "pdf-visual", "requires_signal": "page_images"}],
        )

    def test_parallel_target_is_never_shown_as_default(self):
        """它不参与首条命中。显示「默认」会让读者以为同一 kind 有两条默认规则。"""
        from gateway.commands.routes import _condition

        self.assertEqual(_condition(self.rows["pdf-digital-simple"]), "默认")
        text = _condition(self.rows["pdf-visual"])
        self.assertNotEqual(text, "默认")
        self.assertIn("并跑", text)
        self.assertIn("page_images", text)


if __name__ == "__main__":
    unittest.main()
