"""多链执行计划（ADR 0005）：profile 决定跑几条，探测门控决定值不值得跑。"""

import unittest

from gateway import routing


def _plan(profile, state=None, kind="pdf"):
    signals = {"page_images": state} if state else None
    return routing.resolve_plan(kind, None, {}, signals, profile=profile)


class TestProfileGating(unittest.TestCase):
    """三档的实质区别就是门控松紧。"""

    def test_fast_never_runs_parallel_chains(self):
        for state in ("present", "unknown", "absent"):
            plan, _ = _plan("fast", state)
            self.assertEqual([p.rule_id for p in plan], ["pdf-digital-simple"], state)

    def test_balanced_runs_only_on_confirmed_signal(self):
        self.assertEqual(len(_plan("balanced", "present")[0]), 2)
        self.assertEqual(len(_plan("balanced", "unknown")[0]), 1)

    def test_quality_also_runs_on_unknown(self):
        """探测不到不等于不存在——重点材料宁可多跑一次。"""
        self.assertEqual(len(_plan("quality", "unknown")[0]), 2)
        self.assertEqual(len(_plan("quality", "present")[0]), 2)

    def test_absent_blocks_every_profile(self):
        """确认没有图还去 OCR 是纯浪费，这正是探测门控省钱的地方。"""
        for profile in routing.PROFILES:
            plan, _ = _plan(profile, "absent")
            self.assertEqual([p.rule_id for p in plan], ["pdf-digital-simple"], profile)


class TestSkippingIsAlwaysRecorded(unittest.TestCase):
    """ADR 0005 决策 5：少跑必须显性，静默省略等同于假装跑过了。"""

    def test_every_skipped_chain_leaves_a_reason(self):
        cases = [("fast", "present"), ("balanced", "absent"), ("balanced", "unknown"), ("quality", "absent")]
        for profile, state in cases:
            plan, skipped = _plan(profile, state)
            self.assertEqual(len(skipped), 1, f"{profile}/{state} 少跑了却没记录")
            self.assertEqual(skipped[0]["rule_id"], "pdf-visual")
            self.assertTrue(skipped[0]["reason"], "跳过记录必须带原因")

    def test_reason_distinguishes_why(self):
        """「你让我跑快的」和「探测到没有图」是两种不同的少跑，不能混成一句。"""
        self.assertEqual(_plan("fast", "present")[1][0]["reason"], "profile_fast")
        self.assertEqual(_plan("balanced", "absent")[1][0]["reason"], "signal_page_images_absent")
        self.assertEqual(_plan("balanced", "unknown")[1][0]["reason"], "signal_page_images_unknown")

    def test_nothing_skipped_means_empty_record(self):
        plan, skipped = _plan("balanced", "present")
        self.assertEqual(len(plan), 2)
        self.assertEqual(skipped, [])

    def test_dangling_declaration_is_recorded_not_ignored(self):
        """并跑声明指向不存在的规则时，不能悄悄当无事发生。"""
        rule = routing.RouteRule(
            "probe-dangling", "pdf", "测试用",
            ("pypdf",),
            parallel_with=(routing.ParallelChain("no-such-rule"),),
        )
        original = routing.ROUTE_RULES
        routing.ROUTE_RULES = (rule,) + original
        try:
            plan, skipped = routing.resolve_plan("pdf", None, {}, {"page_images": "present"})
        finally:
            routing.ROUTE_RULES = original
        self.assertEqual([p.rule_id for p in plan], ["probe-dangling"])
        self.assertEqual(skipped, [{"rule_id": "no-such-rule", "reason": "unknown_rule"}])


class TestPlanShape(unittest.TestCase):
    def test_primary_chain_is_first_and_not_marked_parallel(self):
        plan, _ = _plan("quality", "present")
        self.assertFalse(plan[0].parallel)
        self.assertTrue(plan[1].parallel)

    def test_plan_carries_reason_for_each_chain(self):
        """每条链都要能说出自己为什么被选中——adg routes 与报告共用这句话。"""
        for chain in _plan("quality", "present")[0]:
            self.assertTrue(chain.reason.strip(), chain.rule_id)

    def test_order_is_execution_order_not_ranking(self):
        """顺序只表示先后，不表示优劣（ADR 0005 决策 4）。

        这里钉住的是数据形状：PlannedChain 不带任何分数/排名字段，
        想加的人得先修订 ADR 0005。
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
