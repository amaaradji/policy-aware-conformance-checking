import os
from pm4py.objects.log.importer.xes import importer as xes_importer
from pm4py.objects.log.exporter.xes import exporter as xes_exporter
from pm4py.objects.log.obj import EventLog, Trace, Event

# Paths
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)
ENRICHED_LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "enriched_log.xes")
AGG_LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "agg_log.xes")


def calculate_event_compliance(event):
    """Fast compliance calculation using direct access"""
    # Direct access is faster than .get() with defaults
    a = event.get("a_comp", 0)
    r = event.get("r_comp", 0)
    b = event.get("b_comp", 0)
    return (a + r + b) / 3.0


def aggregate_log():
    print(f"Loading enriched log: {ENRICHED_LOG_PATH}")
    log = xes_importer.apply(ENRICHED_LOG_PATH)
    aggregated_log = EventLog()
    
    # Pre-allocate list for better performance
    for trace in log:
        new_trace = Trace()
        new_trace.attributes["concept:name"] = trace.attributes.get("concept:name")
        
        # Use dictionary for grouping (fast)
        activity_groups = {}
        ordered_activities = []
        
        for event in trace:
            name = event["concept:name"]
            if name not in activity_groups:
                activity_groups[name] = []
                ordered_activities.append(name)
            activity_groups[name].append(event)
        
        for name in ordered_activities:
            events = activity_groups[name]
            
            # Fast average calculation
            total = 0.0
            for e in events:
                total += calculate_event_compliance(e)
            avg_compliance = total / len(events)
            
            agg_event = Event()
            agg_event["concept:name"] = name
            agg_event["time:timestamp"] = events[0]["time:timestamp"]
            agg_event["compliance_score"] = round(avg_compliance, 3)
            
            new_trace.append(agg_event)
        
        aggregated_log.append(new_trace)
    
    print(f"Saving aggregated log to: {AGG_LOG_PATH}")
    xes_exporter.apply(aggregated_log, AGG_LOG_PATH)
    print("Aggregation complete.")


if __name__ == "__main__":
    aggregate_log()