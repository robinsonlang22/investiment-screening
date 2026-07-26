import unittest

from engine.aggregator import (
    COMPLIANT,
    CONDITIONAL,
    INFORMATION_INSUFFICIENT,
    NOT_COMPLIANT,
    aggregate_rule_results,
    build_evaluation_bundle,
    enforce_exception_cap,
    validate_evaluation_completeness,
)


class AggregatorTests(unittest.TestCase):
    def setUp(self):
        self.passed = [
            {"rule_id": "P1", "status": "PASS", "hard_veto": False},
            {"rule_id": "P2", "status": "PASS", "hard_veto": False},
            {"rule_id": "F1", "status": "PASS", "hard_veto": False},
        ]

    def test_priority(self):
        self.assertEqual(aggregate_rule_results(self.passed), COMPLIANT)
        self.assertEqual(
            aggregate_rule_results(
                [*self.passed[:2], {"rule_id": "F1", "status": "CONDITIONAL_PASS"}]
            ),
            CONDITIONAL,
        )
        self.assertEqual(
            aggregate_rule_results(
                [
                    *self.passed[:2],
                    {"rule_id": "F1", "status": "INSUFFICIENT_INFORMATION"},
                ]
            ),
            INFORMATION_INSUFFICIENT,
        )
        vetoed = [dict(item) for item in self.passed]
        vetoed[1]["hard_veto"] = True
        self.assertEqual(aggregate_rule_results(vetoed), NOT_COMPLIANT)

    def test_completeness(self):
        result = validate_evaluation_completeness(
            ["P1", "P2", "F1"], self.passed[:2]
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_rules"], ["F1"])

    def test_exception_cap_and_bundle_constraints(self):
        reviewed = [
            *self.passed[:2],
            {
                "rule_id": "F1",
                "status": "CONDITIONAL_PASS",
                "manual_override_applied": True,
                "human_review": {
                    "required": True,
                    "completed": True,
                    "confirmed_passive_adjustment": True,
                },
            },
        ]
        self.assertEqual(enforce_exception_cap(reviewed, COMPLIANT), CONDITIONAL)
        bundle = build_evaluation_bundle(
            "301536.SZ", reviewed, {"status": "VALID"}
        )
        self.assertEqual(bundle["overall_status"], CONDITIONAL)
        self.assertFalse(bundle["report_constraints"]["may_recalculate"])
        self.assertFalse(bundle["report_constraints"]["may_override_status"])


if __name__ == "__main__":
    unittest.main()
