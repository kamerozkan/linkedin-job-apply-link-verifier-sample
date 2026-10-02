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
        self.run = {"id": "syntheticRun0001", "buildId": "syntheticBuild01", "defaultDatasetId": "syntheticDataset1",
                    "status": "SUCCEEDED", "startedAt": "2026-09-30T21:15:56.473Z", "finishedAt": "2026-09-30T21:16:07.107Z",
                    "chargedEventCounts": {"verified-job": 2}, "usageTotalUsd": 0.01}
    def test_diagnostics_are_counted_without_charging_them(self):
        result = analyze(self.run, self.rows, self.output, "0.0045")
        self.assertEqual(result["datasetRows"], 4)
        self.assertEqual(result["validUniqueRows"], 2)
        self.assertEqual(result["chargedUsefulEvents"], 2)
        self.assertEqual(result["usefulRateOfDatasetRows"], .5)
        self.assertEqual(result["usefulRateOfValidUniqueRows"], 1)
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
    def test_unsafe_action_url_is_not_publishable(self):
        for url in ["javascript:alert(1)", "http://example.com/apply", "https://127.0.0.1/", "https://[::1]/", "https://localhost/", "https://company.internal/", "https://0177.0.0.1/", "https://2130706433/", "https://0x7f000001/", "https://user:secret@example.com/", "https://example.com\\@127.0.0.1/", "https://example.com:8443/"]:
            with self.subTest(url=url):
                rows = copy.deepcopy(self.rows)
                rows[2]["actionUrl"] = url
                with self.assertRaisesRegex(ValueError, "Unsafe publication"):
                    analyze(self.run, rows, self.output, ".0045")
    def test_review_required_cannot_be_safe_to_publish(self):
        self.rows[2]["reviewRequired"] = True
        with self.assertRaisesRegex(ValueError, "Unsafe publication"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_boolean_shaped_flags_and_counts_are_rejected(self):
        for key in ["safeToPublish", "reviewRequired", "usefulClassification"]:
            with self.subTest(key=key):
                rows = copy.deepcopy(self.rows)
                rows[2][key] = int(rows[2][key])
                with self.assertRaisesRegex(ValueError, "actual booleans"):
                    analyze(self.run, rows, self.output, ".0045")
        self.output["safeToPublishRows"] = True
        with self.assertRaisesRegex(ValueError, "integer"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_partial_dataset_is_rejected_by_delivery_count(self):
        self.output["deliveredRows"] = 999
        with self.assertRaisesRegex(ValueError, "delivery counts"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_repeated_or_orphan_source_indexes_are_rejected(self):
        for bad_index in [self.rows[0]["sourceIndex"], 999, True, -1]:
            with self.subTest(index=bad_index):
                rows = copy.deepcopy(self.rows)
                rows[2]["sourceIndex"] = bad_index
                with self.assertRaises(ValueError):
                    analyze(self.run, rows, self.output, ".0045")
    def test_source_job_identity_mismatch_is_rejected(self):
        self.rows[2]["linkedinUrl"] = "https://www.linkedin.com/jobs/view/999999"
        with self.assertRaisesRegex(ValueError, "identity/source"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_repeated_valid_job_identity_is_rejected(self):
        self.rows[3]["jobId"] = self.rows[2]["jobId"]
        self.rows[3]["linkedinUrl"] = self.rows[2]["linkedinUrl"]
        with self.assertRaisesRegex(ValueError, "duplicated"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_easy_apply_route_matches_its_job_not_just_linkedin_host(self):
        self.rows[3]["actionUrl"] = "https://www.linkedin.com/jobs/view/999999"
        with self.assertRaisesRegex(ValueError, "Easy Apply"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_timezone_and_row_clock_must_belong_to_run(self):
        for checked in ["2026-09-29T21:16:01Z", "2026-09-30T21:16:01", "not-a-date", "9999-12-31T23:59:59-14:00"]:
            with self.subTest(checked=checked):
                rows = copy.deepcopy(self.rows)
                rows[2]["checkedAt"] = checked
                with self.assertRaises(ValueError):
                    analyze(self.run, rows, self.output, ".0045")
    def test_run_identity_output_identity_and_chronology(self):
        for key in ["id", "buildId", "defaultDatasetId"]:
            with self.subTest(key=key):
                run = copy.deepcopy(self.run)
                run.pop(key)
                with self.assertRaisesRegex(ValueError, "provenance"):
                    analyze(run, self.rows, self.output, ".0045")
        self.output["runId"] = "foreignRun0001"
        with self.assertRaisesRegex(ValueError, "identity conflicts"):
            analyze(self.run, self.rows, self.output, ".0045")
        self.output.pop("runId")
        self.run["finishedAt"] = "2026-09-29T21:16:07Z"
        with self.assertRaisesRegex(ValueError, "chronology"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_failed_output_does_not_produce_contribution(self):
        self.output["status"] = "FAILED_QUALITY_GATE"
        with self.assertRaisesRegex(ValueError, "OUTPUT"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_unknown_billing_protection_is_not_false_confirmation(self):
        self.output["billing"].pop("unresolvedRowsCharged")
        with self.assertRaisesRegex(ValueError, "unbillable"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_accounted_events_never_added_to_scenario_gross_value(self):
        self.run["accountedChargedEventCounts"] = {"verified-job": 2}
        result = analyze(self.run, self.rows, self.output, ".0045")
        self.assertEqual(result["scenarioGrossEventValueUsd"], .009)
        self.assertIsNone(result["realizedCustomerRevenueUsd"])
        self.run["accountedChargedEventCounts"]["verified-job"] = 3
        with self.assertRaisesRegex(ValueError, "Accounted events"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_useful_expired_and_ambiguous_are_distinct_from_publishable(self):
        self.rows[2].update(status="EXPIRED", safeToPublish=False, reviewRequired=True, actionUrl=None)
        self.rows[3].update(status="AMBIGUOUS", usefulClassification=False, safeToPublish=False, reviewRequired=True, actionUrl=None)
        self.run["chargedEventCounts"]["verified-job"] = 1
        self.output.update(usefulClassifications=1, safeToPublishRows=0, reviewRequiredRows=2, statusCounts={"EXPIRED": 1, "AMBIGUOUS": 1})
        result = analyze(self.run, self.rows, self.output, ".0045")
        self.assertEqual((result["usefulDecisions"], result["publishableDecisions"], result["unresolvedRows"]), (1, 0, 1))
    def test_invalid_decimal_and_extreme_finite_values_fail_cleanly(self):
        for price in ["not a number", True, "1E999", "-1", "Infinity"]:
            with self.subTest(price=price), self.assertRaises(ValueError):
                analyze(self.run, self.rows, self.output, price)
    def test_request_accounting_is_not_double_counted_or_silently_mismatched(self):
        result = analyze(self.run, self.rows, self.output, ".0045")
        self.assertTrue(result["rowRequestAccountingVerified"])
        self.assertEqual(result["requests"], 8)
        self.rows[2]["requestAccounting"]["total"] = 999
        with self.assertRaisesRegex(ValueError, "Row request total"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_output_request_total_matches_row_components(self):
        self.output["requests"] = 999
        with self.assertRaisesRegex(ValueError, "OUTPUT requests"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_free_report_charge_is_rejected(self):
        self.run["chargedEventCounts"]["verification-report"] = 1
        with self.assertRaisesRegex(ValueError, "Unpriced report"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_status_denominators_do_not_hide_diagnostic_rows(self):
        self.output["statusCounts"] = {"EXPIRED": 2}
        with self.assertRaisesRegex(ValueError, "statusCounts"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_ppe_mode_must_be_a_real_boolean(self):
        for mode in ["false", "true", 0, 1, None]:
            with self.subTest(mode=mode):
                self.output["billing"]["payPerEventActive"] = mode
                with self.assertRaisesRegex(ValueError, "actual boolean"):
                    analyze(self.run, self.rows, self.output, ".0045")
    def test_ppe_mode_and_charge_count_must_be_consistent(self):
        self.output["billing"]["payPerEventActive"] = False
        with self.assertRaisesRegex(ValueError, "billing mode"):
            analyze(self.run, self.rows, self.output, ".0045")
        self.output["billing"]["payPerEventActive"] = True
        self.run["chargedEventCounts"]["verified-job"] = 0
        with self.assertRaisesRegex(ValueError, "billing mode"):
            analyze(self.run, self.rows, self.output, ".0045")
    def test_inactive_ppe_can_return_useful_decisions_without_charges(self):
        self.output["billing"]["payPerEventActive"] = False
        self.run["chargedEventCounts"]["verified-job"] = 0
        result = analyze(self.run, self.rows, self.output, ".0045")
        self.assertFalse(result["payPerEventActive"])
        self.assertEqual(result["usefulDecisions"], 2)
        self.assertEqual(result["chargedUsefulEvents"], 0)
        self.assertEqual(result["scenarioGrossEventValueUsd"], 0)
        self.assertIsNone(result["realizedCustomerRevenueUsd"])
    def test_requested_input_coverage_is_separate_from_complete_export(self):
        self.rows = self.rows[2:]
        self.output.update(deliveredRows=2, invalidRows=0, duplicateRowsDropped=0)
        result = analyze(self.run, self.rows, self.output, ".0045")
        self.assertTrue(result["exportedDatasetMatchesOutput"])
        self.assertEqual(result["requestedRows"], 4)
        self.assertEqual(result["deliveredRows"], 2)
        self.assertEqual(result["inputRowsWithoutDecision"], 2)
        self.assertEqual(result["inputCoverageRate"], .5)
        self.assertFalse(result["requestedBatchFullyDelivered"])
        self.assertEqual(result["usefulRateOfDatasetRows"], 1)
if __name__ == "__main__":
    unittest.main()
