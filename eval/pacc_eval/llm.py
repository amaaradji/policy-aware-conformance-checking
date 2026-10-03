"""LLM explanation of a PACC report, and the checks used to assess its faithfulness.

The prompt is the one of the prototype's interface (PFE_interface/Backend/src/llm_explainer.py,
explain_report_with_context), verbatim except for the inputs: the violation summary is built
from the failed evaluations of the checker (with the violated constraint and observed value)
instead of being decoded from the flags, and the activity sequences are given as text instead
of an excerpt of the XES file.
"""
import os
import re
import time
from html import unescape

SYSTEM_PROMPT = """You are a process mining expert explaining conformance and compliance results to a business analyst.

You receive a pre-decoded POLICY VIOLATION SUMMARY. Each entry is already interpreted — it tells you:
- Which trace and which activity
- Whether it is an [ACTIVITY POLICY], [RESOURCE POLICY], or [BINDING POLICY] violation
- The resource name, resource type, and what went wrong

POLICY TYPE REFERENCE (use this to explain violations):
- [ACTIVITY POLICY] (a_comp=0): The activity did not follow its required lifecycle state machine.
  - Pivot: must go NotActivated → Activated → Done or Failed. No retry, no compensation.
  - Retriable: may retry after failure, but only up to the allowed retry count.
  - Compensatable: if it reaches Done and then fails, it must be compensated (→ Compensated state).
- [RESOURCE POLICY] (r_comp=0): The resource was used in a way that violates its consumption rules.
  - NonShareable resources require exclusive access: only one activity at a time.
  - Limited resources have a capped usage count. 'Expired' or 'Overused' means the cap was exceeded.
  - 'Done' is a terminal state — a resource in 'Done' must not be reused; it should be Withdrawn.
- [BINDING POLICY] (b_comp=0): The activity-resource association is invalid. Only an Activated activity may consume the resource.

STRICT OUTPUT RULES:
- Be CONCISE. Use maximum 3 bullet points per section subsection.
- Use the violation summary as ground truth. Do not guess.
- Never say "likely", "not specified", or "details not shown".
- Do not repeat scores.
- Plain text only. Format: numbers (1. 2. 3. 4.) for sections, letters (A. B.) for subsections, dashes (-) for bullets.
- Each bullet must be a complete, standalone sentence explaining one violation clearly.
- Section 4 (RECOMMENDATIONS) is MANDATORY. Always include it with concrete, actionable fixes.
- Return your entire response as valid HTML wrapped in a single <div> tag, using <h3> for section titles, <h4> for subsections, and <ul>/<li> for bullet points."""

USER_PROMPT = """Using the data below, write the 4-section analysis. SECTION 4 (RECOMMENDATIONS) IS REQUIRED.

---
CONFORMANCE REPORT (scores only - do not repeat):
{report}

---
ACTIVITY SEQUENCES (abbreviated):
{sequences}

---
POLICY VIOLATION SUMMARY (your primary source):
{violations}

---
BPMN MODEL (activity names only):
{activities}
---

Write these 4 sections (Section 4 is mandatory):

1. PROBLEMATIC TRACES - TWO CATEGORIES
A. Control-Flow Issues: List traces with conformance < 1.00. Name specific missing/extra/out-of-order activities.
B. Policy Violations: For each trace with violations, write one brief bullet per violation type (not per event). Include activity name, resource name, policy type, and what went wrong.

2. PERFECT-CONFORMANCE BUT NON-COMPLIANT TRACES
List traces where conformance = 1.00 but compliance < 1.00. For each, give 1-2 sentences explaining the main violation.

3. CORRELATION ANALYSIS
Answer in 2-3 sentences: Are policy violations independent of sequence deviations? Which violation type is most common?

4. RECOMMENDATIONS (MANDATORY - must include)
Provide exactly 4 concrete fixes:
- One model change to fix control-flow issues
- One fix for activity policy violations (lifecycle handling)
- One fix for resource policy violations (access patterns, usage caps, state transitions)
- One fix for binding policy violations (activity-resource assignment rules)

Be specific and actionable. Example: "Add a 'Compensated' state handler for Compensatable activities" or "Configure resource 'Credit Bureau API' to reject access when in 'Done' state."""

REFERENCE = SYSTEM_PROMPT[SYSTEM_PROMPT.index("POLICY TYPE REFERENCE"):SYSTEM_PROMPT.index("STRICT OUTPUT RULES")]
REVISED_REFERENCE = """POLICY TYPE REFERENCE (use this to explain violations):
- [ACTIVITY POLICY] (a_comp=0): the activity instance took a lifecycle transition that its activity policy does not authorize, or a transition whose constraint failed (for instance a retry beyond the allowed count).
  - Pivot: NotActivated, then Activated, then Done or Failed.
  - Compensatable: as Pivot, and Done may be followed by Compensated.
  - Retriable: as Pivot, and Failed may be followed by Activated again, up to the allowed count.
- [RESOURCE POLICY] (r_comp=0): the consumption of the bound resource took a transition that its resource policy does not authorize (for instance consuming a non-shareable resource without locking it, or withdrawing it while it is consumed), or a transition whose constraint failed (for instance a consumption longer than the allowed duration).
- [BINDING POLICY] (b_comp=0): the activity used a resource other than the one bound to it, or the resource changed state during its consumption while the activity was not Activated (for instance the activity completed before releasing the resource), or a consumption exceeded the time bound of the binding.
- Each entry gives the transition, the violated constraint, and the observed value. Explain violations with these facts only.

"""
REVISED_USER_PROMPT = USER_PROMPT.replace(
    "ACTIVITY SEQUENCES (abbreviated):\n{sequences}\n",
    "ACTIVITY SEQUENCES (abbreviated):\n{sequences}\n\n---\n"
    "CONTROL-FLOW DEVIATIONS (from optimal alignments with the model; your primary source for section 1A):\n"
    "{deviations}\n").replace(
    "A. Control-Flow Issues: List traces with conformance < 1.00. Name specific missing/extra/out-of-order activities.",
    "A. Control-Flow Issues: List traces with conformance < 1.00. Name the missing, unexpected, or out-of-order "
    "activities given in the CONTROL-FLOW DEVIATIONS.")
VARIANTS = {"prototype": (SYSTEM_PROMPT, USER_PROMPT),
            "revised": (SYSTEM_PROMPT.replace(REFERENCE, REVISED_REFERENCE), REVISED_USER_PROMPT)}
DIMENSIONS = {"a": "ACTIVITY POLICY", "r": "RESOURCE POLICY", "b": "BINDING POLICY"}


def deviation_text(alignments, f):
    """Control-flow deviations per trace from optimal alignments (pairs of log and model labels)."""
    lines = []
    for t, moves in enumerate(alignments):
        if f[t] == 1:
            continue
        log_moves = [a for a, m in moves if m == ">>"]
        model_moves = [m for a, m in moves if a == ">>" and m is not None]
        swapped = set(log_moves) & set(model_moves)
        parts = ([f"'{a}' out of order" for a in sorted(swapped)]
                 + [f"unexpected '{a}'" for a in log_moves if a not in swapped]
                 + [f"missing '{m}'" for m in model_moves if m not in swapped])
        lines.append(f"Trace {t + 1}: " + "; ".join(parts))
    return "\n".join(lines) or "No control-flow deviations."


def report_inputs(log, config, activities, f, v, pacc, reasons, alignments=()):
    """Prompt inputs from a checked log: traces are numbered from 1."""
    from .checker import instance_projection
    report = "\n".join(f"Trace {t + 1}: Conformance: {f[t]:.3f}, Compliance: {v[t]:.3f}, Total: {pacc[t]:.3f}"
                       for t in range(len(log)))
    report += f"\nOverall score: {sum(pacc) / len(pacc):.4f}"
    sequences = "\n".join(f"Trace {t + 1}: " + " -> ".join(instance_projection(events, config))
                          for t, events in enumerate(log))
    lines = []
    for t in range(len(log)):
        mine = [r for r in reasons if r["trace"] == t]
        if not mine:
            continue
        lines.append(f"Trace {t + 1}:")
        for r in mine:
            info = config[r["activity"]]
            for key, label in DIMENSIONS.items():
                if r[key]:
                    lines.append(f"  - [{label}] activity '{r['activity']}' ({info['type']}), resource "
                                 f"'{info['resource']}' ({info['resourceType']}), event {r['event'] + 1}: {r[key]}")
    violations = "\n".join(lines) or "No violations found."
    return {"report": report, "sequences": sequences, "violations": violations,
            "activities": ", ".join(activities), "deviations": deviation_text(alignments, f)}


def client():
    from openai import AzureOpenAI
    return (AzureOpenAI(api_key=os.environ["AZURE_OPENAI_KEY"], azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                        api_version="2024-12-01-preview"),
            os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1"))


def explain(llm, deployment, inputs, variant="revised", temperature=0.2):
    """One explanation (the prototype's settings: temperature 0.2, at most 2000 tokens)."""
    system, user = VARIANTS[variant]
    start = time.perf_counter()
    response = llm.chat.completions.create(
        model=deployment, temperature=temperature, max_tokens=2000,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user.format(**inputs)}])
    return (response.choices[0].message.content, time.perf_counter() - start,
            response.usage.prompt_tokens, response.usage.completion_tokens, response.model)


# ---------------------------------------------------------------- faithfulness checks

TRACE_LIST = re.compile(r"\btraces?\s*#?\s*((?:\d+(?:\s*(?:,|and|&|or)\s*#?\s*)?)+)", re.I)
DECIMAL = re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")


def _text(html):
    return unescape(re.sub(r"<[^>]+>", " ", html))


def sections(html):
    """Bullets per section: 1A control flow, 1B policy, 2 conformant-but-non-compliant, 3, 4."""
    parts, current = {"1A": [], "1B": [], "2": [], "3": [], "4": []}, None
    for tag, body in re.findall(r"<(h3|h4|li|p)[^>]*>(.*?)</\1>", html, flags=re.S | re.I):
        text = " ".join(_text(body).split())
        head = text.upper()
        if tag.lower() in ("h3", "h4"):
            if "CONTROL" in head:
                current = "1A"
            elif "POLICY VIOLATION" in head and "NON-COMPLIANT" not in head:
                current = "1B"
            elif "PERFECT" in head or "NON-COMPLIANT" in head:
                current = "2"
            elif "CORRELATION" in head:
                current = "3"
            elif "RECOMMENDATION" in head:
                current = "4"
            continue
        if current:
            parts[current].append(text)
    return parts


def trace_ids(text):
    return {int(n) for group in TRACE_LIST.findall(text) for n in re.findall(r"\d+", group)}


def _prf(claimed, truth):
    hits = len(claimed & truth)
    precision = hits / len(claimed) if claimed else (1.0 if not truth else 0.0)
    recall = hits / len(truth) if truth else 1.0
    return precision, recall


def _supported(number, values, tolerance=0.0051):
    return any(abs(float(number) - x) <= tolerance for x in values)


def assess(html, n_traces, f, v, reasons, config, inputs, deviations, labels):
    """Faithfulness of one explanation against the checked log (traces numbered from 1).
    `deviations[t]`: activities involved in the deviations of trace t (core.involved_activities);
    `labels`: every activity label of the log and the model."""
    parts = sections(html)
    flow_claims = flow_hits = 0
    for bullet in parts["1A"]:
        named = {a for a in labels if f"'{a.lower()}'" in bullet.lower() or f'"{a.lower()}"' in bullet.lower()}
        for t in trace_ids(bullet):
            if 1 <= t <= n_traces:
                flow_claims += len(named)
                flow_hits += len(named & deviations[t - 1])
    named_any = {(t, a) for b in parts["1A"] for t in trace_ids(b) if 1 <= t <= n_traces
                 for a in labels if f"'{a.lower()}'" in b.lower() or f'"{a.lower()}"' in b.lower()}
    deviating = [t for t in range(n_traces) if f[t] < 1]
    covered = sum(any((t + 1, a) in named_any for a in deviations[t]) for t in deviating)
    source = " ".join(inputs.values()).lower()
    quoted = [q for k in ("1A", "1B", "2") for b in parts[k] for q in re.findall(r"'([^']{2,})'", b)]
    source_values = [float(x) for x in DECIMAL.findall(" ".join(inputs.values()))]
    flow = {t + 1 for t in range(n_traces) if f[t] < 1}
    policy = {t + 1 for t in range(n_traces) if v[t] < 1}
    silent = {t + 1 for t in range(n_traces) if f[t] == 1 and v[t] < 1}
    clean = {t + 1 for t in range(n_traces)} - flow - policy
    claimed = {k: set().union(*(trace_ids(b) for b in parts[k])) if parts[k] else set() for k in ("1A", "1B", "2")}
    failed = {(r["trace"] + 1, r["activity"], key) for r in reasons for key in "arb" if r[key]}
    names = sorted(config, key=len, reverse=True)
    pairs = set()       # (trace, activity, dimension or None) claimed in policy bullets
    for bullet in parts["1B"] + parts["2"]:
        ids = trace_ids(bullet)
        acts = {a for a in names if a.lower() in bullet.lower()}
        dims = {k for k, label in DIMENSIONS.items() if label.split()[0].lower() + " polic" in bullet.lower()}
        pairs |= {(t, a, d) for t in ids for a in acts for d in (dims or {None})}
    true_pairs = {(t, a) for t, a, _ in failed}
    claimed_pairs = {(t, a) for t, a, _ in pairs}
    typed = {(t, a, d) for t, a, d in pairs if d}
    numbers = DECIMAL.findall(_text(html))
    mentioned = set().union(*claimed.values())
    p1a, r1a = _prf(claimed["1A"], flow)
    p1b, r1b = _prf(claimed["1B"], policy)
    p2, r2 = _prf(claimed["2"], silent)
    pp, rp = _prf(claimed_pairs, true_pairs)
    return {
        "flow_precision": p1a, "flow_recall": r1a, "policy_precision": p1b, "policy_recall": r1b,
        "silent_precision": p2, "silent_recall": r2,
        "flow_activity_precision": flow_hits / flow_claims if flow_claims else 1.0,
        "flow_coverage": covered / len(deviating) if deviating else 1.0,
        "pair_precision": pp, "pair_recall": rp,
        "dimension_accuracy": (sum((t, a, d) in failed for t, a, d in typed) / len(typed)) if typed else 1.0,
        "hallucinated_traces": len(mentioned & clean) + len({t for t in mentioned if t < 1 or t > n_traces}),
        "entities": len(quoted), "entities_unsupported": sum(q.lower() not in source for q in quoted),
        "numbers": len(numbers), "numbers_unsupported": sum(not _supported(n, source_values) for n in numbers),
        "sections_present": sum(bool(parts[k]) for k in ("1A", "1B", "2", "3", "4")),
        "recommendations": len(parts["4"]),
        "claimed_flow": sorted(claimed["1A"]), "claimed_policy": sorted(claimed["1B"]),
        "claimed_silent": sorted(claimed["2"]),
    }
