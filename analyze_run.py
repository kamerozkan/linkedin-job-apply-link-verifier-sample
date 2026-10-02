#!/usr/bin/env python3
"""Audit exported verifier output without inferring realized customer revenue."""
import argparse
import ipaddress
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

USEFUL = {"ACTIVE", "AUTHORIZED_APPLY_ROUTE", "LINKEDIN_EASY_APPLY", "EXPIRED", "SOURCE_MISMATCH", "STATUS_CONFLICT"}
UNBILLABLE = {"AMBIGUOUS", "INVALID_INPUT", "DUPLICATE_INPUT"}
PUBLISHABLE = {"ACTIVE", "AUTHORIZED_APPLY_ROUTE", "LINKEDIN_EASY_APPLY"}

def when(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (AttributeError, ValueError, OverflowError):
        raise ValueError(label + " must be an ISO timestamp with timezone.") from None

def public_https(value):
    """Validate URL syntax without fetching a URL or claiming DNS verification."""
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\x00-\x20\x7f\\]", value):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port or not host:
            return False
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return ("." in host and re.fullmatch(r"[a-z0-9.-]+", host) is not None
                    and all(label and not label.startswith("-") and not label.endswith("-") for label in host.split("."))
                    and re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", host.split(".")[-1], re.I) is None)
    except ValueError:
        return False

def integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(label + " must be a nonnegative integer.")
    return value

def analyze(run, rows, output, scenario_event_price):
    if not isinstance(run, dict) or run.get("status") != "SUCCEEDED":
        raise ValueError("The cloud run did not finish successfully.")
    if not isinstance(output, dict) or output.get("status") != "SUCCEEDED":
        raise ValueError("Verifier OUTPUT did not finish successfully.")
    for key in ("id", "buildId", "defaultDatasetId"):
        if not isinstance(run.get(key), str) or not re.fullmatch(r"[A-Za-z0-9]{4,64}", run[key]):
            raise ValueError("Missing or invalid run/build/dataset provenance.")
    started, finished = when(run.get("startedAt"), "startedAt"), when(run.get("finishedAt"), "finishedAt")
    if finished < started:
        raise ValueError("Run chronology is invalid.")
    if output.get("generatedAt") is not None and not started - timedelta(seconds=5) <= when(output["generatedAt"], "OUTPUT.generatedAt") <= finished + timedelta(seconds=5):
        raise ValueError("OUTPUT timestamp does not belong to the exported run window.")
    for output_key, run_key in (("runId", "id"), ("buildId", "buildId"), ("datasetId", "defaultDatasetId")):
        if output.get(output_key) is not None and output[output_key] != run[run_key]:
            raise ValueError("OUTPUT run/build/dataset identity conflicts with the run export.")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Expected exported dataset objects.")
    requested = integer(output.get("requestedRows"), "OUTPUT.requestedRows")
    if integer(output.get("deliveredRows"), "OUTPUT.deliveredRows") != len(rows) or len(rows) > requested:
        raise ValueError("OUTPUT and dataset delivery counts differ.")
    counts = Counter()
    useful = publishable = diagnostic = unresolved = review_required = request_total = 0
    accounting_rows = 0
    indexes, job_ids = set(), set()
    for row in rows:
        index = integer(row.get("sourceIndex"), "sourceIndex")
        if index >= requested or index in indexes:
            raise ValueError("Duplicate or out-of-range sourceIndex.")
        indexes.add(index)
        status = row.get("status")
        if status not in USEFUL | UNBILLABLE:
            raise ValueError("Unknown decision status.")
        for flag in ("safeToPublish", "reviewRequired", "usefulClassification"):
            if type(row.get(flag)) is not bool:
                raise ValueError("Decision flags must be actual booleans.")
        checked = when(row.get("checkedAt"), "checkedAt")
        if not started - timedelta(seconds=5) <= checked <= finished + timedelta(seconds=5):
            raise ValueError("Decision timestamp does not belong to the exported run window.")
        if status not in {"INVALID_INPUT", "DUPLICATE_INPUT"}:
            job_id = row.get("jobId")
            source = row.get("linkedinUrl")
            parsed = urlsplit(source) if public_https(source) else None
            host = (parsed.hostname or "").lower() if parsed else ""
            match = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d{1,24})/?", parsed.path) if parsed else None
            if (not isinstance(job_id, str) or not re.fullmatch(r"\d{1,24}", job_id)
                    or not (host == "linkedin.com" or host.endswith(".linkedin.com"))
                    or not match or match.group(1) != job_id or job_id in job_ids):
                raise ValueError("Valid decision job identity/source URL is invalid or duplicated.")
            job_ids.add(job_id)
        counts[status] += 1
        expected_useful = status in USEFUL
        if row.get("usefulClassification") is not expected_useful:
            raise ValueError("The useful-decision flag conflicts with the status.")
        if status in UNBILLABLE and (row.get("safeToPublish") or row.get("actionUrl")):
            raise ValueError("An unproven or diagnostic row has a publishable route.")
        if row.get("safeToPublish"):
            if status not in PUBLISHABLE or row["reviewRequired"] or not public_https(row.get("actionUrl")):
                raise ValueError("Unsafe publication flags.")
            if status == "LINKEDIN_EASY_APPLY":
                action = urlsplit(row["actionUrl"])
                action_host = (action.hostname or "").lower()
                action_id = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d{1,24})/?", action.path)
                if not (action_host == "linkedin.com" or action_host.endswith(".linkedin.com")) or not action_id or action_id.group(1) != job_id:
                    raise ValueError("Easy Apply action URL does not match the source job.")
            publishable += 1
        if status not in PUBLISHABLE and row.get("actionUrl"):
            raise ValueError("A held decision unexpectedly has an action URL.")
        accounting = row.get("requestAccounting")
        if accounting is not None:
            if not isinstance(accounting, dict) or "total" not in accounting:
                raise ValueError("Invalid row request accounting.")
            total = integer(accounting["total"], "Row request total")
            parts = sum(integer(value, "Row request component") for key, value in accounting.items() if key != "total")
            if total != parts:
                raise ValueError("Row request total does not match its components.")
            request_total += total
            accounting_rows += 1
        useful += int(expected_useful)
        diagnostic += int(status in {"INVALID_INPUT", "DUPLICATE_INPUT"})
        unresolved += int(status == "AMBIGUOUS")
        review_required += int(row["reviewRequired"])
    charged_counts = run.get("chargedEventCounts")
    if not isinstance(charged_counts, dict):
        raise ValueError("Missing charged event counts.")
    charged = charged_counts.get("verified-job", 0)
    billing = output.get("billing")
    if not isinstance(billing, dict) or type(billing.get("payPerEventActive")) is not bool:
        raise ValueError("payPerEventActive must be an actual boolean.")
    pay_per_event_active = billing["payPerEventActive"]
    expected_charges = useful if pay_per_event_active else 0
    if type(charged) is not int or charged != expected_charges:
        raise ValueError("Charged decisions do not match the billing mode and useful dataset decisions.")
    for key, expected in (("usefulClassifications", useful), ("safeToPublishRows", publishable), ("rows", len(rows) - diagnostic)):
        if integer(output.get(key), "OUTPUT." + key) != expected:
            raise ValueError("OUTPUT and dataset counts differ.")
    for key, expected in (("reviewRequiredRows", review_required), ("invalidRows", counts["INVALID_INPUT"]), ("duplicateRowsDropped", counts["DUPLICATE_INPUT"])):
        # This OUTPUT counter covers processed valid rows, excluding input diagnostics.
        if key == "reviewRequiredRows":
            expected -= sum(row["reviewRequired"] for row in rows if row["status"] in {"INVALID_INPUT", "DUPLICATE_INPUT"})
        if key in output and integer(output[key], "OUTPUT." + key) != expected:
            raise ValueError("OUTPUT and dataset decision counts differ.")
    if "statusCounts" in output:
        reported = output["statusCounts"]
        expected = {key: value for key, value in counts.items() if key not in {"INVALID_INPUT", "DUPLICATE_INPUT"} and value}
        if not isinstance(reported, dict) or any(type(value) is not int or value < 0 for value in reported.values()) or {key: value for key, value in reported.items() if value} != expected:
            raise ValueError("OUTPUT statusCounts do not match valid dataset decisions.")
    if not isinstance(billing, dict) or billing.get("unresolvedRowsCharged") is not False or billing.get("invalidOrDuplicateRowsCharged") is not False:
        raise ValueError("OUTPUT reports charges for unbillable rows.")
    report_event = billing.get("reportEvent")
    if report_event and billing.get("reportEventPriced") is False and integer(charged_counts.get(report_event, 0), "Report event count"):
        raise ValueError("Unpriced report has charged report events.")
    if "requests" in output and integer(output["requests"], "OUTPUT.requests") != request_total and accounting_rows == len(rows):
        raise ValueError("OUTPUT requests do not match row request accounting.")
    accounted = run.get("accountedChargedEventCounts")
    if accounted is not None:
        if not isinstance(accounted, dict) or integer(accounted.get("verified-job", 0), "Accounted verified-job events") > charged:
            raise ValueError("Accounted events exceed observed charged events.")
    usage_value = run.get("usageTotalUsd")
    try:
        cost = None if usage_value is None else Decimal(str(usage_value))
        price = Decimal(str(scenario_event_price))
    except InvalidOperation:
        raise ValueError("Cost and scenario event price must be numeric.") from None
    if not price.is_finite() or price < 0:
        raise ValueError("The scenario event price must be finite and nonnegative.")
    if cost is not None and (not cost.is_finite() or cost < 0):
        raise ValueError("Invalid usage snapshot.")
    if not math.isfinite(float(price)) or cost is not None and not math.isfinite(float(cost)):
        raise ValueError("Cost or price is outside the finite JSON numeric range.")
    event_value = price * charged
    contribution = None if cost is None else Decimal("0.8") * event_value - cost
    def number(value):
        converted = None if value is None else float(value)
        if converted is not None and not math.isfinite(converted):
            raise ValueError("Scenario result is outside the finite JSON numeric range.")
        return converted
    return {
        "runId": run.get("id"), "buildNumber": run.get("buildNumber"),
        "observedFinishedAt": run.get("finishedAt"),
        "requestedRows": requested, "deliveredRows": len(rows),
        "inputRowsWithoutDecision": requested - len(indexes),
        "inputCoverageRate": len(indexes) / requested if requested else None,
        "requestedBatchFullyDelivered": len(indexes) == requested,
        "exportedDatasetMatchesOutput": True,
        "datasetRows": len(rows), "validUniqueRows": len(rows) - diagnostic,
        "statusCounts": dict(sorted(counts.items())), "usefulDecisions": useful,
        "publishableDecisions": publishable, "diagnosticRows": diagnostic,
        "usefulRateOfDatasetRows": useful / len(rows) if rows else None,
        "usefulRateOfValidUniqueRows": useful / (len(rows) - diagnostic) if len(rows) > diagnostic else None,
        "publishableRateOfDatasetRows": publishable / len(rows) if rows else None,
        "unresolvedRows": unresolved, "chargedUsefulEvents": charged,
        "payPerEventActive": pay_per_event_active,
        "accountedChargedEventCounts": run.get("accountedChargedEventCounts"),
        "requests": output.get("requests"),
        "rowRequestAccountingVerified": accounting_rows == len(rows),
        "ownerUsageSnapshotUsd": number(cost),
        "ownerUsagePerUsefulDecisionUsd": number(cost / useful) if cost is not None and useful else None,
        "scenarioEventPriceUsd": number(price),
        "scenarioGrossEventValueUsd": number(event_value),
        "scenarioPaidContributionUsingOwnerCostUsd": number(contribution),
        "realizedCustomerRevenueUsd": None,
        "limitations": [
            "Owner run usage is an observed, potentially delayed platform-cost snapshot.",
            "The 80 percent share and supplied event price form a paid-use scenario, not realized revenue.",
            "Owner costs and network behavior are not proof of a paid customer's tier-specific costs.",
            "A useful EXPIRED or mismatch decision can be chargeable without being publishable.",
            "OUTPUT.rows can exclude diagnostic dataset records; datasetRows is counted independently.",
            "A complete exported dataset can still cover only a capped subset of requested inputs; input coverage is reported separately.",
            "Scenario event value uses observed verified-job event counts; inactive PPE with zero charges has no event value in this scenario.",
            "Local IDs, clocks and counts are checked; same-count exports are not cryptographically bound to saved INPUT.",
            "URL syntax checks do not fetch destinations or prove current availability or public DNS resolution.",
            "The caller must establish owner identity; this helper does not infer it from financial counters."
        ]
    }

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", required=True)
    p.add_argument("--rows", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--scenario-event-price", default="0.0045", help="Explicit hypothetical price; check live pricing first.")
    p.add_argument("--save")
    args = p.parse_args()
    try:
        result = analyze(*(json.loads(Path(x).read_text()) for x in [args.run, args.rows, args.output]), args.scenario_event_price)
    except (OSError, ValueError, TypeError) as e:
        p.exit(1, str(e) + "\n")
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.save:
        Path(args.save).write_text(text)
    else:
        print(text, end="")

if __name__ == "__main__":
    main()
