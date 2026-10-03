"""
inject_deviations.py — OPTIMIZED (faster XML operations)
"""

import os
import sys
import random
import argparse
from datetime import datetime, timedelta
from lxml import etree
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

VALID_ACTIVITY_STATES = ["NotActivated", "Activated", "Done", "Failed", "Compensated"]
VALID_RESOURCE_STATES = ["NotConsumed", "Consumed", "Withdrawn", "MadeAvailable", "Locked", "Unlocked"]
INVALID_ACTIVITY_STATES = ["InvalidState", "Unknown", "Skipped", "Pending"]
INVALID_RESOURCE_STATES = ["Corrupted", "Expired", "Blocked", "Overused"]
VALID_RESOURCES = ["Accounting System", "Finance Clerk", "HR System", "Approval Board"]

VIOLATION_TYPES = ['activity_state_violation', 'resource_state_violation', 'wrong_resource', 
                   'timestamp_shift', 'missing_policy', 'state_sequence_skip']
VIOLATION_WEIGHTS = [0.25, 0.20, 0.20, 0.15, 0.10, 0.10]

XES_NS = "http://www.xes-standard.org/"
NAMESPACES = {'xes': XES_NS}

# Pre-compiled list of correct resources for fast lookup
CORRECT_RESOURCES = {"Create Invoice": "Accounting System", "Send Invoice": "Finance Clerk"}


def get_attribute_value(element, key):
    """Fast attribute value lookup"""
    for child in element:
        if child.tag.endswith('string') and child.get('key') == key:
            return child.get('value')
    return None


def find_attribute_element(element, key):
    """Fast attribute element lookup"""
    for child in element:
        if child.tag.endswith(('string', 'date', 'int')) and child.get('key') == key:
            return child
    return None


def update_compliance_flags(event_elem):
    """Update compliance flags (optimized)"""
    a_state = get_attribute_value(event_elem, 'concept:currentState')
    r_state = get_attribute_value(event_elem, 'concept:resource_current_state')
    resource = get_attribute_value(event_elem, 'concept:resource')
    
    a_comp = 1 if a_state in VALID_ACTIVITY_STATES else 0
    r_comp = 1 if r_state in VALID_RESOURCE_STATES else 0
    
    activity = get_attribute_value(event_elem, 'concept:name')
    correct = CORRECT_RESOURCES.get(activity, resource)
    b_comp = 1 if resource == correct else 0
    
    # Update or create elements
    for key, value in [('a_comp', a_comp), ('r_comp', r_comp), ('b_comp', b_comp)]:
        elem = find_attribute_element(event_elem, key)
        if elem is None:
            elem = etree.SubElement(event_elem, 'int')
            elem.set('key', key)
        elem.set('value', str(value))


def inject_deviations(xml_root, noise_level=0.1):
    """Inject deviations (optimized)"""
    events = xml_root.findall('.//xes:event', namespaces=NAMESPACES)
    total_events = len(events)
    violations_to_inject = max(1, int(total_events * noise_level))
    
    print(f"\n{'='*50}")
    print(f"INJECTING DEVIATIONS")
    print(f"{'='*50}")
    print(f"Total events: {total_events}")
    print(f"Violations to inject: {violations_to_inject} ({noise_level*100:.0f}%)")
    
    # Shuffle once
    random.shuffle(events)
    
    injected_count = 0
    violations_by_type = {vt: 0 for vt in VIOLATION_TYPES}
    
    for event in events[:violations_to_inject]:
        vtype = random.choices(VIOLATION_TYPES, weights=VIOLATION_WEIGHTS)[0]
        success = False
        
        if vtype == 'activity_state_violation':
            state_elem = find_attribute_element(event, 'concept:currentState')
            if state_elem is not None:
                new_state = random.choice(INVALID_ACTIVITY_STATES) if random.random() < 0.6 else random.choice(["Done", "Compensated", "Failed"])
                state_elem.set('value', new_state)
                success = True
        
        elif vtype == 'resource_state_violation':
            state_elem = find_attribute_element(event, 'concept:resource_current_state')
            if state_elem is not None:
                new_state = random.choice(INVALID_RESOURCE_STATES) if random.random() < 0.7 else random.choice(["Withdrawn", "Locked"])
                state_elem.set('value', new_state)
                success = True
        
        elif vtype == 'wrong_resource':
            resource_elem = find_attribute_element(event, 'concept:resource')
            if resource_elem is not None:
                current = resource_elem.get('value')
                available = [r for r in VALID_RESOURCES if r != current]
                if available:
                    resource_elem.set('value', random.choice(available))
                    success = True
        
        elif vtype == 'timestamp_shift':
            ts_elem = find_attribute_element(event, 'time:timestamp')
            if ts_elem is not None:
                try:
                    ts_str = ts_elem.get('value')
                    if '+' in ts_str or 'Z' in ts_str:
                        ts_str = ts_str.replace('Z', '+00:00')
                        dt = datetime.fromisoformat(ts_str)
                    else:
                        dt = datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S')
                    delta = timedelta(seconds=random.randint(30, 3600))
                    dt = dt + delta if random.random() < 0.5 else dt - delta
                    new_ts = dt.isoformat()
                    if '+' not in new_ts and 'Z' not in new_ts:
                        new_ts += '+00:00'
                    ts_elem.set('value', new_ts)
                    success = True
                except:
                    pass
        
        elif vtype == 'missing_policy':
            policy = random.choice(['activity_policy', 'resource_policy', 'binding_policy'])
            policy_elem = find_attribute_element(event, policy)
            if policy_elem is not None:
                if random.random() < 0.6:
                    policy_elem.getparent().remove(policy_elem)
                else:
                    policy_elem.set('value', 'INVALID_POLICY_STRING')
                success = True
        
        elif vtype == 'state_sequence_skip':
            state_elem = find_attribute_element(event, 'concept:currentState')
            if state_elem is not None:
                state_elem.set('value', random.choice(["Done", "Compensated", "Failed"]))
                success = True
        
        if success:
            injected_count += 1
            violations_by_type[vtype] += 1
    
    # Update compliance flags for all events
    print("\nUpdating compliance flags...")
    for event in events:
        update_compliance_flags(event)
    
    print(f"\n✅ Injected {injected_count} deviations")
    return injected_count


def main():
    parser = argparse.ArgumentParser(description="Inject deviations into enriched_log.xes")
    parser.add_argument('--noise', type=float, default=0.1, help='Fraction of events to modify')
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--input', type=str, default=None)
    args = parser.parse_args()
    
    if args.seed:
        random.seed(args.seed)
    
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = Path(__file__).parent.parent / "logs" / "enriched_log.xes"
    
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)
    
    print(f"\n📁 Input file: {input_path}")
    print(f"🔊 Noise level: {args.noise*100:.0f}%")
    
    tree = etree.parse(str(input_path))
    injected = inject_deviations(tree.getroot(), args.noise)
    
    if injected > 0:
        tree.write(str(input_path), encoding='utf-8', xml_declaration=True)
        print("\n✅ INJECTION COMPLETE!")


if __name__ == "__main__":
    main()