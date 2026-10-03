"""
odrl_compliance_check_fixed.py — OPTIMIZED with aggressive caching
"""

import os
import ast
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Union, Tuple
from dataclasses import dataclass, field
from functools import lru_cache

from pm4py.objects.log.importer.xes import importer as xes_importer
from pm4py.objects.log.exporter.xes import exporter as xes_exporter

# ── paths ──────────────────────────────────────────────────────────────────
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)

LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "enriched_log.xes")
ACTIVITY_INFO_PATH = os.path.join(PROJECT_ROOT, "src", "activity_info_bpic2013.json")

OUTPUT_PRED = "http://example.com/ns#output"
ODRL_NS = "http://www.w3.org/ns/odrl/2/"
SCHEMA_NS = "https://schema.org/"

# Global caches
_POLICY_PARSE_CACHE = {}
_TRANSITION_TABLE_CACHE = {}


# ═══════════════════════════════════════════════════════════════════════════
# CASE-INSENSITIVE KEY ACCESS
# ═══════════════════════════════════════════════════════════════════════════

class CaseInsensitiveDict(dict):
    """Dictionary that allows case-insensitive key access."""
    def __init__(self, *args, **kwargs):
        super().__init__()
        self._key_map = {}
        
        for arg in args:
            if isinstance(arg, dict):
                for k, v in arg.items():
                    self[k] = v
        
        for k, v in kwargs.items():
            self[k] = v
    
    def __setitem__(self, key, value):
        lower_key = key.lower() if isinstance(key, str) else key
        self._key_map[lower_key] = key
        super().__setitem__(key, value)
    
    def __getitem__(self, key):
        if isinstance(key, str):
            lower_key = key.lower()
            if lower_key in self._key_map:
                original_key = self._key_map[lower_key]
                return super().__getitem__(original_key)
        return super().__getitem__(key)
    
    def __contains__(self, key):
        if isinstance(key, str):
            return key.lower() in self._key_map
        return super().__contains__(key)
    
    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError:
            return default


def deep_strip_case_insensitive(obj):
    """Recursively strip whitespace and convert to case-insensitive dicts."""
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            new_key = k.strip() if isinstance(k, str) else k
            new_dict[new_key] = deep_strip_case_insensitive(v)
        return new_dict
    elif isinstance(obj, list):
        return [deep_strip_case_insensitive(item) for item in obj]
    elif isinstance(obj, str):
        return obj.strip()
    return obj


def get_case_insensitive(d: Dict, key: str, default=None):
    """Get value from dict with case-insensitive key matching."""
    if not isinstance(d, dict):
        return default
    
    if key in d:
        return d[key]
    
    key_lower = key.lower()
    for k, v in d.items():
        if isinstance(k, str) and k.lower() == key_lower:
            return v
    
    return default


# ═══════════════════════════════════════════════════════════════════════════
# VALUE RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════

def resolve_value(val: Any) -> Any:
    """Extract value from ODRL JSON-LD structures."""
    if val is None:
        return None
    
    if isinstance(val, dict):
        if '@id' in val:
            return val['@id']
        if '@value' in val:
            return val['@value']
        return str(val)
    
    if isinstance(val, str):
        return val.strip()
    
    return str(val) if val is not None else None


# ═══════════════════════════════════════════════════════════════════════════
# CONSTRAINT REGISTRY AND CONTEXT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ConstraintContext:
    event: Dict[str, Any]
    trace: List[Dict[str, Any]]
    event_index: int
    prev_state: Optional[str] = None
    curr_state: Optional[str] = None
    timestamp: Optional[datetime] = None
    custom_data: Dict[str, Any] = field(default_factory=dict)
    
    def get_event_attr(self, name: str, default=None):
        return self.event.get(name, default)


_constraint_handlers: Dict[str, Callable[[Any, Any, ConstraintContext], bool]] = {}


def register_handler(left_operand: str):
    def decorator(func: Callable[[Any, Any, ConstraintContext], bool]):
        uri = left_operand if left_operand.startswith("http") else f"{ODRL_NS}{left_operand}"
        _constraint_handlers[uri] = func
        return func
    return decorator


def get_handler(left_operand: str) -> Optional[Callable]:
    if not left_operand:
        return None
    
    if left_operand in _constraint_handlers:
        return _constraint_handlers[left_operand]
    
    if ":" in left_operand and not left_operand.startswith("http"):
        fragment = left_operand.split(":")[-1]
        uri = f"{ODRL_NS}{fragment}"
        if uri in _constraint_handlers:
            return _constraint_handlers[uri]
    
    for sep in ["#", "/"]:
        if sep in left_operand:
            fragment = left_operand.rstrip(sep).split(sep)[-1]
            uri = f"{ODRL_NS}{fragment}"
            if uri in _constraint_handlers:
                return _constraint_handlers[uri]
    
    return None


# ═══════════════════════════════════════════════════════════════════════════
# CONSTRAINT HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

@register_handler("odrl:dateTime")
def check_datetime(op: Any, right_val: Any, ctx: ConstraintContext) -> bool:
    if ctx.timestamp is None:
        return False
    
    try:
        right_dt = parse_timestamp(str(right_val))
        if not right_dt:
            return False
        
        op_str = str(op).lower().replace(ODRL_NS, "").split("/")[-1]
        
        ops = {
            'lt': ctx.timestamp < right_dt,
            'lteq': ctx.timestamp <= right_dt,
            'gt': ctx.timestamp > right_dt,
            'gteq': ctx.timestamp >= right_dt,
            'eq': abs((ctx.timestamp - right_dt).total_seconds()) < 1,
            'neq': ctx.timestamp != right_dt,
        }
        return ops.get(op_str, False)
    except Exception:
        return False


@register_handler("odrl:event")
def check_event(op: Any, right_val: Any, ctx: ConstraintContext) -> bool:
    op_str = str(op).lower().replace(ODRL_NS, "").split("/")[-1]
    
    if "policyusage" in str(right_val).lower():
        perm_time = ctx.custom_data.get('permission_timestamp')
        
        if perm_time is None or ctx.timestamp is None:
            return True
        
        if op_str in ['gt', 'gteq']:
            return ctx.timestamp >= perm_time
        elif op_str in ['lt', 'lteq']:
            return ctx.timestamp <= perm_time
    
    return True


@register_handler("odrl:elapsedTime")
def check_elapsed_time(op: Any, right_val: Any, ctx: ConstraintContext) -> bool:
    duration = parse_iso_duration(str(right_val))
    if duration is None or ctx.timestamp is None:
        return False
    
    entry_time = ctx.custom_data.get('state_entry_timestamp')
    if not entry_time:
        entry_time = ctx.custom_data.get('permission_timestamp')
    
    if not entry_time:
        return False
    
    elapsed = ctx.timestamp - entry_time
    op_str = str(op).lower().replace(ODRL_NS, "").split("/")[-1]
    
    ops = {
        'lt': elapsed < duration,
        'lteq': elapsed <= duration,
        'gt': elapsed > duration,
        'gteq': elapsed >= duration,
    }
    return ops.get(op_str, False)


@register_handler("odrl:count")
def check_count(op: Any, right_val: Any, ctx: ConstraintContext) -> bool:
    try:
        limit = int(right_val)
        op_str = str(op).lower().replace(ODRL_NS, "").split("/")[-1]
        
        activity = ctx.get_event_attr('concept:name')
        current = sum(
            1 for e in ctx.trace[:ctx.event_index + 1]
            if e.get('concept:name') == activity
        )
        
        ops = {
            'lt': current < limit, 'lteq': current <= limit,
            'gt': current > limit, 'gteq': current >= limit,
            'eq': current == limit, 'neq': current != limit,
        }
        return ops.get(op_str, False)
    except (ValueError, TypeError):
        return False


@register_handler("odrl:spatial")
def check_spatial(op: Any, right_val: Any, ctx: ConstraintContext) -> bool:
    event_loc = ctx.get_event_attr('org:resource_location') or ctx.get_event_attr('location')
    target = str(right_val)
    op_str = str(op).lower().replace(ODRL_NS, "").split("/")[-1]
    
    if event_loc is None:
        return False
    
    ops = {
        'eq': event_loc == target,
        'neq': event_loc != target,
        'ispartof': target in event_loc or event_loc.startswith(target),
    }
    return ops.get(op_str, False)


@register_handler("odrl:role")
def check_role(op: Any, right_val: Any, ctx: ConstraintContext) -> bool:
    event_role = ctx.get_event_attr('org:role') or ctx.get_event_attr('role')
    target = str(right_val)
    op_str = str(op).lower().replace(ODRL_NS, "").split("/")[-1]
    
    if event_role is None:
        return False
    
    ops = {'eq': event_role == target, 'neq': event_role != target}
    return ops.get(op_str, False)


# ═══════════════════════════════════════════════════════════════════════════
# OPTIMIZED UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def parse_iso_duration_fast(iso_str: str) -> Optional[timedelta]:
    """Fast ISO duration parser - avoids regex"""
    if not iso_str:
        return None
    
    iso_str = str(iso_str).strip().upper()
    if not iso_str.startswith('P'):
        return None
    
    years = months = weeks = days = hours = minutes = seconds = 0
    
    t_pos = iso_str.find('T')
    date_part = iso_str[1:t_pos] if t_pos > 0 else iso_str[1:]
    time_part = iso_str[t_pos + 1:] if t_pos > 0 else ''
    
    # Parse date part
    i = 0
    while i < len(date_part):
        j = i
        while j < len(date_part) and date_part[j].isdigit():
            j += 1
        if j == i:
            break
        num = int(date_part[i:j])
        if j < len(date_part):
            unit = date_part[j]
            if unit == 'Y':
                years = num
            elif unit == 'M':
                months = num
            elif unit == 'W':
                weeks = num
            elif unit == 'D':
                days = num
        i = j + 1
    
    # Parse time part
    i = 0
    while i < len(time_part):
        j = i
        while j < len(time_part) and (time_part[j].isdigit() or time_part[j] == '.'):
            j += 1
        if j == i:
            break
        val = float(time_part[i:j]) if '.' in time_part[i:j] else int(time_part[i:j])
        if j < len(time_part):
            unit = time_part[j]
            if unit == 'H':
                hours = int(val) if isinstance(val, float) else val
            elif unit == 'M':
                minutes = int(val) if isinstance(val, float) else val
            elif unit == 'S':
                seconds = val
        i = j + 1
    
    total_days = years * 365 + months * 30 + weeks * 7 + days
    return timedelta(days=total_days, hours=hours, minutes=minutes, seconds=seconds)


def parse_iso_duration(iso_str: str) -> Optional[timedelta]:
    return parse_iso_duration_fast(iso_str)


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse timestamp to timezone-aware datetime."""
    if not ts_str:
        return None

    ts_str = str(ts_str).strip().replace('Z', '+00:00')

    for fmt in ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"]:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        pass

    return None


def _as_list(val):
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def _uri_to_state(uri: str) -> str:
    return str(uri).rstrip("/").split("/")[-1].lower().strip()


@lru_cache(maxsize=512)
def _parse_policy_string_cached(raw: str):
    """Cached version of policy parsing"""
    if not raw or not isinstance(raw, str) or len(raw.strip()) == 0:
        return None
    
    if raw in _POLICY_PARSE_CACHE:
        return _POLICY_PARSE_CACHE[raw]
    
    raw = raw.strip()
    result = None
    
    try:
        result = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        pass
    
    if result is None:
        try:
            result = json.loads(raw.replace("'", '"'))
        except json.JSONDecodeError:
            pass
    
    _POLICY_PARSE_CACHE[raw] = result
    return result


def _parse_policy_string(raw: str):
    return _parse_policy_string_cached(raw)


# ═══════════════════════════════════════════════════════════════════════════
# ASSET DURATION EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════

def extract_asset_durations(policy_obj) -> Dict[str, timedelta]:
    durations = {}
    
    def _walk(obj):
        if isinstance(obj, dict):
            obj_type = obj.get('@type', '')
            if isinstance(obj_type, str):
                obj_type = [obj_type]
            
            if any('asset' in str(t).lower() for t in _as_list(obj_type)):
                asset_id = obj.get('@id', '')
                for key in ['schema:duration', 'duration', 'hasDuration']:
                    dur_val = obj.get(key)
                    if dur_val:
                        if isinstance(dur_val, dict):
                            dur_str = dur_val.get('@value') or dur_val.get('value', '')
                        else:
                            dur_str = str(dur_val)
                        
                        duration = parse_iso_duration(dur_str)
                        if duration and asset_id:
                            durations[asset_id] = duration
                            break
            
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
    
    _walk(policy_obj)
    return durations


# ═══════════════════════════════════════════════════════════════════════════
# POLICY PARSER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TransitionRule:
    from_state: str
    to_state: str
    constraints: List[Dict] = field(default_factory=list)
    asset_duration: Optional[timedelta] = None
    rule_type: str = "permission"
    source: str = ""


def extract_constraints(obj: Dict) -> List[Dict]:
    """Extract constraints from object with case-insensitive key matching."""
    constraints = []
    
    constraint_val = None
    for key in obj.keys():
        if key.lower() == 'constraint':
            constraint_val = obj[key]
            break
    
    if not constraint_val:
        return constraints
    
    if isinstance(constraint_val, list):
        for c in constraint_val:
            if isinstance(c, dict):
                constraints.append(c)
    elif isinstance(constraint_val, dict):
        constraints.append(constraint_val)
    
    return constraints


def collect_transitions_with_constraints(policy_obj) -> Tuple[List[TransitionRule], Dict[str, timedelta]]:
    """Extract transitions with constraints and asset durations."""
    rules = []
    asset_durations = extract_asset_durations(policy_obj)
    
    def _extract(obj, inherited_target=None, inherited_constraints=None, 
                 current_asset_duration=None, rule_type="permission"):
        
        if inherited_constraints is None:
            inherited_constraints = []
        
        if isinstance(obj, dict):
            target = obj.get('target') or inherited_target
            output = obj.get('output') or obj.get(OUTPUT_PRED.lower())
            
            target_uri = resolve_value(target) if target else None
            if target_uri and target_uri in asset_durations:
                current_asset_duration = asset_durations[target_uri]
            
            own_constraints = extract_constraints(obj)
            all_constraints = inherited_constraints + own_constraints
            
            if target and output:
                from_uri = resolve_value(target)
                to_uri = resolve_value(output)
                
                if from_uri and to_uri:
                    rules.append(TransitionRule(
                        from_state=_uri_to_state(from_uri),
                        to_state=_uri_to_state(to_uri),
                        constraints=all_constraints,
                        asset_duration=current_asset_duration,
                        rule_type=rule_type,
                        source=obj.get('@id', 'anonymous')
                    ))
            
            for key in ['permission', 'obligation', 'prohibition']:
                for child in _as_list(obj.get(key)):
                    _extract(child, target, all_constraints if key == 'duty' else [],
                            current_asset_duration, key)
            
            for child in _as_list(obj.get('duty')):
                _extract(child, output, all_constraints, current_asset_duration, 'duty')
            
            for child in _as_list(obj.get('consequence')):
                _extract(child, target, all_constraints, current_asset_duration, 'consequence')
            
            for child in _as_list(obj.get('@graph')):
                _extract(child, None, [], None, 'permission')
        
        elif isinstance(obj, list):
            for item in obj:
                _extract(item, inherited_target, inherited_constraints,
                        current_asset_duration, rule_type)
    
    _extract(policy_obj)
    return rules, asset_durations


def build_transition_table(rules: List[TransitionRule]) -> Dict[str, Dict[str, Dict]]:
    """Build transition lookup table."""
    table: Dict[str, Dict[str, Dict]] = {}
    
    for rule in rules:
        if rule.from_state == rule.to_state:
            continue
        
        if rule.from_state not in table:
            table[rule.from_state] = {}
        if rule.to_state not in table[rule.from_state]:
            table[rule.from_state][rule.to_state] = {
                'constraints': [],
                'asset_duration': None
            }
        
        table[rule.from_state][rule.to_state]['constraints'].extend(rule.constraints)
        if rule.asset_duration:
            table[rule.from_state][rule.to_state]['asset_duration'] = rule.asset_duration
    
    return table


# ═══════════════════════════════════════════════════════════════════════════
# CONSTRAINT EVALUATOR
# ═══════════════════════════════════════════════════════════════════════════

class ConstraintEvaluator:
    def evaluate(self, constraints: List[Dict], ctx: ConstraintContext) -> Tuple[bool, List[str]]:
        """Evaluate constraints with case-insensitive field access."""
        if not constraints:
            return True, []
        
        violations = []
        
        for c in constraints:
            if not isinstance(c, dict):
                continue
            
            left = resolve_value(get_case_insensitive(c, 'leftOperand'))
            op = resolve_value(get_case_insensitive(c, 'operator'))
            right = resolve_value(get_case_insensitive(c, 'rightOperand'))
            
            if left is None:
                continue
            
            handler = get_handler(left)
            if handler is None:
                continue
            
            if not handler(op, right, ctx):
                violations.append(f"{left} {op} {right}")
        
        return len(violations) == 0, violations
    
    def evaluate_duration(self, allowed: timedelta, ctx: ConstraintContext) -> Tuple[bool, str]:
        """Check duration limit."""
        entry_time = ctx.custom_data.get('state_entry_timestamp')
        if not entry_time or not ctx.timestamp:
            return True, "no_timing"
        
        elapsed = ctx.timestamp - entry_time
        
        if elapsed > allowed:
            return False, f"exceeded: {elapsed} > {allowed}"
        
        return True, f"ok: {elapsed} <= {allowed}"


# ═══════════════════════════════════════════════════════════════════════════
# COMPLIANCE CHECKER
# ═══════════════════════════════════════════════════════════════════════════

class IntegratedComplianceChecker:
    def __init__(self):
        self.evaluator = ConstraintEvaluator()
        self._cache = _TRANSITION_TABLE_CACHE
    
    def get_transition_table(self, policy_raw: str) -> Dict:
        """Get cached transition table - OPTIMIZED"""
        if not policy_raw or not isinstance(policy_raw, str) or len(policy_raw.strip()) == 0:
            return {}
        
        if policy_raw in self._cache:
            return self._cache[policy_raw]
        
        parsed = _parse_policy_string_cached(policy_raw)
        if not parsed:
            self._cache[policy_raw] = {}
            return {}
        
        rules, asset_durations = collect_transitions_with_constraints(parsed)
        table = build_transition_table(rules)
        self._cache[policy_raw] = table
        return table
    
    def check_compliance(
        self,
        prev_state: Optional[str],
        curr_state: Optional[str],
        policy_raw: str,
        event: Dict,
        trace: List[Dict],
        event_index: int
    ) -> Tuple[bool, str]:
        """Check integrated compliance."""
        
        p = (prev_state or "").lower().strip() if prev_state else None
        c = (curr_state or "").lower().strip() if curr_state else ""
        
        if p is None:
            return True, "initial"
        if p == c:
            return True, "no_change"
        
        table = self.get_transition_table(policy_raw)
        
        if p not in table or c not in table[p]:
            return False, f"no_transition_{p}_to_{c}"
        
        trans_data = table[p][c]
        constraints = trans_data.get('constraints', [])
        asset_duration = trans_data.get('asset_duration')
        
        ctx = self._build_context(event, trace, event_index, prev_state, curr_state)
        
        if constraints:
            passed, violations = self.evaluator.evaluate(constraints, ctx)
            if not passed:
                return False, f"constraint: {'; '.join(violations)}"
        
        if asset_duration:
            passed, msg = self.evaluator.evaluate_duration(asset_duration, ctx)
            if not passed:
                return False, f"duration_{msg}"
        
        return True, f"ok_{p}_to_{c}"
    
    def _build_context(
        self,
        event: Dict,
        trace: List[Dict],
        event_index: int,
        prev_state: str,
        curr_state: str
    ) -> ConstraintContext:
        """Build evaluation context."""
        ts_str = event.get('time:timestamp') or event.get('timestamp')
        timestamp = parse_timestamp(ts_str) if ts_str else None
        
        custom_data = {}
        
        for i in range(event_index - 1, -1, -1):
            prev_event = trace[i]
            prev_state_name = prev_event.get('concept:currentState', '').lower().strip()
            
            if prev_state_name == prev_state.lower().strip():
                entry_ts = prev_event.get('time:timestamp') or prev_event.get('timestamp')
                if entry_ts:
                    custom_data['state_entry_timestamp'] = parse_timestamp(entry_ts)
                break
            
            if i == event_index - 1:
                perm_ts = prev_event.get('time:timestamp') or prev_event.get('timestamp')
                if perm_ts:
                    custom_data['permission_timestamp'] = parse_timestamp(perm_ts)
        
        return ConstraintContext(
            event=event,
            trace=trace,
            event_index=event_index,
            prev_state=prev_state,
            curr_state=curr_state,
            timestamp=timestamp,
            custom_data=custom_data
        )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def perform_compliance_check():
    with open(ACTIVITY_INFO_PATH, "r", encoding="utf-8") as f:
        raw_info = json.load(f)
    
    act_info = {a["activity"]: a for a in raw_info.get("activities", [])}
    
    print(f"Loading log: {LOG_PATH}")
    log_data = xes_importer.apply(LOG_PATH)
    
    checker = IntegratedComplianceChecker()
    
    total = a_fail = r_fail = b_fail = 0
    
    for trace_idx, trace in enumerate(log_data):
        history = {}
        trace_list = list(trace)
        
        for event_idx, event in enumerate(trace_list):
            total += 1
            
            act_name = event.get("concept:name", "")
            curr_a_state = event.get("concept:currentState", "")
            curr_r_state = event.get("concept:resource_current_state", "")
            
            act_policy = event.get("activity_policy", "")
            res_policy = event.get("resource_policy", "")
            res_policy2 = event.get("resource_policy2", "")
            bind_policy = event.get("binding_policy", "")
            
            if act_name not in history:
                history[act_name] = {"act": None, "res": None}
            
            prev_a = history[act_name]["act"]
            prev_r = history[act_name]["res"]
            
            # Activity compliance
            a_ok, a_reason = checker.check_compliance(
                prev_a, curr_a_state, act_policy, event, trace_list, event_idx
            )
            event["a_comp"] = 1 if a_ok else 0
            
            if not a_ok:
                a_fail += 1
                print(f"  [a_comp=0] trace={trace_idx} act='{act_name}' {a_reason}")
            
            # Resource compliance
            table1 = checker.get_transition_table(res_policy)
            table2 = checker.get_transition_table(res_policy2)
            
            merged = {}
            for t in [table1, table2]:
                for f, targets in t.items():
                    if f not in merged:
                        merged[f] = {}
                    for t_state, data in targets.items():
                        if t_state not in merged[f]:
                            merged[f][t_state] = {'constraints': [], 'asset_duration': None}
                        merged[f][t_state]['constraints'].extend(data.get('constraints', []))
                        if data.get('asset_duration'):
                            merged[f][t_state]['asset_duration'] = data['asset_duration']
            
            p = (prev_r or "").lower().strip() if prev_r else None
            c = (curr_r_state or "").lower().strip()
            
            r_ok, r_reason = True, "ok"
            
            if p is not None and p != c:
                if p not in merged or c not in merged[p]:
                    r_ok, r_reason = False, f"no_transition_{p}_to_{c}"
                else:
                    data = merged[p][c]
                    ctx = checker._build_context(event, trace_list, event_idx, prev_r, curr_r_state)
                    
                    if data.get('constraints'):
                        passed, violations = checker.evaluator.evaluate(data['constraints'], ctx)
                        if not passed:
                            r_ok, r_reason = False, f"constraint: {'; '.join(violations)}"
                    
                    if r_ok and data.get('asset_duration'):
                        passed, msg = checker.evaluator.evaluate_duration(data['asset_duration'], ctx)
                        if not passed:
                            r_ok, r_reason = False, f"duration_{msg}"
            
            event["r_comp"] = 1 if r_ok else 0
            
            if not r_ok:
                r_fail += 1
                print(f"  [r_comp=0] trace={trace_idx} act='{act_name}' {r_reason}")
            
            # Binding compliance
            correct_res = act_info.get(act_name, {}).get("resource", "")
            event_res = (event.get("concept:resource") or "").strip()
            
            binding_correct = (event_res == correct_res)
            
            if binding_correct and bind_policy:
                bind_table = checker.get_transition_table(bind_policy)
                if prev_a and curr_a_state:
                    p_bind = prev_a.lower().strip()
                    c_bind = curr_a_state.lower().strip()
                    
                    if p_bind in bind_table and c_bind in bind_table[p_bind]:
                        data = bind_table[p_bind][c_bind]
                        if data.get('constraints'):
                            ctx = checker._build_context(event, trace_list, event_idx, prev_a, curr_a_state)
                            passed, violations = checker.evaluator.evaluate(data['constraints'], ctx)
                            if not passed:
                                binding_correct = False
                                print(f"  [b_comp=0] binding constraint: {'; '.join(violations)}")
            
            b_ok = binding_correct
            event["b_comp"] = 1 if b_ok else 0
            
            if not b_ok:
                b_fail += 1
                if event_res != correct_res:
                    print(f"  [b_comp=0] resource mismatch '{event_res}' vs '{correct_res}'")
            
            history[act_name]["act"] = curr_a_state
            history[act_name]["res"] = curr_r_state
    
    print(f"\nSaving enriched log → {LOG_PATH}")
    xes_exporter.apply(log_data, LOG_PATH)
    
    print("\n" + "=" * 60)
    print("  COMPLIANCE SUMMARY")
    print("=" * 60)
    print(f"  Total events : {total}")
    if total:
        print(f"  a_comp=0 : {a_fail} ({100*a_fail/total:.1f}%)")
        print(f"  r_comp=0 : {r_fail} ({100*r_fail/total:.1f}%)")
        print(f"  b_comp=0 : {b_fail} ({100*b_fail/total:.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    perform_compliance_check()