"""BPI Challenge 2013 (incidents): real control flow with a semi-synthetic policy layer.

The log carries no compliance information, so the policy layer is synthetic and stated as such:
activities are the status and sub-status pairs (concept:name + lifecycle:transition); each gets
a transactional and a consumption property drawn uniformly (seeded) and the corrected policy
templates; every real event is enriched with the lifecycle of its activity starting at the real
timestamp; policy violations of all seven types are injected into 10% of the cases (ground
truth). Control flow: the cases are split in halves (seeded), a model is discovered with the
Inductive Miner (infrequent, noise threshold 0.2) on one half, and the other half is checked,
so the model is not derived from the behaviour it checks. Only checking is timed.
Outputs: eval/results/bpic2013_cases.csv, eval/results/bpic2013_summary.json
"""
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd
import pm4py

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import checker, configs, core, oracle, parallel  # noqa: E402
from pacc_eval.paths import DATA, RESULTS  # noqa: E402

SOURCE = DATA / "bpic2013" / "BPI_Challenge_2013_incidents.xes.gz"
TYPES = core.VIOLATION_TYPES + ["premature_completion"]
SEED = 2013


def configuration(activities, rng):
    entries = {}
    for a in activities:
        entries[a] = configs.correct_activity({
            "activity": a, "type": rng.choice(sorted(configs.ACTIVITY_TEMPLATES)), "currentState": "NotActivated",
            "resource": f"Resource {a}", "resourceType": rng.choice(sorted(configs.RESOURCE_TEMPLATES)),
            "resource_current_state": "NotConsumed"})
    return entries


def main():
    df = pm4py.convert_to_dataframe(pm4py.read_xes(str(SOURCE)))
    df["activity"] = df["concept:name"] + "+" + df["lifecycle:transition"]
    df["timestamp"] = pd.to_datetime(df["time:timestamp"], utc=True)
    rng = random.Random(SEED)
    activities = sorted(df.activity.unique())
    config = configuration(activities, rng)
    traces = {c: list(zip(g.activity, g.timestamp.dt.to_pydatetime()))
              for c, g in df.groupby("case:concept:name", sort=False)}
    names = list(traces)
    rng.shuffle(names)
    train, test = names[: len(names) // 2], names[len(names) // 2:]

    t0 = time.perf_counter()
    tree = pm4py.discover_process_tree_inductive(
        pm4py.format_dataframe(df[df["case:concept:name"].isin(set(train))], case_id="case:concept:name",
                               activity_key="activity", timestamp_key="timestamp"), noise_threshold=0.2)
    model = pm4py.convert_to_petri_net(tree)
    discovery = time.perf_counter() - t0

    act_types, res_types = core.load_types()
    log = core.enrich([traces[c] for c in test], config, act_types, res_types, rng)
    labels = core.inject_policy_violations(log, 0.1, rng, TYPES, [1] * len(TYPES), config)
    injected = {x["trace"]: x["type"] for x in labels}

    t0 = time.perf_counter()
    v, reasons = core.policy_pathway(log, config)
    policy_seconds = time.perf_counter() - t0
    t0 = time.perf_counter()
    projections = [checker.instance_projection(events, config) for events in log]
    aligned = parallel.align_variants(projections, model, workers=6, trace_budget=30, total_budget=1200)
    f = [r["fitness"] if r else None for r in aligned]
    alignment_seconds = time.perf_counter() - t0

    rows = []
    for t, case in enumerate(test):
        rows.append({"case": case, "events": len(traces[case]), "lifecycle_events": len(log[t]), "f": f[t], "v": v[t],
                     "pacc": 0.7 * f[t] + 0.3 * v[t] if f[t] is not None else None, "injected": injected.get(t, ""),
                     "admissible": int(oracle.admissible(log[t], config, act_types, res_types))})
    cases = pd.DataFrame(rows)
    cases.to_csv(RESULTS / "bpic2013_cases.csv", index=False)
    pol = cases.injected != ""
    shift = cases.injected == "timestamp_shift"
    effective = pol & ((cases.admissible == 0) | (shift & (cases.v < 1)))
    summary = {
        "cases": len(traces), "events": len(df), "activities": len(activities), "train_cases": len(train),
        "test_cases": len(test), "test_variants": len({tuple(a for a, _ in traces[c]) for c in test}),
        "lifecycle_events_checked": int(cases.lifecycle_events.sum()), "discovery_seconds": round(discovery, 2),
        "policy_seconds": round(policy_seconds, 2), "alignment_seconds": round(alignment_seconds, 2),
        "injected": int(pol.sum()), "effective": int(effective.sum()),
        "detected_effective": int((effective & (cases.v < 1)).sum()),
        "false_alarms": int((~pol & (cases.v < 1)).sum()),
        "aligned_case_share": round(cases.f.notna().mean(), 4), "alignment_workers": 6,
        "held_out_fitness_mean": round(cases.f.mean(), 4),
        "held_out_fitness_perfect": round((cases.f[cases.f.notna()] == 1).mean(), 4),
        "alignment_only_flags_injected": int((pol & (cases.f < 1)).sum()),
        "mean_pacc": round(cases.pacc.mean(), 4)}
    (RESULTS / "bpic2013_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
