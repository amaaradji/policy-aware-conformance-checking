"""Seeded data generation and the two PACC pathways, in memory.

Generation (simulate, enrich, inject_*) is kept separate from checking (check_policies,
aggregate, fitness, pacc_scores) so that runtime measurements cover checking only.
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pm4py.objects.bpmn.importer import importer as bpmn_importer
from pm4py.objects.conversion.bpmn import converter as bpmn_converter
from pm4py.objects.log.obj import Event, EventLog, Trace
from pm4py.objects.petri_net import semantics
from pm4py.algo.conformance.alignments.petri_net import algorithm as alignments

from . import checker
from .paths import CONFIG, SRC

sys.path.insert(0, str(SRC))
from policy_check import IntegratedComplianceChecker  # noqa: E402  (published checker, unchanged)

# Same constants as src/enrich_event.py and src/inject_deviations.py.
ACTIVITY_OPTIONAL_STOPS = {"Pivot": set(), "Compensatable": {"Done"}, "Retriable": {"Failed"}}
INVALID_ACTIVITY_STATES = ["InvalidState", "Unknown", "Skipped", "Pending"]
INVALID_RESOURCE_STATES = ["Corrupted", "Expired", "Blocked", "Overused"]
VALID_RESOURCES = ["Accounting System", "Finance Clerk", "HR System", "Approval Board"]
VIOLATION_TYPES = ["activity_state_violation", "resource_state_violation", "wrong_resource",
                   "timestamp_shift", "missing_policy", "state_sequence_skip"]
VIOLATION_WEIGHTS = [0.25, 0.20, 0.20, 0.15, 0.10, 0.10]
CONTROL_FLOW_TYPES = ["swap", "skip", "insert"]
FOREIGN_ACTIVITY = "Unexpected Activity"
PLAYOUT_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------- loading

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_model(bpmn_path):
    return bpmn_converter.apply(bpmn_importer.apply(str(bpmn_path)))


def load_activity_info(path):
    return {a["activity"]: a for a in load_json(path)["activities"]}


def load_types():
    act_types = load_json(CONFIG / "activity_types.json")
    res_types = {k.lower(): v for k, v in load_json(CONFIG / "resource_types.json").items()}
    return act_types, res_types


# ---------------------------------------------------------------- generation

def simulate(model, n_traces, rng, max_length=500):
    """Random playout of the Petri net. Enabled transitions are sorted by name before the
    seeded choice, so the result does not depend on set iteration order."""
    net, im, fm = model
    traces, clock = [], PLAYOUT_START
    for _ in range(n_traces):
        marking, labels = im, []
        while marking != fm and len(labels) < max_length:
            enabled = sorted(semantics.enabled_transitions(net, marking), key=lambda t: t.name)
            if not enabled:
                break
            transition = rng.choice(enabled)
            marking = semantics.execute(transition, net, marking)
            if transition.label is not None:
                labels.append((transition.label, clock))
                clock += timedelta(seconds=1)
        traces.append(labels)
    return traces


def _probabilistic_path(state, transitions, rng, optional_stops=frozenset(), max_steps=30, max_visits=3):
    """Same random walk as src/enrich_event.get_probabilistic_path, with an explicit RNG."""
    stop = "__STOP__"
    optional_lower = {s.lower() for s in optional_stops}
    path, visits = [state], {state: 1}
    for _ in range(max_steps):
        options = transitions.get(state)
        if not options:
            break
        options = list(options) + ([stop] if state.lower() in optional_lower else [])
        options = [s for s in options if s == stop or visits.get(s, 0) < max_visits]
        if not options:
            break
        chosen = options[rng.randrange(len(options))]
        if chosen == stop:
            break
        path.append(chosen)
        visits[chosen] = visits.get(chosen, 0) + 1
        state = chosen
    return path


def enrich(traces, act_info, act_types, res_types, rng):
    """Same enrichment as src/enrich_event.enrich: each activity occurrence becomes the
    sequence of (activity state, resource state) steps of its lifecycle."""
    serialized = {name: {key: json.dumps(info[key], sort_keys=True) for key in
                         ("activity_policy", "resource_policy", "resource_policy2", "binding_policy") if key in info}
                  for name, info in act_info.items()}
    enriched = []
    for labels in traces:
        events = []
        for name, base_time in labels:
            info = act_info.get(name)
            if info is None:
                events.append({"concept:name": name, "time:timestamp": base_time})
                continue
            policies = serialized[name]
            a_path = _probabilistic_path(info["currentState"],
                                         act_types.get(info["type"], {}).get("transitions", {}),
                                         rng, ACTIVITY_OPTIONAL_STOPS.get(info["type"], set()))
            r_path = _probabilistic_path(info["resource_current_state"],
                                         res_types.get(info["resourceType"].lower(), {}).get("transitions", {}),
                                         rng)
            act_idx = a_path.index("Activated") if "Activated" in a_path else 0
            steps = ([(a_path[i], r_path[0]) for i in range(act_idx)]
                     + [(a_path[act_idx], r) for r in r_path]
                     + [(a_path[i], r_path[-1]) for i in range(act_idx + 1, len(a_path))])
            for i, (a_state, r_state) in enumerate(steps):
                event = {
                    "concept:name": name,
                    "time:timestamp": base_time + timedelta(seconds=i),
                    "concept:type": info["type"],
                    "concept:currentState": a_state,
                    "concept:resource": info["resource"],
                    "concept:resourceType": info["resourceType"],
                    "concept:resource_current_state": r_state,
                    # The published checker merges a second resource policy (second consumption
                    # cycle of limited resources); the published enrichment never wrote it.
                    **policies,
                }
                events.append(event)
        enriched.append(events)
    return enriched


def _apply_policy_violation(event, vtype, rng):
    """One mutation of src/inject_deviations.py. Returns False when it does not apply."""
    if vtype == "activity_state_violation" and "concept:currentState" in event:
        event["concept:currentState"] = (rng.choice(INVALID_ACTIVITY_STATES) if rng.random() < 0.6
                                         else rng.choice(["Done", "Compensated", "Failed"]))
    elif vtype == "resource_state_violation" and "concept:resource_current_state" in event:
        event["concept:resource_current_state"] = (rng.choice(INVALID_RESOURCE_STATES) if rng.random() < 0.7
                                                   else rng.choice(["Withdrawn", "Locked"]))
    elif vtype == "wrong_resource" and "concept:resource" in event:
        event["concept:resource"] = rng.choice([r for r in VALID_RESOURCES if r != event["concept:resource"]])
    elif vtype == "timestamp_shift":
        delta = timedelta(seconds=rng.randint(30, 3600))
        event["time:timestamp"] += delta if rng.random() < 0.5 else -delta
    elif vtype == "missing_policy":
        key = rng.choice(["activity_policy", "resource_policy", "binding_policy"])
        if key not in event:
            return False
        if rng.random() < 0.6:
            del event[key]
        else:
            event[key] = "INVALID_POLICY_STRING"
    elif vtype == "state_sequence_skip" and "concept:currentState" in event:
        event["concept:currentState"] = rng.choice(["Done", "Compensated", "Failed"])
    else:
        return False
    return True


def _in_episode(states, k, rule):
    """Whether the resource transition at position k enters or belongs to an open consumption
    episode of a binding synchronization rule."""
    opened = [j for j in range(k + 1) if states[j] == rule.source]
    return bool(opened) and rule.target not in states[opened[-1] + 1:k]


def premature_completion_candidates(events, act_info):
    """Events from which the completion of their activity instance can be moved earlier, so
    that resource transitions of a consumption episode follow the completion."""
    options = []
    starts = checker.instance_starts(events, act_info)
    for s, end in zip(starts, starts[1:] + [len(events)]):
        info = act_info.get(events[s]["concept:name"])
        policy = checker.compile_policy(info["binding_policy"]) if info else None
        if policy is None:
            continue
        acts = [checker._norm(e.get("concept:currentState")) for e in events[s:end]]
        res = [checker._norm(e.get("concept:resource_current_state")) for e in events[s:end]]
        for k in range(1, end - s):
            for rule in policy.sync:
                if acts[k - 1] == acts[k] == rule.activity_state and res[k] != res[k - 1] \
                        and _in_episode(res, k, rule):
                    run_end = k
                    while run_end + 1 < end - s and acts[run_end + 1] == rule.activity_state:
                        run_end += 1
                    if run_end + 1 < end - s:
                        options.append((s + k, s + run_end + 1))
                    break
    return options


def _premature_completion(events, start, successor):
    """The activity leaves its assignee state at `start` (taking the state it originally reached
    at `successor`) while its resource is still being consumed. Both lifecycles stay valid."""
    state = events[successor]["concept:currentState"]
    for i in range(start, successor):
        events[i]["concept:currentState"] = state


def inject_policy_violations(log, rate, rng, types=VIOLATION_TYPES, weights=VIOLATION_WEIGHTS, act_info=None):
    """Each trace receives one policy violation with probability `rate` (the paper's
    definition of the noise level). Default types and weights follow
    src/inject_deviations.py; "premature_completion" (needs act_info) breaks only the
    activity/resource synchronization. Returns ground-truth labels."""
    labels = []
    for t, events in enumerate(log):
        candidates = [i for i, e in enumerate(events) if "activity_policy" in e]
        if not candidates or rng.random() >= rate:
            continue
        for _ in range(20):
            i = rng.choice(candidates)
            vtype = rng.choices(types, weights=weights)[0]
            if vtype == "premature_completion":
                options = premature_completion_candidates(events, act_info)
                if not options:
                    continue
                i, successor = rng.choice(options)
                _premature_completion(events, i, successor)
            elif not _apply_policy_violation(events[i], vtype, rng):
                continue
            labels.append({"trace": t, "event": i, "activity": events[i]["concept:name"], "type": vtype})
            break
    return labels


def _instances(events, act_info):
    """Split a trace into its activity instances (lists of events)."""
    starts = checker.instance_starts(events, act_info)
    return [events[s:e] for s, e in zip(starts, starts[1:] + [len(events)])]


def inject_control_flow_deviations(log, rate, rng, act_info):
    """Each trace receives one control-flow deviation with probability `rate`: two adjacent
    activity instances swapped, one skipped, or one unexpected activity inserted. Inserted
    events carry no policy attributes, so they cannot trigger policy flags."""
    labels = []
    for t, events in enumerate(log):
        if rng.random() >= rate:
            continue
        blocks = _instances(events, act_info)
        options = [k for k in CONTROL_FLOW_TYPES if k != "swap" or len(blocks) > 1]
        kind = rng.choice(options)
        if kind == "swap":
            i = rng.randrange(len(blocks) - 1)
            activity = f"{blocks[i][0]['concept:name']} <-> {blocks[i + 1][0]['concept:name']}"
            blocks[i], blocks[i + 1] = blocks[i + 1], blocks[i]
        elif kind == "skip":
            i = rng.randrange(len(blocks))
            activity = blocks.pop(i)[0]["concept:name"]
        else:
            i = rng.randrange(len(blocks) + 1)
            anchor = blocks[min(i, len(blocks) - 1)][0]["time:timestamp"]
            blocks.insert(i, [{"concept:name": FOREIGN_ACTIVITY, "time:timestamp": anchor}])
            activity = FOREIGN_ACTIVITY
        log[t] = [e for block in blocks for e in block]
        labels.append({"trace": t, "type": kind, "activity": activity})
    return labels


# ---------------------------------------------------------------- checking

def check_policies(log, act_info, checker=None, per_instance=False):
    """Policy-compliance pathway: same per-event loop as src/policy_check.py
    (perform_compliance_check), using its IntegratedComplianceChecker. Writes a_comp, r_comp,
    b_comp on every event and returns the failure reasons.

    The published loop keeps one lifecycle history per activity name, so a repeated activity
    (loop) restarting at NotActivated is reported as a forbidden transition. With
    `per_instance=True` each activity instance (maximal run of the activity's events) starts a
    fresh history; this is the only difference."""
    checker = checker or IntegratedComplianceChecker()
    reasons = []
    for t, events in enumerate(log):
        history = {}
        instance = -1
        for idx, event in enumerate(events):
            act_name = event.get("concept:name", "")
            curr_a = event.get("concept:currentState", "")
            curr_r = event.get("concept:resource_current_state", "")
            if idx == 0 or events[idx - 1].get("concept:name", "") != act_name:
                instance += 1
            key = (act_name, instance) if per_instance else act_name
            h = history.setdefault(key, {"act": None, "res": None})
            prev_a, prev_r = h["act"], h["res"]

            a_ok, a_reason = checker.check_compliance(prev_a, curr_a, event.get("activity_policy", ""),
                                                      event, events, idx)

            merged = {}
            for table in (checker.get_transition_table(event.get("resource_policy", "")),
                          checker.get_transition_table(event.get("resource_policy2", ""))):
                for src_state, targets in table.items():
                    for dst_state, data in targets.items():
                        slot = merged.setdefault(src_state, {}).setdefault(
                            dst_state, {"constraints": [], "asset_duration": None})
                        slot["constraints"].extend(data.get("constraints", []))
                        if data.get("asset_duration"):
                            slot["asset_duration"] = data["asset_duration"]
            p = (prev_r or "").lower().strip() if prev_r else None
            c = (curr_r or "").lower().strip()
            r_ok, r_reason = True, "ok"
            if p is not None and p != c:
                if p not in merged or c not in merged[p]:
                    r_ok, r_reason = False, f"no_transition_{p}_to_{c}"
                else:
                    data = merged[p][c]
                    ctx = checker._build_context(event, events, idx, prev_r, curr_r)
                    if data.get("constraints"):
                        passed, violations = checker.evaluator.evaluate(data["constraints"], ctx)
                        if not passed:
                            r_ok, r_reason = False, f"constraint: {'; '.join(violations)}"
                    if r_ok and data.get("asset_duration"):
                        passed, msg = checker.evaluator.evaluate_duration(data["asset_duration"], ctx)
                        if not passed:
                            r_ok, r_reason = False, f"duration_{msg}"

            correct_res = act_info.get(act_name, {}).get("resource", "")
            event_res = (event.get("concept:resource") or "").strip()
            b_ok = event_res == correct_res
            bind_policy = event.get("binding_policy", "")
            if b_ok and bind_policy and prev_a and curr_a:
                table = checker.get_transition_table(bind_policy)
                pb, cb = prev_a.lower().strip(), curr_a.lower().strip()
                if pb in table and cb in table[pb] and table[pb][cb].get("constraints"):
                    ctx = checker._build_context(event, events, idx, prev_a, curr_a)
                    passed, _ = checker.evaluator.evaluate(table[pb][cb]["constraints"], ctx)
                    b_ok = passed

            event["a_comp"], event["r_comp"], event["b_comp"] = int(a_ok), int(r_ok), int(b_ok)
            if not (a_ok and r_ok and b_ok):
                reasons.append({"trace": t, "event": idx, "activity": act_name,
                                "a": a_reason if not a_ok else "", "r": r_reason if not r_ok else "",
                                "b": "" if b_ok else f"resource '{event_res}' vs '{correct_res}'"})
            h["act"], h["res"] = curr_a, curr_r
    return reasons


def aggregate(log):
    """Same aggregation as src/agg_event.py: one event per distinct activity name, in order of
    first occurrence, with the mean of (a_comp + r_comp + b_comp) / 3 rounded to 3 decimals."""
    aggregated = []
    for events in log:
        groups = {}
        for e in events:
            groups.setdefault(e["concept:name"], []).append(e)
        aggregated.append([
            (name, round(sum((e.get("a_comp", 0) + e.get("r_comp", 0) + e.get("b_comp", 0)) / 3.0
                             for e in group) / len(group), 3))
            for name, group in groups.items()
        ])
    return aggregated


def compliance_rates(aggregated):
    """v per trace, as in src/conf_check.py: mean compliance score of the aggregated events."""
    return [sum(s for _, s in trace) / len(trace) if trace else 0.0 for trace in aggregated]


def to_event_log(sequences):
    log = EventLog()
    for i, names in enumerate(sequences):
        trace = Trace(attributes={"concept:name": str(i)})
        for k, name in enumerate(names):
            trace.append(Event({"concept:name": name,
                                "time:timestamp": PLAYOUT_START + timedelta(seconds=k)}))
        log.append(trace)
    return log


def align(sequences, model, max_trace_time=None, max_total_time=None):
    """Optimal alignments (one A* search per distinct sequence). With time budgets, a sequence
    whose search exceeds them gets None."""
    net, im, fm = model
    parameters = {"show_progress_bar": False}
    if max_trace_time:
        parameters["max_align_time_trace"] = max_trace_time
    if max_total_time:
        parameters["max_align_time"] = max_total_time
    return alignments.apply_log(to_event_log(sequences), net, im, fm, parameters=parameters)


def fitness(sequences, model):
    """Control-flow pathway, as in src/conf_check.py: alignment fitness per trace."""
    return [r["fitness"] for r in align(sequences, model)]


def involved_activities(alignment):
    """Activities involved in the deviations of an alignment (pairs of log and model labels):
    those of log moves and visible model moves, and, when an activity is both a log move and a
    model move (reordered), the activities synchronized between the two moves. The result does
    not depend on which of the equally optimal alignments of a reordering is returned."""
    log_moves = {i: a for i, (a, m) in enumerate(alignment) if m == ">>"}
    model_moves = {i: m for i, (a, m) in enumerate(alignment) if a == ">>" and m is not None}
    involved = set(log_moves.values()) | set(model_moves.values())
    for i, a in log_moves.items():
        for j, m in model_moves.items():
            if a == m:
                involved |= {x for x, y in alignment[min(i, j):max(i, j)] if x == y and x != ">>"}
    return involved


def pacc_scores(f, v, alpha=0.7, beta=0.3):
    return [alpha * fi + beta * vi for fi, vi in zip(f, v)]


def run_pacc(log, act_info, model):
    """PACC as published (src/policy_check.py, agg_event.py, conf_check.py). Fitness is computed
    on one event per distinct activity name, so repeated activities (loops) are lost."""
    reasons = check_policies(log, act_info)
    aggregated = aggregate(log)
    v = compliance_rates(aggregated)
    f = fitness([[name for name, _ in trace] for trace in aggregated], model)
    return f, v, pacc_scores(f, v), reasons


def control_flow_pathway(log, act_info, model):
    """f per trace: alignment fitness of the activity-instance projection. Alone, this is the
    alignment-only baseline."""
    return fitness([checker.instance_projection(events, act_info) for events in log], model)


def policy_pathway(log, act_info, semantics=checker.Semantics()):
    """v per trace (Eq. (1)) and the failed evaluations, per the formal model."""
    v, reasons = [], []
    for t, events in enumerate(log):
        flags, why = checker.check_trace(events, act_info, semantics)
        v.append(checker.compliance_rate(flags))
        reasons += [dict(r, trace=t) for r in why]
    return v, reasons


def run_formal(log, act_info, model, alpha=0.7, semantics=checker.Semantics()):
    """PACC per the formal model: f, v, alpha f + (1 - alpha) v, and the failed evaluations."""
    f = control_flow_pathway(log, act_info, model)
    v, reasons = policy_pathway(log, act_info, semantics)
    return f, v, pacc_scores(f, v, alpha, 1 - alpha), reasons


def generate(model, act_info, n_traces, policy_rate, flow_rate, seed,
             policy_types=VIOLATION_TYPES, policy_weights=VIOLATION_WEIGHTS):
    """One synthetic observed log with ground truth: playout, enrichment, then injection."""
    rng = random.Random(seed)
    act_types, res_types = load_types()
    log = enrich(simulate(model, n_traces, rng), act_info, act_types, res_types, rng)
    policy_labels = inject_policy_violations(log, policy_rate, rng, policy_types, policy_weights, act_info)
    flow_labels = inject_control_flow_deviations(log, flow_rate, rng, act_info)
    return log, policy_labels, flow_labels
