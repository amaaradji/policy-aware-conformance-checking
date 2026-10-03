"""Use case (loan application, model 21): hand-built traces checked with the formal model.

T1 carries the three designed policy violations and no control-flow deviation:
  1. Credit Check (retriable, at most 3 retries) fails and is retried a fourth time;
  2. the Credit Bureau API (non-shareable limited, 5-minute query) is held 9 minutes;
  3. Risk Assessment is completed while its Risk Analyst is still engaged (the analyst is
     unlocked and released after completion).
T2 skips Risk Assessment (control-flow deviation only). T3 is compliant.
Outputs: eval/results/usecase_report.json, eval/results/usecase_events.csv, and the joined
event/policy log models/usecase/usecase_log.xes.
"""
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pm4py
from pm4py.objects.log.obj import Event, EventLog, Trace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import checker, configs, core  # noqa: E402
from pacc_eval.paths import MODELS, RESULTS  # noqa: E402

START = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
NA, A, D, F = "NotActivated", "Activated", "Done", "Failed"
NC, L, C, U, RD, W = "NotConsumed", "Locked", "Consumed", "Unlocked", "Done", "Withdrawn"


def minutes(pairs):
    """Steps (activity state, resource state, minutes since the previous step), one minute apart."""
    return [(a, r, 1) for a, r in pairs]


RECEIVE = minutes([(NA, NC), (A, NC), (A, C), (A, W), (D, W)])
NON_SHAREABLE = minutes([(NA, NC), (A, NC), (A, L), (A, C), (A, U), (A, RD), (A, W), (D, W)])
APPROVE = minutes([(NA, NC), (A, NC), (A, C), (A, RD), (A, W), (D, W)])
CREDIT_T1 = (minutes([(NA, NC), (A, NC)] + [(F, NC), (A, NC)] * 4 + [(A, L), (A, C)])
             + [(A, U, 9), (A, RD, 1), (A, W, 1), (D, W, 1)])
RISK_T1 = minutes([(NA, NC), (A, NC), (A, L), (A, C)]) + [(D, C, 7), (D, U, 1), (D, RD, 1), (D, W, 1)]


def instance(name, steps, start, info):
    events, t = [], start
    for i, (a_state, r_state, gap) in enumerate(steps):
        t += timedelta(minutes=gap if i else 0)
        e = {"concept:name": name, "time:timestamp": t, "concept:type": info["type"],
             "concept:currentState": a_state, "concept:resource": info["resource"],
             "concept:resourceType": info["resourceType"], "concept:resource_current_state": r_state}
        for key in ("activity_policy", "resource_policy", "resource_policy2", "binding_policy"):
            if key in info:
                e[key] = json.dumps(info[key], sort_keys=True)
        events.append(e)
    return events, t


def build(config):
    receive, credit, risk, approve = config
    plans = {
        "T1": [(receive, RECEIVE), (credit, CREDIT_T1), (risk, RISK_T1), (approve, APPROVE)],
        "T2": [(receive, RECEIVE), (credit, NON_SHAREABLE), (approve, APPROVE)],
        "T3": [(receive, RECEIVE), (credit, NON_SHAREABLE), (risk, NON_SHAREABLE), (approve, APPROVE)],
    }
    log = {}
    for day, (case, plan) in enumerate(plans.items()):
        events, t = [], START + timedelta(days=day)
        for name, steps in plan:
            part, t = instance(name, steps, t, config[name])
            events += part
            t += timedelta(minutes=5)
        log[case] = events
    return log


def export_xes(log, path):
    xes = EventLog()
    for case, events in log.items():
        trace = Trace(attributes={"concept:name": case})
        for e in events:
            trace.append(Event(e))
        xes.append(trace)
    pm4py.write_xes(xes, str(path))


def main():
    config = {name: configs.instantiate(entry)
              for name, entry in core.load_activity_info(MODELS / "usecase" / "activity_info_21.json").items()}
    model = core.load_model(MODELS / "usecase" / "processModel_21.bpmn")
    log = build(config)
    cases, traces = list(log), list(log.values())
    f, v, pacc, reasons = core.run_formal(traces, config, model)
    v_nosync, _ = core.policy_pathway(traces, config, checker.Semantics(sync=False))
    v_reduced, _ = core.policy_pathway(traces, config, checker.PUBLISHED)
    report = {"traces": [{"case": c, "events": len(t), "evaluations": 3 * len(t), "f": f[i], "v": v[i],
                          "pacc": pacc[i], "v_nosync": v_nosync[i], "v_reduced": v_reduced[i]}
                         for i, (c, t) in enumerate(log.items())],
              "failed_evaluations": [dict(r, case=cases[r.pop("trace")]) for r in reasons],
              "log_pacc": sum(pacc) / len(pacc)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / "usecase_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    with open(RESULTS / "usecase_events.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["case", "event", "activity", "timestamp", "activity_state", "resource",
                         "resource_state", "a", "r", "b", "reason"])
        for case, events in log.items():
            flags, why = checker.check_trace(events, config)
            by_event = {x["event"]: "; ".join(filter(None, (x["a"], x["r"], x["b"]))) for x in why}
            for k, (e, fl) in enumerate(zip(events, flags)):
                writer.writerow([case, k, e["concept:name"], e["time:timestamp"].isoformat(),
                                 e["concept:currentState"], e["concept:resource"],
                                 e["concept:resource_current_state"], *fl, by_event.get(k, "")])
    export_xes(log, MODELS / "usecase" / "usecase_log.xes")
    for row in report["traces"]:
        print({k: (round(x, 4) if isinstance(x, float) else x) for k, x in row.items()})
    for r in report["failed_evaluations"]:
        print(r)
    print("log PACC", round(report["log_pacc"], 4))


if __name__ == "__main__":
    main()
