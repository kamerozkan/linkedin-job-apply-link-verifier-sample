import copy
import json
import unittest
from pathlib import Path
from analyze_run import analyze

ROOT = Path(__file__).parent
class AuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((ROOT / "05_current_billing_validation_output.json").read_text())
        self.output = json.loads((ROOT / "05_current_billing_validation_summary.json").read_text())
        self.run = {"status": "SUCCEEDED", "chargedEventCounts": {"verified-job": 2}, "usageTotalUsd": 0.01}
    def test_diagnostics_are_counted_without_charging_them(self):
        result = analyze(self.run, self.rows, self.output, "0.0045")
        self.assertEqual(result["datasetRows"], 4)
        self.assertEqual(result["validUniqueRows"], 2)
        self.assertEqual(result["chargedUsefulEvents"], 2)
        self.assertAlmostEqual(result["scenarioPaidContributionUsingOwnerCostUsd"], -0.0028)
        self.assertIsNone(result["realizedCustomerRevenueUsd"])
    def test_missing_usage_keeps_cost_and_contribution_unknown(self):
        self.run.pop("usageTotalUsd")
        result = analyze(self.run, self.rows, self.output, "0.0045")
        self.assertIsNone(result["ownerUsageSnapshotUsd"])
        self.assertIsNone(result["scenarioPaidContributionUsingOwnerCostUsd"])
    def test_charge_diagnostics_counter_is_rejected(self):
        self.run["chargedEventCounts"]["verified-job"] = 4
        with self.assertRaisesRegex(ValueError, "Charged decisions"):
            analyze(self.run, self.rows, self.output, "0.0045")
    def test_unproven_row_cannot_release_a_url(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["actionUrl"] = "https://example.com/jobs/unsafe"
        with self.assertRaisesRegex(ValueError, "unproven"):
            analyze(self.run, rows, self.output, "0.0045")
    def test_summary_dataset_disagreement_is_rejected(self):
        self.output["usefulClassifications"] = 1
        with self.assertRaisesRegex(ValueError, "OUTPUT and dataset"):
            analyze(self.run, self.rows, self.output, "0.0045")
    def test_failed_run_and_nonfinite_prices_do_not_produce_profit(self):
        self.run["status"] = "FAILED"
        with self.assertRaises(ValueError):
            analyze(self.run, self.rows, self.output, "0.0045")
        self.run["status"] = "SUCCEEDED"
        with self.assertRaises(ValueError):
            analyze(self.run, self.rows, self.output, "NaN")
if __name__ == "__main__":
    unittest.main()

