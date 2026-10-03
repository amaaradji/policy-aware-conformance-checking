"""Corrected policy configuration for the evaluation models.

The published activity_info files assign resource and binding policy templates that do not
match the resource's consumption property (e.g. limited-but-renewable resources mostly use the
limited template, which has no renewal rule), so the published checker flags compliant
behaviour. Here each resource type gets the template(s) whose transitions cover its consumption
cycles in config/resource_types.json; limited resources get both cycles (l1 and l2), merged by
the checker through `resource_policy2`. Activity policies are unchanged (already consistent).
The binding templates leave the consumption bound as a placeholder ("PXM", X minutes); it is
instantiated with BINDING_EPISODE_BOUND. The shareable-limited resource template declares a
zero consumption duration ("PT0S"), which no consumption can meet; it is aligned with its
non-shareable counterpart (PT1H).
"""
import json

from .core import load_json
from .paths import POLICIES

BINDING_EPISODE_BOUND = "PT5M"
PLACEHOLDERS = {"PXM": BINDING_EPISODE_BOUND, "PT0S": "PT1H"}

RESOURCE_TEMPLATES = {
    "ShareableUnlimited": ("shareableUL.json", None),
    "NonShareableUnlimited": ("non-ShareableUL.json", None),
    "ShareableLimited": ("shareableLi1.json", "shareableLi2.json"),
    "NonShareableLimited": ("non-ShareableLi1.json", "non-ShareableLi2.json"),
    "ShareableLimitedBR": ("shareableLr.json", None),
    "NonShareableLimitedBR": ("non-ShareableLr.json", None),
}
BINDING_TEMPLATES = {
    "ShareableUnlimited": "activity_shareableUnlimited.json",
    "NonShareableUnlimited": "activity_noneShareableUnlimited.json",
    "ShareableLimited": "activity_shareableLimited.json",
    "NonShareableLimited": "activity_noneSharealeLimited.json",
    "ShareableLimitedBR": "activity_shareableLimitedButRenewable.json",
    "NonShareableLimitedBR": "activity_noneShareableLimitedButRenewable.json",
}
ACTIVITY_TEMPLATES = {"Pivot": "pivot.json", "Compensatable": "compensatable.json",
                      "Retriable": "retriable.json"}


def instantiate(node, values=PLACEHOLDERS):
    """Copy of a policy with placeholder literals replaced."""
    if isinstance(node, dict):
        return {k: instantiate(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [instantiate(v, values) for v in node]
    return values.get(node, node) if isinstance(node, str) else node


def template(kind, name):
    return instantiate(load_json(POLICIES / kind / name))


def correct_activity(entry):
    rtype = entry["resourceType"]
    primary, secondary = RESOURCE_TEMPLATES[rtype]
    fixed = dict(entry)
    fixed["activity_policy"] = template("activity_policies", ACTIVITY_TEMPLATES[entry["type"]])
    fixed["resource_policy"] = template("resource_policies", primary)
    fixed.pop("resource_policy2", None)
    if secondary:
        fixed["resource_policy2"] = template("resource_policies", secondary)
    fixed["binding_policy"] = template("binding_policies", BINDING_TEMPLATES[rtype])
    return fixed


def corrected_activity_info(act_info):
    return {name: correct_activity(entry) for name, entry in act_info.items()}


def write_activity_info(act_info, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"activities": list(act_info.values())}, f, indent=2)
