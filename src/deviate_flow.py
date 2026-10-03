"""
deviate_flow.py — OPTIMIZED (faster XML operations)
"""

import os
import sys
import random
import argparse
from lxml import etree
from pathlib import Path

ACTIVITY_REPLACEMENTS = {
    "Create Invoice": ["Create Order", "Generate Bill", "Make Invoice", "Create Receipt", "Issue Invoice"],
    "Send Invoice": ["Send Bill", "Email Invoice", "Deliver Invoice", "Send Receipt", "Transmit Invoice"]
}
ALL_ACTIVITIES = list(ACTIVITY_REPLACEMENTS.keys()) + ["Process Payment", "Update Record", "Validate Order", "Approve Request"]

XES_NS = "http://www.xes-standard.org/"
NAMESPACES = {'xes': XES_NS}


def rename_activities(xml_root, noise_level=0.2):
    events = xml_root.findall('.//xes:event', namespaces=NAMESPACES)
    total_events = len(events)
    events_to_modify = max(1, int(total_events * noise_level))
    
    print(f"\n{'='*50}")
    print(f"RENAMING ACTIVITIES")
    print(f"{'='*50}")
    print(f"Total events: {total_events}")
    print(f"Events to modify: {events_to_modify} ({noise_level*100:.0f}%)")
    
    random.shuffle(events)
    modified_count = 0
    
    for event in events[:events_to_modify]:
        for child in event:
            if child.tag.endswith('string') and child.get('key') == 'concept:name':
                old_name = child.get('value')
                if old_name in ACTIVITY_REPLACEMENTS:
                    new_name = random.choice(ACTIVITY_REPLACEMENTS[old_name])
                else:
                    new_name = random.choice(ALL_ACTIVITIES)
                child.set('value', new_name)
                modified_count += 1
                break
    
    print(f"\n✅ Modified {modified_count} events")
    return modified_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=str, default=None)
    parser.add_argument('--noise', type=float, default=0.2)
    parser.add_argument('--seed', type=int, default=None)
    args = parser.parse_args()
    
    if args.seed:
        random.seed(args.seed)
    
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = Path(__file__).parent.parent / "logs" / "agg_log.xes"
    
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)
    
    print(f"\n📁 Input file: {input_path}")
    
    tree = etree.parse(str(input_path))
    modified = rename_activities(tree.getroot(), args.noise)
    
    if modified > 0:
        tree.write(str(input_path), encoding='utf-8', xml_declaration=True)
        print("\n✅ RENAMING COMPLETE!")


if __name__ == "__main__":
    main()