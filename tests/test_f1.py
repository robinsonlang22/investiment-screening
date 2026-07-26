import unittest

from engine.f1_evaluator import (
    CONDITIONAL_PASS,
    FAIL,
    PASS,
    apply_human_review,
    choose_threshold_tier,
    detect_human_review_trigger,
    evaluate_f1,
    evaluate_outflow_vetoes,
)
from tests.helpers import load_rule


class F1EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_rule("f1")
        cls.base = {
            "leverage_ratio_pct": 8,
            "c20_pct": 0,
            "margin_trend": {"gb_daily_pct": 0},
            "worst_daily_outflow": {
                "change_pct": -1,
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            },
            "worst_three_day_outflow": {
                "change_pct": -2,
                "start_date": "2025-12-30",
                "end_date": "2026-01-02",
            },
            "market_deleveraging": [],
        }

    def test_tier_boundaries_and_crowding(self):
        self.assertEqual(choose_threshold_tier(5, self.config)["tier"], "RELAXED")
        self.assertEqual(
            choose_threshold_tier(5.0001, self.config)["tier"], "STRICT"
        )
        self.assertFalse(
            choose_threshold_tier(10, self.config)["high_crowding"]
        )
        self.assertTrue(
            choose_threshold_tier(10.0001, self.config)["high_crowding"]
        )

    def test_veto_boundaries(self):
        strict = choose_threshold_tier(8, self.config)["thresholds"]
        relaxed = choose_threshold_tier(4, self.config)["thresholds"]
        self.assertIn(
            "single_day_outflow",
            evaluate_outflow_vetoes(-3, -4, strict)["triggered_vetoes"],
        )
        self.assertIn(
            "three_day_outflow",
            evaluate_outflow_vetoes(-2, -5, strict)["triggered_vetoes"],
        )
        self.assertIn(
            "single_day_outflow",
            evaluate_outflow_vetoes(-4, -5, relaxed)["triggered_vetoes"],
        )
        self.assertIn(
            "three_day_outflow",
            evaluate_outflow_vetoes(-3, -6, relaxed)["triggered_vetoes"],
        )

    def test_pass_conditional_and_fail(self):
        self.assertEqual(evaluate_f1(self.base, self.config)["status"], PASS)
        one_failed = {**self.base, "c20_pct": -2.1}
        self.assertEqual(
            evaluate_f1(one_failed, self.config)["status"], CONDITIONAL_PASS
        )
        both_failed = {
            **self.base,
            "c20_pct": -2.1,
            "margin_trend": {"gb_daily_pct": -0.11},
        }
        self.assertEqual(evaluate_f1(both_failed, self.config)["status"], FAIL)

    def test_human_review_requires_matching_systemic_date_and_caps_result(self):
        veto = {
            **self.base,
            "worst_daily_outflow": {
                "change_pct": -3,
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            },
            "market_deleveraging": [
                {
                    "change_pct": -2.6,
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-02",
                    "below_minus_2_5_pct": True,
                }
            ],
        }
        result = evaluate_f1(veto, self.config)
        self.assertTrue(result["human_review"]["required"])
        reviewed = apply_human_review(
            result,
            True,
            "reviewer-a",
            "市场同步去杠杆且个股无异常放量",
        )
        self.assertEqual(reviewed["status"], CONDITIONAL_PASS)
        self.assertNotEqual(reviewed["status"], PASS)
        no_match = detect_human_review_trigger(
            FAIL, ["2026-01-03"], veto["market_deleveraging"]
        )
        self.assertFalse(no_match["required"])


if __name__ == "__main__":
    unittest.main()
