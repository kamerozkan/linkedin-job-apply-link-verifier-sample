#!/usr/bin/env python3
"""Audit exported verifier output without inferring realized customer revenue."""
import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

USEFUL = {"ACTIVE", "AUTHORIZED_APPLY_ROUTE", "LINKEDIN_EASY_APPLY", "EXPIRED", "SOURCE_MISMATCH", "STATUS_CONFLICT"}
UNBILLABLE = {"AMBIGUOUS", "INVALID_INPUT", "DUPLICATE_INPUT"}
PUBLISHABLE = {"ACTIVE", "AUTHORIZED_APPLY_ROUTE", "LINKEDIN_EASY_APPLY"}

def analyze(run, rows, output, scenario_event_price):
    if run.get("status") != "SUCCEEDED":
        raise ValueError("The cloud run did not finish successfully.")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Expected exported dataset objects.")
    counts = Counter()
    useful = publishable = diagnostic = unresolved = 0
    for row in rows:
        status = row.get("status")
        if status not in USEFUL | UNBILLABLE:
            raise ValueError("Unknown decision status.")
        counts[status] += 1
        expected_useful = status in USEFUL
        if row.get("usefulClassification") is not expected_useful:
            raise ValueError("The useful-decision flag conflicts with the status.")
        if status in UNBILLABLE and (row.get("safeToPublish") or row.get("actionUrl")):
            raise ValueError("An unproven or diagnostic row has a publishable route.")
        if row.get("safeToPublish"):
            if status not in PUBLISHABLE or not row.get("actionUrl"):
                raise ValueError("Unsafe publication flags.")
            publishable += 1
        useful += int(expected_useful)
        diagnostic += int(status in {"INVALID_INPUT", "DUPLICATE_INPUT"})
        unresolved += int(status == "AMBIGUOUS")
    charged = (run.get("chargedEventCounts") or {}).get("verified-job", 0)
    if not isinstance(charged, int) or isinstance(charged, bool) or charged != useful:
        raise ValueError("Charged decisions do not match useful dataset decisions.")
    if output.get("usefulClassifications") != useful or output.get("safeToPublishRows") != publishable:
        raise ValueError("OUTPUT and dataset counts differ.")
    billing = output.get("billing") or {}
    if billing.get("unresolvedRowsCharged") or billing.get("invalidOrDuplicateRowsCharged"):
        raise ValueError("OUTPUT reports charges for unbillable rows.")
    usage_value = run.get("usageTotalUsd")
    cost = None if usage_value is None else Decimal(str(usage_value))
    price = Decimal(str(scenario_event_price))
    if not price.is_finite() or price < 0:
        raise ValueError("The scenario event price must be finite and nonnegative.")
    if cost is not None and (not cost.is_finite() or cost < 0):
        raise ValueError("Invalid usage snapshot.")
    event_value = price * charged
    contribution = None if cost is None else Decimal("0.8") * event_value - cost
    def number(value):
        return None if value is None else float(value)
    return {
        "runId": run.get("id"), "buildNumber": run.get("buildNumber"),
        "observedFinishedAt": run.get("finishedAt"),
        "datasetRows": len(rows), "validUniqueRows": len(rows) - diagnostic,
        "statusCounts": dict(sorted(counts.items())), "usefulDecisions": useful,
        "publishableDecisions": publishable, "diagnosticRows": diagnostic,
        "unresolvedRows": unresolved, "chargedUsefulEvents": charged,
        "accountedChargedEventCounts": run.get("accountedChargedEventCounts"),
        "requests": output.get("requests"),
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
            "OUTPUT.rows can exclude diagnostic dataset records; datasetRows is counted independently."
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

