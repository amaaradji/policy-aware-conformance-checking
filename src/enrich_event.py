import os
import json
import random
import warnings
from datetime import timedelta

warnings.filterwarnings("ignore")

from pm4py.objects.log.importer.xes import importer as xes_importer
from pm4py.objects.log.exporter.xes import exporter as xes_exporter
from pm4py.objects.log.obj import EventLog, Trace, Event

# ======================================
# CONFIGURATION & PATHS
# ======================================
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)

PATHS = {
    "activity_info":  os.path.join(PROJECT_ROOT, "src", "activity_info_bpic2013.json"),
    "activity_types": os.path.join(PROJECT_ROOT, "config", "activity_types.json"),
    "resource_types": os.path.join(PROJECT_ROOT, "config", "resource_types.json"),
    "input_log":      os.path.join(PROJECT_ROOT, "logs",   "event_log.xes"),
    "output_log":     os.path.join(PROJECT_ROOT, "logs",   "enriched_log.xes"),
}

ACTIVITY_OPTIONAL_STOPS = {
    "Pivot":         set(),
    "Compensatable": {"Done"},
    "Retriable":     {"Failed"},
}

# Pre-compile for faster access
ACTIVITY_OPTIONAL_STOPS_LOWER = {k: {s.lower() for s in v} for k, v in ACTIVITY_OPTIONAL_STOPS.items()}


# ======================================
# OPTIMIZED PATH GENERATOR (faster random choices)
# ======================================

def get_probabilistic_path(current_state, transitions_dict,
                            max_steps=30,
                            optional_stop_states=None,
                            max_visits=3):
    """Optimized path generator with pre-computed options"""
    STOP_TOKEN = "__STOP__"
    
    if optional_stop_states is None:
        optional_stop_states = set()
    optional_stop_lower = {s.lower() for s in optional_stop_states}
    
    path = [current_state]
    curr = current_state
    visit_counts = {current_state: 1}
    
    # Pre-compute transition options when possible
    for _ in range(max_steps):
        raw_options = transitions_dict.get(curr)
        if not raw_options:
            break
        
        # Build effective options
        if curr.lower() in optional_stop_lower:
            effective = list(raw_options) + [STOP_TOKEN]
        else:
            effective = list(raw_options)
        
        # Filter by revisit cap (list comprehension is fast)
        if max_visits > 0:
            effective = [s for s in effective if s == STOP_TOKEN or visit_counts.get(s, 0) < max_visits]
        
        if not effective:
            break
        
        # Fast random choice
        chosen = effective[random.randrange(len(effective))]
        
        if chosen == STOP_TOKEN:
            break
        
        path.append(chosen)
        visit_counts[chosen] = visit_counts.get(chosen, 0) + 1
        curr = chosen
    
    return path


# ======================================
# FASTER JSON LOADING
# ======================================

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


# ======================================
# OPTIMIZED ENRICHMENT
# ======================================

def enrich():
    # Load all configs once
    act_info_map = {a["activity"]: a for a in load_json(PATHS["activity_info"])["activities"]}
    act_types = load_json(PATHS["activity_types"])
    res_types_raw = load_json(PATHS["resource_types"])
    res_types = {k.lower(): v for k, v in res_types_raw.items()}
    
    original_log = xes_importer.apply(
        PATHS["input_log"], parameters={"show_progress_bar": False}
    )
    enriched_log = EventLog()
    
    # Pre-convert optional stops for faster lookup
    act_optional_stops_cache = {}
    for act_type, stops in ACTIVITY_OPTIONAL_STOPS.items():
        act_optional_stops_cache[act_type] = stops
    
    for trace in original_log:
        new_trace = Trace()
        new_trace.attributes["concept:name"] = trace.attributes.get("concept:name", "0")
        
        for original_event in trace:
            act_name = original_event["concept:name"]
            base_time = original_event["time:timestamp"]
            
            if act_name not in act_info_map:
                new_trace.append(original_event)
                continue
            
            info = act_info_map[act_name]
            r_type_key = info["resourceType"].lower()
            act_type = info["type"]
            
            # Activity path
            a_path = get_probabilistic_path(
                info["currentState"],
                act_types.get(act_type, {}).get("transitions", {}),
                optional_stop_states=ACTIVITY_OPTIONAL_STOPS.get(act_type, set()),
            )
            
            # Resource path
            r_path = get_probabilistic_path(
                info["resource_current_state"],
                res_types.get(r_type_key, {}).get("transitions", {}),
                optional_stop_states=None,
            )
            
            # Find activation index (fast linear search)
            activation_idx = 0
            for i, s in enumerate(a_path):
                if s == "Activated":
                    activation_idx = i
                    break
            
            # Build combined steps efficiently
            combined_steps = []
            
            # Steps before activation
            for i in range(activation_idx):
                combined_steps.append((a_path[i], r_path[0]))
            
            # Steps during activation (all resource states)
            for r_state in r_path:
                combined_steps.append((a_path[activation_idx], r_state))
            
            # Steps after activation
            for i in range(activation_idx + 1, len(a_path)):
                combined_steps.append((a_path[i], r_path[-1]))
            
            # Create events efficiently
            for i, (a_state, r_state) in enumerate(combined_steps):
                ev = Event()
                ev["concept:name"] = act_name
                ev["time:timestamp"] = base_time + timedelta(seconds=i)
                ev["concept:type"] = act_type
                ev["concept:currentState"] = a_state
                ev["concept:resource"] = info["resource"]
                ev["concept:resourceType"] = info["resourceType"]
                ev["concept:resource_current_state"] = r_state
                ev["activity_policy"] = json.dumps(info.get("activity_policy", {}), sort_keys=True)
                ev["resource_policy"] = json.dumps(info.get("resource_policy", {}), sort_keys=True)
                ev["binding_policy"] = json.dumps(info.get("binding_policy", {}), sort_keys=True)
                ev["a_comp"], ev["r_comp"], ev["b_comp"] = 1, 1, 1
                new_trace.append(ev)
        
        enriched_log.append(new_trace)
    
    xes_exporter.apply(
        enriched_log, PATHS["output_log"],
        parameters={"show_progress_bar": False}
    )
    print(f"Enriched log saved to: {os.path.basename(PATHS['output_log'])}")


if __name__ == "__main__":
    enrich()