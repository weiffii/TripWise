"""Проверяемые правки маршрута 0.2: injected LLM, preview и confirm.

InMemoryRouteStore — reference adapter, не HTTP/БД. Backend передаёт actor_id
после авторизации; административные register/refresh не являются клиентским API.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import RLock
from uuid import uuid4
from ai_ds.data import CATEGORIES, nonempty
from ai_ds.openrouter import OpenRouterError
from ai_ds.recommendation import recommend
OPERATIONS = {
    'replace_poi': {'op', 'day', 'target_poi_id', 'desired_category'},
    'add_poi': {'op', 'day', 'poi_id'},
    'remove_poi': {'op', 'poi_id'},
    'set_day_pace': {'op', 'day', 'pace'},
    'set_meal_break': {'op', 'day', 'start_at', 'end_at'},
}
PACES = {'relaxed', 'balanced', 'intensive'}

class AssistantError(ValueError):
    def __init__(self, code, path=None):
        super().__init__(code)
        self.code, self.path = code, path
    def response(self):
        return {'schema_version': '0.2', 'error': {'code': self.code, 'path': self.path}}

def require(condition, code, path=None):
    if not condition:
        raise AssistantError(code, path)

def exact(value, fields, path):
    require(isinstance(value, dict) and set(value) == set(fields), 'INVALID_FIELDS', path)

def fingerprint(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise AssistantError('INVALID_SERVER_STATE') from None
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def sequences_of(route):
    require(isinstance(route, dict) and isinstance(route.get('days'), list), 'INVALID_ROUTE_SNAPSHOT')
    result = {}
    for day in route['days']:
        require(isinstance(day, dict) and nonempty(day.get('date')) and day['date'] not in result and isinstance(day.get('visits'), list), 'INVALID_ROUTE_SNAPSHOT')
        require(all(isinstance(v, dict) and nonempty(v.get('poi_id')) for v in day['visits']), 'INVALID_ROUTE_SNAPSHOT')
        result[day['date']] = [v['poi_id'] for v in day['visits']]
    return result

def schedule(state, sequences):
    from ai_ds.multiday import schedule_sequences
    return schedule_sequences(state['preferences'], state['pois'], state['recommendation'], state['travel_times'], state['routing_context'], sequences)

def validate(route, state):
    from ai_ds.multiday import validate_route
    return validate_route(route, state['preferences'], state['pois'], state['recommendation'], state['travel_times'], state['routing_context'])

def has_failed(value):
    if isinstance(value, dict):
        return any(has_failed(v) for v in value.values())
    if isinstance(value, list):
        return any(has_failed(v) for v in value)
    return isinstance(value, str) and value in ('failed', 'infeasible')

def check_validation(result):
    require(isinstance(result, dict), 'INVALID_ROUTE_VALIDATION')
    require('valid' not in result or result['valid'] is True, 'ROUTE_VALIDATION_FAILED')
    require(not has_failed(result.get('constraint_checks', result)), 'ROUTE_CONSTRAINT_FAILED')

class InMemoryRouteStore:
    def __init__(self):
        self._routes, self._proposals = {}, {}
        self._lock = RLock()
    def register_route(self, actor_id, route_id, *, preferences, pois, recommendation, travel_times, routing_context, poi_snapshot_id, route, version=1):
        require(nonempty(actor_id) and nonempty(route_id) and nonempty(poi_snapshot_id), 'INVALID_SERVER_STATE')
        require(type(version) is int and version >= 1, 'INVALID_VERSION')
        require(isinstance(route, dict) and route.get('schema_version') == '0.2', 'INVALID_ROUTE_SNAPSHOT')
        state = deepcopy(dict(owner=actor_id, route_id=route_id, version=version, preferences=preferences, pois=pois, recommendation=recommendation, travel_times=travel_times, routing_context=routing_context, poi_snapshot_id=poi_snapshot_id, route=route))
        fingerprint(state)
        sequences_of(state['route'])
        state['route'].update(route_id=route_id, version=version)
        with self._lock:
            require(route_id not in self._routes, 'ROUTE_ALREADY_EXISTS')
            self._routes[route_id] = state
    def _authorized(self, actor_id, route_id):
        state = self._routes.get(route_id)
        require(state is not None and state['owner'] == actor_id, 'ROUTE_NOT_FOUND')
        return state
    def get_route(self, actor_id, route_id):
        with self._lock:
            return deepcopy(self._authorized(actor_id, route_id))
    def refresh_data(self, actor_id, route_id, **changes):
        """Доверенное обновление backend инвалидирует существующие previews."""
        allowed = {'pois', 'recommendation', 'travel_times', 'routing_context', 'poi_snapshot_id'}
        require(bool(changes) and set(changes) <= allowed, 'INVALID_FIELDS')
        with self._lock:
            state = deepcopy(self._authorized(actor_id, route_id))
            state.update(deepcopy(changes))
            require(nonempty(state['poi_snapshot_id']), 'INVALID_SERVER_STATE')
            fingerprint(state)
            self._routes[route_id] = state

def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_INTERPRETER_FIELD')
        result[key] = value
    return result

def reject_constant(value):
    raise AssistantError('INVALID_INTERPRETER_JSON')

def parse_interpretation(value, *, extra_operations=None):
    if isinstance(value, str):
        try:
            value = json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
        except (ValueError, RecursionError):
            raise AssistantError('INVALID_INTERPRETER_JSON') from None
    exact(value, {'action', 'operations', 'clarification'}, 'interpretation')
    require(isinstance(value['action'], str) and value['action'] in ('propose', 'needs_clarification', 'unsupported'), 'INVALID_INTERPRETER_ACTION')
    require(isinstance(value['operations'], list), 'INVALID_OPERATIONS')
    require(value['clarification'] is None or nonempty(value['clarification']), 'INVALID_CLARIFICATION')
    if value['action'] != 'propose':
        require(not value['operations'] and nonempty(value['clarification']), 'INVALID_CLARIFICATION')
        return deepcopy(value)
    require(bool(value['operations']) and value['clarification'] is None, 'INVALID_PROPOSAL')
    operations = {**OPERATIONS, **(extra_operations or {})}
    for index, op in enumerate(value['operations']):
        path = f'operations[{index}]'
        require(isinstance(op, dict) and isinstance(op.get('op'), str) and op['op'] in operations, 'UNSUPPORTED_OPERATION', path)
        exact(op, operations[op['op']], path)
        if 'day' in op:
            nullable_day = op['op'] == 'add_poi' or (extra_operations is not None and
                op['op'] in {'add_named_poi', 'add_category_poi', 'replace_any_named'} and op['op'] in extra_operations)
            require((op['day'] is None and nullable_day) or nonempty(op['day']), 'INVALID_DAY', path)
        for field in ('poi_id', 'target_poi_id'):
            if field in op:
                require(nonempty(op[field]), 'INVALID_POI_ID', path)
        if op['op'] == 'replace_poi':
            require(isinstance(op['desired_category'], str) and op['desired_category'] in CATEGORIES, 'INVALID_CATEGORY', path)
        if op['op'] == 'set_day_pace':
            require(isinstance(op['pace'], str) and op['pace'] in PACES, 'INVALID_PACE', path)
        if op['op'] == 'set_meal_break':
            require(nonempty(op['start_at']) and nonempty(op['end_at']), 'INVALID_MEAL_BREAK', path)
    fingerprint(value)
    return deepcopy(value)

class RouteAssistant:
    def __init__(self, store, interpreter, *, proposal_ttl_seconds, clock=None, scheduler=None, validator=None, interpretation_parser=None, allow_cost_edits=False):
        require(isinstance(store, InMemoryRouteStore), 'INVALID_STORE')
        require(callable(interpreter), 'INVALID_INTERPRETER')
        require(type(proposal_ttl_seconds) is int and proposal_ttl_seconds > 0, 'INVALID_PROPOSAL_TTL')
        self.store, self.interpreter, self.ttl = store, interpreter, proposal_ttl_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.scheduler, self.validator = scheduler or schedule, validator or validate
        self.interpretation_parser = interpretation_parser or parse_interpretation
        self.allow_cost_edits = allow_cost_edits
    def _working_copy(self, base):
        """Адаптер может подготовить транзакцию, не меняя сохранённый маршрут."""
        return deepcopy(base)
    def _now(self):
        now = self.clock()
        require(isinstance(now, datetime) and now.utcoffset() is not None, 'INVALID_CLOCK')
        return now.astimezone(timezone.utc)
    @staticmethod
    def _unknown_costs(state):
        costs = state['routing_context'].get('cost_context')
        return not isinstance(costs, dict) or not costs.get('coverage_complete', False)
    def _payload(self, state, text):
        ctx = {d['date']: d for d in state['routing_context']['days']}
        days = []
        for day, ids in sequences_of(state['route']).items():
            days.append({'date': day, 'pace': state['routing_context'].get('day_pace_overrides', {}).get(day, state['preferences']['pace']), 'day_start': ctx[day]['day_start'], 'day_end': ctx[day]['day_end'], 'visits': [{'poi_id': pid} for pid in ids]})
        return {'text': text, 'days': days, 'catalog': [{'poi_id': p['id'], 'name': p['name'], 'categories': p['categories']} for p in state['pois']], 'required_poi_ids': list(state['preferences']['required_poi_ids']), 'timezone': state['preferences']['timezone']}
    @staticmethod
    def _settings(state, day):
        return {'day': next(d for d in state['routing_context']['days'] if d['date'] == day), 'pace': state['routing_context'].get('day_pace_overrides', {}).get(day, state['preferences']['pace'])}
    def _candidate(self, state, sequences):
        result = self.scheduler(state, sequences)
        require(isinstance(result, dict) and result.get('schema_version') == '0.2', 'INVALID_CANDIDATE_ROUTE')
        check_validation(self.validator(result, state))
        require(result.get('status') in ('unknown', 'feasible'), 'ROUTE_CONSTRAINT_FAILED')
        require(not has_failed(result.get('constraint_checks', {})), 'ROUTE_CONSTRAINT_FAILED')
        require(sequences_of(result) == sequences, 'ROUTE_SEQUENCE_MISMATCH')
        return result

    @staticmethod
    def _addition_score(candidate, day):
        travel = sum(leg['duration_seconds'] for item in candidate['days'] for leg in item.get('legs', [])
                     if leg.get('duration_seconds') is not None)
        end = next((item['visits'][-1].get('end_at') for item in candidate['days']
                    if item['date'] == day and item['visits']), None) or ''
        return travel, end

    def _best_addition(self, state, sequences, poi_id, days):
        choices = []
        for day in days:
            for pos in range(len(sequences[day]) + 1):
                attempt = deepcopy(sequences)
                attempt[day].insert(pos, poi_id)
                try:
                    candidate = self._candidate(state, attempt)
                except (ValueError, KeyError, TypeError, OverflowError):
                    continue
                choices.append(((*self._addition_score(candidate, day), day, pos), attempt))
        return min(choices, key=lambda c: c[0])[1] if choices else None
    def _apply(self, state, sequences, op):
        name, day = op['op'], op.get('day')
        if day is not None:
            require(day in sequences, 'UNKNOWN_DAY')
        required = set(state['preferences']['required_poi_ids'])
        by_id = {p['id']: p for p in state['pois']}
        placed = {p for ids in sequences.values() for p in ids}
        if name in ('remove_poi', 'replace_poi'):
            target = op.get('poi_id', op.get('target_poi_id'))
            require(target not in required, 'REQUIRED_PLACE_PROTECTED')
            require(target in placed, 'TARGET_NOT_IN_ROUTE')
            if name == 'remove_poi':
                for ids in sequences.values():
                    if target in ids:
                        ids.remove(target)
                return
            require(target in sequences[day], 'TARGET_DAY_MISMATCH')
            profile = deepcopy(state['preferences'])
            profile['top_k'] = max(1, len(state['pois']))
            ranking = recommend(profile, state['pois'])['items']
            replacements = [i['poi_id'] for i in ranking if i['poi_id'] not in placed and op['desired_category'] in by_id[i['poi_id']]['categories']]
            require(bool(replacements), 'NO_REPLACEMENT_CANDIDATE')
            reduced = deepcopy(sequences)
            reduced[day].remove(target)
            for replacement in replacements:
                attempt = self._best_addition(state, reduced, replacement, [day])
                if attempt is not None:
                    sequences.clear()
                    sequences.update(attempt)
                    return
            raise AssistantError('NO_FEASIBLE_REPLACEMENT')
        if name == 'add_poi':
            require(op['poi_id'] in by_id, 'UNKNOWN_POI_ID')
            require(op['poi_id'] not in placed, 'DUPLICATE_VISIT')
            attempt = self._best_addition(state, sequences, op['poi_id'], [day] if day else list(sequences))
            require(attempt is not None, 'NO_FEASIBLE_INSERTION')
            sequences.clear()
            sequences.update(attempt)
        elif name == 'set_day_pace':
            state['routing_context'].setdefault('day_pace_overrides', {})[day] = op['pace']
        elif name == 'set_meal_break':
            next(d for d in state['routing_context']['days'] if d['date'] == day)['meal_break'] = {'kind': 'meal', 'start_at': op['start_at'], 'end_at': op['end_at']}
    def propose(self, actor_id, request):
        exact(request, {'schema_version', 'route_id', 'base_version', 'poi_snapshot_id', 'text'}, 'request')
        require(request['schema_version'] == '0.2', 'UNSUPPORTED_SCHEMA_VERSION')
        require(nonempty(request['route_id']) and nonempty(request['text']) and nonempty(request['poi_snapshot_id']), 'INVALID_REQUEST')
        require(type(request['base_version']) is int and request['base_version'] >= 1, 'INVALID_VERSION')
        with self.store._lock:
            base = deepcopy(self.store._authorized(actor_id, request['route_id']))
        require(base['version'] == request['base_version'], 'VERSION_CONFLICT')
        require(base['poi_snapshot_id'] == request['poi_snapshot_id'], 'SNAPSHOT_CONFLICT')
        price_words = ('дорог', 'дешев', 'дёшев', 'стоимост', 'expensive', 'cheaper')
        if not self.allow_cost_edits and any(w in request['text'].casefold() for w in price_words):
            return {'schema_version': '0.2', 'validation_status': 'needs_clarification', 'code': 'UNKNOWN_COST' if self._unknown_costs(base) else 'COST_FILTER_NOT_SUPPORTED', 'clarification': 'Правки по стоимости требуют известных цен и отдельной поддерживаемой операции.'}
        try:
            parsed = self.interpretation_parser(self.interpreter(deepcopy(self._payload(base, request['text']))))
        except AssistantError:
            raise
        except OpenRouterError as error:
            raise AssistantError(error.code) from None
        except Exception:
            raise AssistantError('INTERPRETER_UNAVAILABLE') from None
        if parsed['action'] != 'propose':
            return {'schema_version': '0.2', 'validation_status': parsed['action'], 'clarification': parsed['clarification']}
        state, seq = self._working_copy(base), sequences_of(base['route'])
        before = deepcopy(seq)
        try:
            for op in parsed['operations']:
                self._apply(state, seq, op)
            candidate = self._candidate(state, seq)
        except AssistantError:
            raise
        except (ValueError, KeyError, TypeError, OverflowError):
            raise AssistantError('ROUTE_RECOMPUTATION_FAILED') from None
        expires = self._now() + timedelta(seconds=self.ttl)
        candidate.update(route_id=base['route_id'], version=base['version'] + 1)
        proposal_id = uuid4().hex
        proposal = {'schema_version': '0.2', 'proposal_id': proposal_id, 'route_id': base['route_id'], 'base_version': base['version'], 'poi_snapshot_id': base['poi_snapshot_id'], 'operations': parsed['operations'], 'diff': {'before': before, 'after': deepcopy(seq), 'changed_days': [d for d in seq if seq[d] != before[d] or self._settings(base, d) != self._settings(state, d)]}, 'candidate_route': candidate, 'checks': candidate.get('constraint_checks', {}), 'warnings': candidate.get('warnings', []), 'validation_status': 'ready_for_confirmation', 'requires_draft_confirmation': candidate['status'] == 'unknown', 'explanation': 'Проверены операции: ' + ', '.join(op['op'] for op in parsed['operations']) + '. Требуется подтверждение.', 'expires_at': expires.isoformat()}
        internal = {'owner': actor_id, 'proposal': deepcopy(proposal), 'state': state, 'base_fingerprint': fingerprint(base), 'sequences': seq, 'expires': expires, 'decision': None, 'result': None}
        with self.store._lock:
            require(fingerprint(self.store._authorized(actor_id, base['route_id'])) == internal['base_fingerprint'], 'VERSION_OR_DATA_CONFLICT')
            self.store._proposals[proposal_id] = internal
        return deepcopy(proposal)
    def decide(self, actor_id, request, *, allow_draft=False):
        exact(request, {'proposal_id', 'base_version', 'decision'}, 'confirmation')
        require(nonempty(request['proposal_id']) and type(request['base_version']) is int and request['base_version'] >= 1, 'INVALID_CONFIRMATION')
        require(isinstance(request['decision'], str) and request['decision'] in ('confirm', 'reject'), 'INVALID_DECISION')
        require(type(allow_draft) is bool, 'INVALID_DRAFT_CONFIRMATION')
        with self.store._lock:
            item = self.store._proposals.get(request['proposal_id'])
            require(item is not None and item['owner'] == actor_id, 'PROPOSAL_NOT_FOUND')
            proposal = item['proposal']
            require(request['base_version'] == proposal['base_version'], 'VERSION_CONFLICT')
            if item['decision'] is not None:
                require(item['decision'] == request['decision'], 'PROPOSAL_ALREADY_DECIDED')
                return deepcopy(item['result'])
            require(self._now() < item['expires'], 'PROPOSAL_EXPIRED')
            current = self.store._authorized(actor_id, proposal['route_id'])
            require(current['version'] == proposal['base_version'], 'VERSION_CONFLICT')
            require(fingerprint(current) == item['base_fingerprint'], 'INPUT_DATA_CHANGED')
            if request['decision'] == 'reject':
                result = {'schema_version': '0.2', 'decision': 'rejected', 'proposal_id': proposal['proposal_id'], 'route_id': current['route_id'], 'version': current['version']}
            else:
                require(not proposal['requires_draft_confirmation'] or allow_draft, 'DRAFT_CONFIRMATION_REQUIRED')
                try:
                    candidate = self._candidate(item['state'], item['sequences'])
                except AssistantError:
                    raise
                except (ValueError, KeyError, TypeError, OverflowError):
                    raise AssistantError('REVALIDATION_FAILED') from None
                candidate.update(route_id=proposal['route_id'], version=proposal['base_version'] + 1)
                require(candidate == proposal['candidate_route'], 'REVALIDATION_CHANGED')
                new = deepcopy(item['state'])
                new.update(route=candidate, version=candidate['version'])
                self.store._routes[new['route_id']] = new
                result = {'schema_version': '0.2', 'decision': 'confirmed', 'proposal_id': proposal['proposal_id'], 'route_id': new['route_id'], 'version': new['version'], 'route': deepcopy(candidate), 'is_draft': proposal['requires_draft_confirmation']}
            item.update(decision=request['decision'], result=deepcopy(result))
            return deepcopy(result)
