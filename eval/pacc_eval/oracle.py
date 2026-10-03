"""Independent admissibility oracle for the mutation analysis.

Decides from the lifecycle automata (config/activity_types.json, config/resource_types.json)
and the configuration, not from the compiled ODRL policies, whether the behaviour recorded in
a trace is admissible: each activity instance follows its transactional lifecycle from the
initial state, its consumption follows the resource's consumption lifecycle from the initial
consumption state, uses the bound resource, records parseable policies, and changes the
resource state during a consumption episode (from entering consumed to entering the release
state of the consumption property, as defined for bindings in the manuscript) only while the
activity is activated. Temporal guards are not modelled.
"""
import json

from . import checker

RELEASE = {"shareableunlimited": "withdrawn"}  # every other consumption property releases in "done"


def _relation(automaton):
    return {(s.lower(), t.lower()) for s, targets in automaton["transitions"].items() for t in targets}


def _parses(value):
    try:
        return bool(json.loads(value)) if isinstance(value, str) else bool(value)
    except json.JSONDecodeError:
        return False


def admissible(events, act_info, act_types, res_types):
    starts = checker.instance_starts(events, act_info)
    for s, end in zip(starts, starts[1:] + [len(events)]):
        info = act_info.get(events[s]["concept:name"])
        if info is None:
            continue
        a_rel = _relation(act_types[info["type"]])
        r_rel = _relation(res_types[info["resourceType"].lower()])
        release = RELEASE.get(info["resourceType"].lower(), "done")
        prev, consuming = None, False
        for e in events[s:end]:
            if not all(_parses(e.get(k)) for k in ("activity_policy", "resource_policy", "binding_policy")):
                return False
            if (e.get("concept:resource") or "").strip() != info["resource"]:
                return False
            a = checker._norm(e.get("concept:currentState"))
            r = checker._norm(e.get("concept:resource_current_state"))
            if prev is None:
                if a != checker._norm(info["currentState"]) or r != checker._norm(info["resource_current_state"]):
                    return False
                consuming = r == "consumed"
            else:
                if a != prev[0] and (prev[0], a) not in a_rel:
                    return False
                if r != prev[1]:
                    if (prev[1], r) not in r_rel or ((r == "consumed" or consuming) and a != "activated"):
                        return False
                    consuming = r == "consumed" or (consuming and r != release)
            prev = (a, r)
    return True
