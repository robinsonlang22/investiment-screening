import unittest

from engine.aggregator import (
    COMPLIANT,
    INFORMATION_INSUFFICIENT,
    NOT_COMPLIANT,
    aggregate_rule_results,
    build_evaluation_bundle,
    validate_evaluation_completeness,
)


class AggregatorTests(unittest.TestCase):
    def setUp(self):
        self.passed = [
            {"rule_id": "P1", "status": "PASS", "hard_veto": False},
            {"rule_id": "P2", "status": "PASS", "hard_veto": False},
        ]

    def test_priority(self):
        self.assertEqual(aggregate_rule_results(self.passed), COMPLIANT)
        insufficient = [self.passed[0], {"rule_id": "P2", "status": "INSUFFICIENT_INFORMATION"}]
        self.assertEqual(aggregate_rule_results(insufficient), INFORMATION_INSUFFICIENT)
        vetoed = [dict(item) for item in self.passed]
        vetoed[1]["hard_veto"] = True
        self.assertEqual(aggregate_rule_results(vetoed), NOT_COMPLIANT)

    def test_completeness(self):
        result = validate_evaluation_completeness(["P1", "P2"], self.passed[:1])
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_rules"], ["P2"])

    def test_bundle_constraints(self):
        bundle = build_evaluation_bundle("301536.SZ", self.passed, {"status": "VALID"})
        self.assertEqual(bundle["overall_status"], COMPLIANT)
        self.assertFalse(bundle["report_constraints"]["may_recalculate"])
        self.assertFalse(bundle["report_constraints"]["may_override_status"])


if __name__ == "__main__":
    unittest.main()
