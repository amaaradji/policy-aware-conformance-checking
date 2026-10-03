"""BPI Challenge 2019: documented compliance rules on a real procure-to-pay log.

Policies. The matching rules documented with the log (Dees and van Dongen, 2019) are encoded as
ODRL activity policies, bound per item category (the binder's choice at instantiation):
  3-way match, invoice after GR:  Record Invoice Receipt only after Record Goods Receipt;
  3-way match, invoice before GR: Clear Invoice only after Record Goods Receipt (invoices are
                                  blocked until the goods are received);
  2-way match:                    no goods-receipt rule;
  Consignment:                    Record Invoice Receipt prohibited (no invoices expected).
Every other activity gets the pivot policy, the shareable-unlimited resource and binding
policies, and a single bound resource. Each real event is normalized into the minimal compliant
lifecycle of its activity (four events ending at the real timestamp), so that v < 1 only when a
documented rule is broken. The rule violations are recomputed independently with pandas.

Control flow. Per item category, the cases are split in halves (seeded); a model is discovered
with the Inductive Miner (infrequent, noise threshold 0.2) on one half and the other half is
aligned against it (no circularity between the model and the checked behaviour). Variants are
aligned most frequent first by 6 worker processes, within 30 s per variant and 20 minutes per
worker; the share of test cases whose variant was aligned is reported.

Timing covers checking only (the normalization is done per batch before the clock starts).
Outputs: eval/results/bpic2019_cases.csv.gz, eval/results/bpic2019_summary.json
"""
import copy
import gzip
import json
import random
import sys
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pm4py

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import checker, configs, core, parallel  # noqa: E402
from pacc_eval.paths import DATA, RESULTS  # noqa: E402

SOURCE = DATA / "bpic2019" / "bpic2019_events.csv.gz"
GR, IR, CLEAR = "Record Goods Receipt", "Record Invoice Receipt", "Clear Invoice"
ACTIVATION = "http://example.com/activity/Ai/states/NotActivated"
RULES = {"3-way match, invoice after GR": {IR: ("after", GR)},
         "3-way match, invoice before GR": {CLEAR: ("after", GR)},
         "2-way match": {},
         "Consignment": {IR: ("prohibited", None)}}
LIFECYCLE = [("NotActivated", "NotConsumed"), ("Activated", "Consumed"), ("Activated", "Withdrawn"),
             ("Done", "Withdrawn")]
BATCH = 10000


def activity_policy(rule):
    policy = configs.template("activity_policies", "pivot.json")
    if rule is None:
        return policy
    kind, other = rule
    if kind == "prohibited":
        policy["prohibition"] = [{"target": {"@id": ACTIVATION},
                                  "output": {"@id": "http://example.com/activity/Ai/states/Activated"}}]
        return policy
    for rule_node in policy.get("permission", []):
        if rule_node["target"]["@id"].rstrip("/").endswith("NotActivated"):
            rule_node.setdefault("constraint", []).append(
                {"leftOperand": {"@id": "odrl:event"}, "operator": {"@id": "odrl:gt"},
                 "rightOperand": {"@value": other, "@type": "ex:activity"}})
    return policy


def configuration(activities, category):
    resource = configs.template("resource_policies", "shareableUL.json")
    binding = configs.template("binding_policies", "activity_shareableUnlimited.json")
    config = {}
    for a in activities:
        config[a] = {"activity": a, "type": "Pivot", "currentState": "NotActivated", "resource": "ERP",
                     "resourceType": "ShareableUnlimited", "resource_current_state": "NotConsumed",
                     "activity_policy": activity_policy(RULES[category].get(a)),
                     "resource_policy": copy.deepcopy(resource), "binding_policy": copy.deepcopy(binding)}
    return config


def normalize(rows, serialized):
    """Events of one case: each real event becomes its activity's minimal lifecycle."""
    events = []
    for activity, ts in rows:
        for k, (a_state, r_state) in enumerate(LIFECYCLE):
            events.append({"concept:name": activity, "time:timestamp": ts - timedelta(microseconds=3 - k),
                           "concept:currentState": a_state, "concept:resource": "ERP",
                           "concept:resource_current_state": r_state, **serialized[activity]})
    return events


def independent_violations(log):
    """Rule violations recomputed directly on the event table (no policies, no checker)."""
    log = log.copy()
    is_gr = (log.activity == GR).astype(int)
    log["gr_before"] = (is_gr.groupby(log.case, sort=False).cumsum() - is_gr) > 0  # a receipt strictly earlier
    bad = pd.Series(False, index=log.index)
    for category, rules in RULES.items():
        in_cat = log.category == category
        for activity, (kind, _) in rules.items():
            hit = in_cat & (log.activity == activity)
            bad |= hit if kind == "prohibited" else hit & ~log.gr_before
    return set(log.loc[bad, "case"])


def main():
    log = pd.read_csv(SOURCE, dtype=str)
    log["timestamp"] = pd.to_datetime(log["timestamp"], utc=True, format="ISO8601")
    activities = sorted(log.activity.unique())
    summary = {"cases": int(log.case.nunique()), "events": len(log), "activities": len(activities), "categories": {}}
    rows = []
    rng = random.Random(2019)
    for category, part in log.groupby("category", sort=True):
        config = configuration(activities, category)
        serialized = {a: {k: json.dumps(info[k], sort_keys=True) for k in ("activity_policy", "resource_policy",
                                                                            "binding_policy")}
                      for a, info in config.items()}
        grouped = {}
        for case, activity, ts in zip(part.case, part.activity, part.timestamp.dt.to_pydatetime()):
            grouped.setdefault(case, []).append((activity, ts))
        cases = list(grouped.items())
        checked = seconds = 0
        verdicts = {}
        for start in range(0, len(cases), BATCH):
            batch = cases[start:start + BATCH]
            traces = [normalize(rows, serialized) for _, rows in batch]
            t0 = time.perf_counter()
            v, reasons = core.policy_pathway(traces, config)
            seconds += time.perf_counter() - t0
            checked += sum(map(len, traces))
            failed = {}
            for r in reasons:
                failed.setdefault(r["trace"], []).append(r["a"] or r["r"] or r["b"])
            for i, (case, case_rows) in enumerate(batch):
                verdicts[case] = (v[i], len(case_rows), "; ".join(sorted(set(failed.get(i, [])))))
        names = [c for c, _ in cases]
        rng.shuffle(names)
        train, test = set(names[: len(names) // 2]), names[len(names) // 2:]
        sequences = {c: [a for a, _ in case_rows] for c, case_rows in cases}
        t0 = time.perf_counter()
        tree = pm4py.discover_process_tree_inductive(
            pm4py.format_dataframe(part[part.case.isin(train)], case_id="case", activity_key="activity",
                                   timestamp_key="timestamp"), noise_threshold=0.2)
        model = pm4py.convert_to_petri_net(tree)
        discovery = time.perf_counter() - t0
        t0 = time.perf_counter()
        aligned = parallel.align_variants([sequences[c] for c in test], model, workers=6, trace_budget=30,
                                          total_budget=1200)
        alignment = time.perf_counter() - t0
        fitness = {c: (r["fitness"] if r else None) for c, r in zip(test, aligned)}
        for case, (v, n, why) in verdicts.items():
            f = fitness.get(case)
            rows.append({"case": case, "category": category, "split": "test" if case in fitness else "train",
                         "events": n, "v": v, "f": f, "pacc": (0.7 * f + 0.3 * v) if f is not None else None,
                         "violation": int(v < 1), "reasons": why})
        summary["categories"][category] = {
            "cases": len(cases), "events": int(len(part)), "lifecycle_events_checked": checked,
            "policy_seconds": round(seconds, 2), "policy_events_per_second": round(checked / seconds),
            "cases_violating": sum(v < 1 for v, _, _ in verdicts.values()),
            "discovery_seconds": round(discovery, 2), "test_cases": len(test),
            "test_variants": len({tuple(sequences[c]) for c in test}), "alignment_seconds": round(alignment, 2),
            "alignment_workers": 6,
            "unaligned": len({tuple(sequences[c]) for c, r in zip(test, aligned) if r is None}),
            "aligned_case_share": round(sum(r is not None for r in aligned) / len(aligned), 4)}
        print(category, summary["categories"][category], flush=True)
    cases = pd.DataFrame(rows)
    flagged = set(cases.loc[cases.violation == 1, "case"])
    independent = independent_violations(log)
    summary["agreement"] = {"checker": len(flagged), "independent": len(independent),
                            "both": len(flagged & independent), "only_checker": len(flagged - independent),
                            "only_independent": len(independent - flagged)}
    test = cases[cases.split == "test"]
    summary["test"] = test.groupby("category")[["f", "v", "pacc"]].mean().round(4).to_dict(orient="index")
    with gzip.open(RESULTS / "bpic2019_cases.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        cases.to_csv(fh, index=False)
    (RESULTS / "bpic2019_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["agreement"]), json.dumps(summary["test"]))


if __name__ == "__main__":
    main()
