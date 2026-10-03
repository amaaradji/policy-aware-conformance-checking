"""Policy-compliance pathway as specified by the formal model of the paper (Section 4.4).

ODRL policies are compiled once into guarded transition relations (kappa). Each event of a
process-model activity then yields three evaluations, activity a(e), resource r(e), and
binding b(e), computed in one pass over the trace with incremental state per activity
instance: current state, entry time of that state, and transition counts, for the activity
instance and for its binding instance (the consumption of the bound resource).

`Semantics` switches parts of the model off. With guards, initial-state checks, strict
policy presence, and synchronization all off, the checker reduces to what the published
prototype evaluated (transition authorization and resource identity); this reduction is used
to validate the re-implementation against it.
"""
import json
import operator
from collections import Counter, namedtuple
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd

OPERATORS = {"eq": operator.eq, "neq": operator.ne, "lt": operator.lt, "lteq": operator.le,
             "gt": operator.gt, "gteq": operator.ge}
RESOURCE_POLICY_KEYS = ("resource_policy", "resource_policy2")

Constraint = namedtuple("Constraint", "operand op value")
Precedence = namedtuple("Precedence", "activity")  # event constraint on another activity of the case
Sync = namedtuple("Sync", "source activity_state target guard")
Semantics = namedtuple("Semantics", "guards initial strict sync", defaults=(True, True, True, True))
PUBLISHED = Semantics(guards=False, initial=False, strict=False, sync=False)


# ---------------------------------------------------------------- compilation (kappa)

def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _term(value):
    """Local name of an IRI, prefixed name, or JSON-LD node ('odrl:lteq' -> 'lteq')."""
    if isinstance(value, dict):
        value = value.get("@id", value.get("@value"))
    return str(value).strip().rstrip("/").split("/")[-1].split("#")[-1].split(":")[-1]


def _state(node):
    """Lifecycle state denoted by a state IRI (.../states/<State>), else None."""
    if isinstance(node, dict):
        node = node.get("@id")
    if not isinstance(node, str) or "/states/" not in node:
        return None
    return node.rstrip("/").split("/")[-1].lower()


def _literal(node):
    return node.get("@value", node.get("@id")) if isinstance(node, dict) else node


def _instant(text):
    text = str(text).strip()
    if len(text) == 10:
        return date.fromisoformat(text)
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _duration(text):
    return pd.Timedelta(str(text).strip()).to_pytimedelta()


PARSERS = {"count": int, "elapsedTime": _duration, "dateTime": _instant}


def _constraint(node):
    operand, op = _term(node.get("leftOperand")), _term(node.get("operator"))
    if op not in OPERATORS:
        raise ValueError(f"unsupported operator {op!r}")
    right = node.get("rightOperand")
    if operand == "event":
        if _term(right) == "policyUsage":
            return Constraint(operand, op, "policyUsage")
        if op not in ("gt", "gteq", "lt", "lteq"):
            raise ValueError(f"unsupported precedence operator {op!r}")
        return Constraint(operand, op, Precedence(str(_literal(right))))
    if operand not in PARSERS:
        raise ValueError(f"unsupported constraint operand {operand!r}")
    return Constraint(operand, op, PARSERS[operand](_literal(right)))


def _constraints(rule):
    nodes = _as_list(rule.get("constraint"))
    for action in _as_list(rule.get("action")):
        if isinstance(action, dict):
            nodes += _as_list(action.get("refinement"))
    return tuple(_constraint(n) for n in nodes if isinstance(n, dict))


@dataclass(eq=False)
class Policy:
    allowed: dict = field(default_factory=dict)      # (source, target) -> alternative guards
    prohibited: set = field(default_factory=set)
    sync: list = field(default_factory=list)         # binding synchronization rules

    def union(self, other):
        merged = Policy({k: list(v) for k, v in self.allowed.items()}, set(self.prohibited), list(self.sync))
        for key, guards in other.allowed.items():
            merged.allowed.setdefault(key, []).extend(guards)
        merged.prohibited |= other.prohibited
        merged.sync += [y for y in other.sync if y not in merged.sync]
        return merged


def _compile_rule(rule, policy, source=None, inherited=()):
    if not isinstance(rule, dict):
        return
    src = _state(rule.get("target")) or source
    dst = _state(rule.get("output"))
    guard = inherited + _constraints(rule)
    if src and dst and src != dst:
        assignee = _state(rule.get("assignee"))
        if assignee:
            policy.sync.append(Sync(src, assignee, dst, guard))
        else:
            policy.allowed.setdefault((src, dst), []).append(guard)
    for duty in _as_list(rule.get("duty")):
        _compile_rule(duty, policy, dst, guard)
    for consequence in _as_list(rule.get("consequence")):
        _compile_rule(consequence, policy, src, guard)


def _compile_node(node, policy, durations):
    if isinstance(node, list):
        for item in node:
            _compile_node(item, policy, durations)
    if not isinstance(node, dict):
        return
    types = [_term(t).lower() for t in _as_list(node.get("@type"))]
    if "asset" in types:
        bound = node.get("schema:duration", node.get("duration"))
        if bound is not None and _state(node):
            durations[_state(node)] = _duration(_literal(bound))
    for kind in ("permission", "obligation"):
        for rule in _as_list(node.get(kind)):
            _compile_rule(rule, policy)
    for rule in _as_list(node.get("prohibition")):
        src, dst = _state(rule.get("target")), _state(rule.get("output"))
        if src and dst:
            policy.prohibited.add((src, dst))
    _compile_node(node.get("@graph"), policy, durations)


def compile_policy(document):
    """kappa: ODRL policy (dict or JSON-LD string) to a Policy; None if absent or unparseable."""
    if isinstance(document, str):
        return _compile_text(document)
    if not isinstance(document, (dict, list)) or not document:
        return None
    policy, durations = Policy(), {}
    _compile_node(document, policy, durations)
    for (src, dst), guards in policy.allowed.items():
        if src in durations:
            bound = Constraint("elapsedTime", "lteq", durations[src])
            policy.allowed[(src, dst)] = [g + (bound,) for g in guards]
    return policy


@lru_cache(maxsize=None)
def _compile_text(text):
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return compile_policy(document)


EMPTY = Policy()


# ---------------------------------------------------------------- evaluation

def _observed(c, ts, entry, count):
    """(observed value, bound) of a constraint at a transition taken at time ts, from a state
    entered at `entry`, executed `count` times so far (including this one)."""
    if c.operand == "event":  # relative to the policy usage that started in the source state
        return ts - entry, timedelta(0)
    if c.operand == "dateTime":
        return (ts if isinstance(c.value, datetime) else ts.date()), c.value
    if c.operand == "elapsedTime":
        return ts - entry, c.value
    return count, c.value


def _failures(guard, ts, entry, count, done):
    """Violated constraints of a guard; `done` holds the activities completed earlier in the case."""
    found = []
    for c in guard:
        if isinstance(c.value, Precedence):
            completed = c.value.activity in done
            if completed != (c.op in ("gt", "gteq")):
                found.append(f"event {c.op} {c.value.activity} (observed: {c.value.activity} "
                             f"{'completed' if completed else 'not completed'} before)")
            continue
        left, right = _observed(c, ts, entry, count)
        if not OPERATORS[c.op](left, right):
            found.append(f"{c.operand} {c.op} {c.value} (observed {left})")
    return found


@dataclass
class Subject:
    """Activity instance or binding instance: current state, its entry time, transition counts."""
    state: str
    entry: datetime
    counts: Counter = field(default_factory=Counter)

    def move(self, new_state, ts):
        self.counts[(self.state, new_state)] += 1
        self.state, self.entry = new_state, ts


def _authorized(policy, subject, new_state, ts, guards, done=frozenset()):
    """comp_P for a transition of `subject` into `new_state` at time ts (subject not yet moved)."""
    t = (subject.state, new_state)
    if t in policy.prohibited:
        return f"{t[0]}->{t[1]} prohibited"
    if t not in policy.allowed:
        return f"{t[0]}->{t[1]} not authorized"
    if guards:
        failures = [_failures(g, ts, subject.entry, subject.counts[t] + 1, done) for g in policy.allowed[t]]
        if all(failures):
            return f"{t[0]}->{t[1]} violates " + " or ".join("; ".join(f) for f in failures)
    return ""


def _norm(value):
    return str(value).strip().lower() if value is not None else ""


def _event_policy(event, key):
    value = event.get(key)
    return _compile_text(value) if isinstance(value, str) else compile_policy(value)


@lru_cache(maxsize=None)
def _union(*policies):
    merged = Policy()
    for p in policies:
        merged = merged.union(p)
    return merged


def _resource_policy(event):
    """RP(e): union of the recorded resource policies (one per consumption cycle). The first
    is the resource's policy proper; the policy counts as absent when it is missing."""
    primary = _event_policy(event, RESOURCE_POLICY_KEYS[0])
    parts = [p for p in (primary, *(_event_policy(event, k) for k in RESOURCE_POLICY_KEYS[1:])) if p is not None]
    return (_union(*parts) if len(parts) > 1 else parts[0] if parts else EMPTY), primary is not None


def instance_starts(events, config):
    """Indices where activity instances start: label change, or a model activity's lifecycle
    restarting in its initial state."""
    starts = []
    for k, e in enumerate(events):
        name = e.get("concept:name")
        if k == 0 or events[k - 1].get("concept:name") != name:
            starts.append(k)
            continue
        info = config.get(name)
        if info is not None:
            initial = _norm(info["currentState"])
            if _norm(e.get("concept:currentState")) == initial != _norm(events[k - 1].get("concept:currentState")):
                starts.append(k)
    return starts


def instance_projection(events, config):
    return [events[k]["concept:name"] for k in instance_starts(events, config)]


@dataclass
class _Instance:
    activity: Subject
    resource: Subject
    episodes: dict = field(default_factory=dict)   # Sync rule -> [open time or None, closed count]
    binding: object = None                         # last recorded binding policy of the instance


def _sync_violation(policy, inst, a_state, r_state, ts, guards, done=frozenset()):
    """Synchronization of a resource transition into r_state with the activity instance."""
    problem = ""
    for y in policy.sync:
        episode = inst.episodes.setdefault(y, [None, 0])
        if (r_state == y.source or episode[0] is not None) and a_state != y.activity_state:
            problem = problem or f"resource {inst.resource.state}->{r_state} while activity {a_state or '?'}"
        if r_state == y.target and episode[0] is not None:
            episode[1] += 1
            failures = _failures(y.guard, ts, episode[0], episode[1], done) if guards else []
            if failures:
                problem = problem or f"consumption {y.source}->{y.target} violates " + "; ".join(failures)
            episode[0] = None
        elif r_state == y.source and episode[0] is None:
            episode[0] = ts
    return problem


def check_trace(events, config, semantics=Semantics()):
    """Evaluations (a, r, b) per event (None for events of non-model activities) and the
    reasons of the failed ones."""
    flags, reasons = [], []
    starts = set(instance_starts(events, config))
    inst, done = None, set()  # done: activities completed so far (precedence guards)
    for k, e in enumerate(events):
        name = e.get("concept:name")
        info = config.get(name)
        if info is None:
            flags.append(None)
            inst = None
            continue
        ts = e["time:timestamp"]
        a_state, r_state = _norm(e.get("concept:currentState")), _norm(e.get("concept:resource_current_state"))
        ap, bp = _event_policy(e, "activity_policy"), _event_policy(e, "binding_policy")
        rp, rp_present = _resource_policy(e)
        a_why = r_why = b_why = ""
        if k in starts or inst is None:
            inst = _Instance(Subject(a_state, ts), Subject(r_state, ts))
            if semantics.initial:
                if a_state != _norm(info["currentState"]):
                    a_why = f"instance starts in {a_state or '?'}"
                if r_state != _norm(info["resource_current_state"]):
                    r_why = f"consumption starts in {r_state or '?'}"
            inst.binding = bp
            if bp is not None and semantics.sync:
                for y in bp.sync:
                    inst.episodes[y] = [ts if r_state == y.source else None, 0]
        else:
            if a_state != inst.activity.state:
                a_why = _authorized(ap or EMPTY, inst.activity, a_state, ts, semantics.guards, done)
                inst.activity.move(a_state, ts)
            if r_state != inst.resource.state:
                r_why = _authorized(rp, inst.resource, r_state, ts, semantics.guards, done)
                rules = bp or inst.binding  # episodes depend on resource states, even if BIP(e) is absent
                if rules is not None and semantics.sync:
                    b_why = _sync_violation(rules, inst, a_state, r_state, ts, semantics.guards, done)
                inst.resource.move(r_state, ts)
        if semantics.strict:
            a_why = a_why if ap is not None else "activity policy absent"
            r_why = r_why if rp_present else "resource policy absent"
            b_why = b_why if bp is not None else "binding policy absent"
        if bp is not None:
            inst.binding = bp
        bound = info["resource"]
        if (e.get("concept:resource") or "").strip() != bound:
            b_why = f"resource {e.get('concept:resource')!r} is not the bound {bound!r}"
        flags.append((int(not a_why), int(not r_why), int(not b_why)))
        if a_why or r_why or b_why:
            reasons.append({"event": k, "activity": name, "a": a_why, "r": r_why, "b": b_why})
        if a_state == "done":
            done.add(name)
    return flags, reasons


def compliance_rate(flags):
    """Eq. (1): compliant evaluations over all evaluations of events of model activities."""
    evaluated = [f for f in flags if f is not None]
    return sum(map(sum, evaluated)) / (3 * len(evaluated)) if evaluated else 1.0
