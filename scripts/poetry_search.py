"""Day 4 resumable global beam search. No model API or Day 5 polishing."""
from copy import deepcopy
from fractions import Fraction
from uuid import uuid4
import argparse
import json

from poem_state import PoemState
from poem_planning import load_plan_artifact
from constraint_engine import ConstraintEngine
from assessment_binding import digest, make_binding
from poetry_round import (export_context, filter_candidates, review_content, review_readings, finish_round,
                          require, RoundError, read_json, write_new_json,
                          _validate_context)
from runtime_workspace import configure_workspace, input_path, output_path
from round_content import export_content_plan, validate_content_plan, requirements
from poetry_unit import (CONTRACT_VERSION as UNIT_CONTRACT_VERSION, is_atomic_couplet,
                         export_unit_context, validate_unit_context,
                         filter_units, review_unit_readings, review_unit_duplicates,
                         review_unit_content, finish_units)
from reading_disambiguation import make_request as make_reading_request, apply_review as apply_reading_review
from duplicate_disambiguation import make_request as make_duplicate_request, apply_review as apply_duplicate_review
from poetry_round import review_duplicates

VERSION = 'poetry-search-10'
TERMINAL = {'ready_for_review', 'budget_exhausted', 'no_feasible_path',
            'needs_clarification', 'run_error', 'legacy_readonly'}


class Search:
    """Mutating methods transact on copies; next_job is a read-only detached export.

    Engine injection is for deterministic tests, not model-generated verdicts.
    Persist the entire to_dict() artifact; never persist only a selected poem.
    """
    def __init__(self, data, *, engine=None):
        self.engine = engine if engine is not None else ConstraintEngine()
        self.data = deepcopy(data)

    @classmethod
    def create(cls, state, *, poem_plan=None, content_plan=None, max_interactions=None,
               max_alternatives=30, max_attempts=3, engine=None):
        initial = state.to_dict()
        require(initial['status'] != 'completed', 'INVALID_INITIAL_STATE', 'Use a planned or draft state')
        require(poem_plan is not None, 'POEM_PLAN_REQUIRED',
                'new search runs require a bound poem plan')
        semantic_plan = load_plan_artifact(state, poem_plan)
        plan, unresolved = validate_content_plan(state, content_plan)
        config = {**initial['budget'], 'max_interactions': max_interactions if max_interactions is not None
                  else initial['budget']['max_rounds'] * 9 + initial['form']['line_count'] * 9,
                  'max_alternatives': max_alternatives, 'max_attempts': max_attempts}
        for k, v in config.items():
            require(type(v) is int and v >= (0 if k in ('max_backtracks', 'max_alternatives') else 1),
                    'INVALID_CONFIG', k)
        require(max_attempts <= 3, 'INVALID_CONFIG', 'Day 3 supports at most three attempts')
        initial_locks = []
        for unit in semantic_plan['structure_plan']:
            if max(unit['line_indices']) <= len(initial['lines']):
                initial_locks.append({'unit': unit['unit'], 'line_indices': deepcopy(unit['line_indices']),
                    'lock': {'protected_meaning': '；'.join(unit['protected_meaning']) or unit['primary_task'],
                             'facts': [], 'images': [], 'relations': [],
                             'emotion_direction': semantic_plan['poem_core']['primary_emotion'],
                             'structural_function': unit['primary_task'], 'confirmed_allusions': []}})
        root = {'path_id': 'p0', 'parent_path_id': None, 'state': initial,
                'depth': len(initial['lines']), 'adoptions': [], 'score': 0.0, 'content': [],
                'semantic_locks': initial_locks, 'readings': {}, 'duplicate_review': None}
        s = cls({'version': VERSION, 'run_id': uuid4().hex, 'status': 'running',
                 'initial': initial, 'poem_plan': semantic_plan,
                 'assignments': plan['assignments'], 'config': config,
                 'active': [root], 'alternatives': [], 'children': [], 'cursor': 0,
                 'pending': None, 'processed': {}, 'invalid_responses': {}, 'drafts': [], 'diagnostics': [],
                 'events': [], 'used': {'rounds': 0, 'interactions': 0, 'backtracks': 0},
                 'next_path': 1, 'next_job': 0}, engine=engine)
        if unresolved:
            s._stop('needs_clarification', 'CONTENT_ASSIGNMENT_REQUIRED')
        else:
            try:
                s._advance()
            except ValueError as exc:
                s._stop('needs_clarification', str(exc))
        return s

    def to_dict(self):
        d = deepcopy(self.data)
        return {'search': d, 'sha256': digest(d)}

    @classmethod
    def from_dict(cls, artifact, *, engine=None):
        require(isinstance(artifact, dict) and set(artifact) == {'search', 'sha256'}
                and digest(artifact['search']) == artifact['sha256'], 'INVALID_SNAPSHOT', 'Snapshot checksum mismatch')
        d = artifact['search']
        if isinstance(d, dict) and d.get('version') in ('poetry-search-1', 'poetry-search-2', 'poetry-search-3', 'poetry-search-4', 'poetry-search-5', 'poetry-search-6', 'poetry-search-7', 'poetry-search-8', 'poetry-search-9'):
            legacy = deepcopy(d)
            legacy.update(version=VERSION, poem_plan=d.get('poem_plan'), status='legacy_readonly', pending=None)
            legacy['events'].append({'code': 'POEM_PLAN_REQUIRED', 'status': 'legacy_readonly'})
            return cls(legacy, engine=engine)
        fields = {'version', 'run_id', 'status', 'initial', 'assignments', 'config', 'active',
                  'alternatives', 'children', 'cursor', 'pending', 'processed', 'invalid_responses',
                  'drafts', 'diagnostics', 'events', 'used', 'next_path', 'next_job', 'poem_plan'}
        require(isinstance(d, dict) and set(d) == fields, 'INVALID_SNAPSHOT', 'Invalid search fields')
        require(d['version'] == VERSION, 'INVALID_VERSION', 'Unsupported search version')
        require(d['status'] in TERMINAL | {'running', 'waiting_for_llm'}, 'INVALID_SNAPSHOT', 'Invalid stage')
        for key in ('active', 'alternatives', 'children', 'drafts', 'diagnostics', 'events', 'assignments'):
            require(isinstance(d[key], list), 'INVALID_SNAPSHOT', 'Expected array: ' + key)
        config_keys = {'beam_width', 'max_rounds', 'max_backtracks', 'candidates_per_round',
                       'max_alternatives', 'max_attempts', 'max_interactions'}
        require(isinstance(d['config'], dict) and set(d['config']) == config_keys,
                'INVALID_SNAPSHOT', 'Invalid configuration fields')
        for k, v in d['config'].items():
            require(type(v) is int and v >= (0 if k in ('max_backtracks', 'max_alternatives') else 1),
                    'INVALID_SNAPSHOT', 'Invalid configuration value')
        require(d['config']['max_attempts'] <= 3 and isinstance(d['used'], dict)
                and set(d['used']) == {'rounds', 'interactions', 'backtracks'},
                'INVALID_SNAPSHOT', 'Invalid budget fields')
        require(type(d['cursor']) is int and 0 <= d['cursor'] <= len(d['active'])
                and (d['pending'] is None or d['cursor'] < len(d['active'])),
                'INVALID_SNAPSHOT', 'Invalid active cursor')
        s = cls(d, engine=engine)
        initial_state = PoemState.from_dict(d['initial'])
        require(d['poem_plan'] is not None, 'POEM_PLAN_REQUIRED', 'New Search snapshots require a semantic plan')
        from poem_planning import validate_plan
        validate_plan(initial_state, d['poem_plan'])
        for p in d['active'] + d['alternatives'] + d['children'] + d['drafts'] + d['diagnostics']:
            require(isinstance(p, dict) and {'state', 'depth', 'path_id', 'parent_path_id',
                    'score', 'adoptions', 'content', 'semantic_locks', 'readings', 'duplicate_review'} <= set(p),
                    'INVALID_SNAPSHOT', 'Invalid path fields')
            state = PoemState.from_dict(p['state'])
            require(p['depth'] == len(p['state']['lines']), 'INVALID_SNAPSHOT', 'Path depth mismatch')
            validate_content_plan(state, s._plan(state))
        for key, limit in [('rounds', 'max_rounds'), ('interactions', 'max_interactions'), ('backtracks', 'max_backtracks')]:
            require(type(d['used'][key]) is int and 0 <= d['used'][key] <= d['config'][limit],
                    'INVALID_SNAPSHOT', 'Invalid consumed budget')
        if d['pending']:
            job = d['pending']
            p = s._current()
            require(job['path_id'] == p['path_id'] and job['run_id'] == d['run_id'], 'INVALID_SNAPSHOT', 'Wrong pending path')
            state = PoemState.from_dict(p['state'])
            if job['context'].get('contract_version') == UNIT_CONTRACT_VERSION:
                validate_unit_context(state, job['context'])
            else:
                _validate_context(state, job['context'])
        return s

    def save(self, path):
        write_new_json(output_path(path), self.to_dict())

    @classmethod
    def load(cls, path, **kwargs):
        return cls.from_dict(read_json(input_path(path).read_text(encoding='utf-8-sig')), **kwargs)

    def next_job(self):
        if self.data['status'] == 'legacy_readonly':
            return None
        return deepcopy(self.data['pending'])

    def next_compact_job(self):
        """Return the model-facing request without the persisted filter artifact."""
        job = self.next_job()
        if job is None:
            return None
        view = compact_job_view(job)
        feedback = next((event for event in reversed(self.data['events'])
                         if event.get('job_id') == job['job_id'] and event.get('code')),
                        None)
        if feedback:
            view['repair_feedback'] = {k: deepcopy(feedback[k]) for k in
                                       ('code', 'message', 'details') if k in feedback}
        return view

    def _current(self):
        return self.data['active'][self.data['cursor']]

    def _plan(self, state):
        plan = export_content_plan(state)
        plan['assignments'] = deepcopy(self.data['assignments'])
        return plan

    def _stop(self, status, code):
        self.data['status'] = status
        self.data['events'].append({'code': code, 'status': status})

    def _job(self, stage, context, filtered=None, final_index=None, reading_request=None, duplicate_request=None):
        d = self.data
        if d['used']['interactions'] >= d['config']['max_interactions']:
            self._stop('budget_exhausted', 'MAX_INTERACTIONS')
            return
        d['next_job'] += 1
        d['used']['interactions'] += 1  # issued work, including format repair, is charged once
        d['pending'] = {'run_id': d['run_id'], 'job_id': f"j{d['next_job']}",
                        'path_id': self._current()['path_id'], 'batch_id': context['batch_id'],
                        'context_sha256': context['context_sha256'], 'stage': stage, 'attempt': 1,
                        'context': deepcopy(context), 'filtered': deepcopy(filtered),
                        'final_index': final_index, 'reading_request': deepcopy(reading_request),
                        'duplicate_request': deepcopy(duplicate_request)}
        d['status'] = 'waiting_for_llm'

    @staticmethod
    def _rank(paths):
        return sorted(paths, key=lambda p: (-p['score'], int(p['path_id'][1:])))

    def _advance(self):
        d = self.data
        while d['status'] not in TERMINAL and d['pending'] is None:
            if d['cursor'] == len(d['active']):
                if d['drafts']:
                    self._stop('ready_for_review', 'DRAFT_RULES_AND_CONTENT_PASSED')
                    return
                ranked = self._rank(d['children'])
                keep = d['config']['beam_width']
                for p in ranked[keep:]:
                    d['events'].append({'code': 'GLOBAL_BEAM_PRUNED', 'path_id': p['path_id'], 'depth': p['depth']})
                d['alternatives'].extend(ranked[keep:])
                # Prefer recent/deeper checkpoints; scores compete only within a depth.
                d['alternatives'].sort(key=lambda p: (-p['depth'], -p['score'], int(p['path_id'][1:])))
                dropped = d['alternatives'][d['config']['max_alternatives']:]
                d['events'].extend({'code': 'ALTERNATIVE_STORAGE_LIMIT', 'path_id': p['path_id']} for p in dropped)
                d['alternatives'] = d['alternatives'][:d['config']['max_alternatives']]
                d['active'], d['children'], d['cursor'] = ranked[:keep], [], 0
                if not d['active']:
                    if d['alternatives'] and d['used']['backtracks'] < d['config']['max_backtracks']:
                        restored = d['alternatives'].pop(0)
                        d['active'] = [restored]
                        d['used']['backtracks'] += 1
                        d['events'].append({'code': 'BRANCH_RESTORED', 'path_id': restored['path_id'],
                                            'parent_path_id': restored['parent_path_id'], 'depth': restored['depth']})
                    else:
                        self._stop('no_feasible_path', 'BACKTRACK_LIMIT' if d['alternatives'] else 'PATHS_EXHAUSTED')
                        return
            p = self._current()
            state = PoemState.from_dict(p['state'])
            if p['depth'] == p['state']['form']['line_count']:
                checks = self.engine.check_poem(state, readings=p.get('readings'),
                                                 duplicate_review=p.get('duplicate_review'))
                state.set_assessment(checks)
                p['state'] = state.to_dict()
                if checks['valid'] is None and checks['checks']['reading']['status'] == 'unknown':
                    context = export_context(state, 1, content_plan=self._plan(state),
                                             poem_plan=d['poem_plan'])
                    self._job('poem_reading', context, reading_request=make_reading_request(checks))
                    continue
                if checks['valid'] is None and checks['checks']['duplicate']['status'] == 'unknown':
                    context = export_context(state, 1, content_plan=self._plan(state),
                                             poem_plan=d['poem_plan'])
                    self._job('poem_duplicate', context, duplicate_request=make_duplicate_request(checks))
                    continue
                if checks['valid'] is not True:
                    self._fail('FINAL_RULES_FAILED_OR_UNKNOWN')
                    continue
                p['final_content'] = []
                self._final_content(1)
                continue
            if d['used']['rounds'] >= d['config']['max_rounds']:
                self._stop('budget_exhausted', 'MAX_ROUNDS')
                return
            if is_atomic_couplet(state):
                context = export_unit_context(state, d['poem_plan'], self._plan(state))
                d['used']['rounds'] += 1
                self._job('unit_generate', context)
                continue
            context = export_context(state, p['depth'] + 1, content_plan=self._plan(state),
                                     poem_plan=d['poem_plan'])
            if context['status'] != 'ready':
                self._stop('needs_clarification', context['reasons'])
                return
            d['used']['rounds'] += 1
            if context['locked_candidate'] is not None:
                filtered = filter_candidates(state, context, engine=self.engine,
                                             readings=p.get('readings'))
                self._after_filter(context, filtered)
            else:
                self._job('generate', context)

    def _fail(self, code):
        p = self._current()
        self.data['diagnostics'].append(deepcopy(p))
        self.data['events'].append({'code': code, 'path_id': p['path_id']})
        self.data['cursor'] += 1

    def _after_filter(self, context, filtered):
        self._current()['round_artifact'] = deepcopy(filtered)
        if filtered.get('reading_request', {}).get('reviews') and filtered.get('reading_input') is None:
            self._job('reading', context, filtered)
        elif filtered.get('duplicate_request', {}).get('reviews') and filtered.get('duplicate_input') is None:
            self._job('duplicate', context, filtered)
        elif filtered.get('content_request', {}).get('candidates'):
            self._job('content', context, filtered)
        elif filtered['score_request']['candidates']:
            self._job('score', context, filtered)
        else:
            self._commit_round(filtered, None)

    def _commit_round(self, filtered, scores):
        p = self._current()
        parent = PoemState.from_dict(p['state'])
        report = finish_round(parent, filtered, scores, engine=self.engine)
        if filtered['context'].get('planned_unit') and scores is not None:
            from poetry_round import require_literary_quotes
            records = {r['id']: r['text'] for r in filtered['records']}
            for i, item in enumerate(scores['scores']):
                if item['evaluation'] is not None:
                    require_literary_quotes(item['evaluation']['reason'], [records[item['id']]],
                                            f'scores[{i}].evaluation.reason')
        self._record_comparison(filtered, report)
        if report['status'] == 'requires_user_adjustment':
            self._stop('needs_clarification', report['code'])
            return
        for cid in report['ranking']:
            r = next(r for r in report['records'] if r['id'] == cid)
            child = parent.fork()
            child.append_line(r['text'])
            adoptions = deepcopy(p['adoptions']) + [{'record': deepcopy(r), 'report': deepcopy(report),
                                                    'parent_path_id': p['path_id'], 'parent_state': deepcopy(p['state'])}]
            score = float(sum(Fraction(str(x['record']['quality_score'])) for x in adoptions) / len(adoptions))
            child_path = {'path_id': f"p{self.data['next_path']}", 'parent_path_id': p['path_id'],
                          'state': child.to_dict(), 'depth': p['depth'] + 1, 'adoptions': adoptions,
                          'score': score, 'content': deepcopy(r.get('content_result', {})),
                          'semantic_locks': deepcopy(p.get('semantic_locks', [])) + ([{
                              'unit': filtered['context']['planned_unit']['unit'],
                              'line_indices': [r['line_index']],
                              'lock': deepcopy(r['semantic_lock'])}]
                              if r.get('semantic_lock') else []),
                          'readings': {**deepcopy(p.get('readings', {})),
                                       **deepcopy(r.get('reading_review', {}).get('readings', {}))},
                          'duplicate_review': None}
            self.data['next_path'] += 1
            self.data['children'].append(child_path)
        if not report['ranking']:
            p['failed_report'] = report
            self._fail(report['code'])
        else:
            self.data['cursor'] += 1

    def _after_unit_filter(self, filtered):
        self._current()['round_artifact'] = deepcopy(filtered)
        if filtered['status'] == 'replan_required':
            self._current()['replan'] = deepcopy(filtered['replan'])
            self._stop('needs_clarification', 'REPLAN_REQUIRED')
        elif filtered['reading_request']['requests'] and filtered.get('reading_input') is None:
            self._job('unit_reading', filtered['context'], filtered)
        elif filtered['duplicate_request']['requests'] and filtered.get('duplicate_input') is None:
            self._job('unit_duplicate', filtered['context'], filtered)
        elif filtered['content_request']['requests']:
            self._job('unit_content', filtered['context'], filtered)
        elif filtered['score_request']['candidates']:
            self._job('unit_score', filtered['context'], filtered)
        else:
            self._record_comparison(filtered)
            self._fail('NO_PASSED_UNIT')

    def _commit_unit(self, filtered, scores):
        p = self._current()
        report = finish_units(filtered, scores)
        from poetry_round import require_literary_quotes
        records = {r['id']: [line['text'] for line in r['lines']] for r in filtered['records']}
        for i, item in enumerate(scores['scores']):
            require_literary_quotes(item['reason'], records[item['id']], f'scores[{i}].reason')
        self._record_comparison(filtered, report)
        for candidate_id in report['ranking']:
            record = next(x for x in report['records'] if x['id'] == candidate_id)
            child = PoemState.from_dict(p['state'])
            texts = [x['text'] for x in record['lines']]
            child.append_unit(texts)
            adoptions = deepcopy(p['adoptions']) + [{
                'record': deepcopy(record), 'report': deepcopy(report),
                'parent_path_id': p['path_id'], 'parent_state': deepcopy(p['state'])}]
            score = float(sum(Fraction(str(x['record']['quality_score'])) for x in adoptions) / len(adoptions))
            child_path = {'path_id': f"p{self.data['next_path']}", 'parent_path_id': p['path_id'],
                          'state': child.to_dict(), 'depth': p['depth'] + 2, 'adoptions': adoptions,
                          'score': score, 'content': [deepcopy(f['records'][0].get('content_result', {}))
                                                     for f in record['line_filters']],
                          'semantic_locks': deepcopy(p.get('semantic_locks', [])) + [{
                              'unit': filtered['context']['unit_name'],
                              'line_indices': deepcopy(filtered['context']['line_indices']),
                              'lock': deepcopy(record['semantic_lock'])}],
                          'readings': {**deepcopy(p.get('readings', {})),
                                       **{coordinate: selector
                                          for line_filter in record['line_filters']
                                          for coordinate, selector in line_filter['records'][0]
                                          .get('reading_review', {}).get('readings', {}).items()}},
                          'duplicate_review': None}
            self.data['next_path'] += 1
            self.data['children'].append(child_path)
        if report['ranking']:
            self.data['cursor'] += 1
        else:
            p['failed_report'] = report
            self._fail(report['code'])

    def _final_content(self, index):
        """Re-run Day 3 content checks on unchanged complete text, at every assigned line.

        No historical semantic verdict is promoted. No full-poem literary review.
        """
        p = self._current()
        state = PoemState.from_dict(p['state'])
        if not requirements(p['state']['request']):
            index = p['depth'] + 1
        while index <= p['depth']:
            c = export_context(state, index, content_plan=self._plan(state),
                               poem_plan=self.data['poem_plan'])
            if c['locked_candidate'] and c['locked_candidate']['text'] != p['state']['lines'][index-1]:
                self._fail('FINAL_LOCKED_TEXT_MISMATCH')
                return
            from poetry_round import ENVELOPE
            payload = {**{k: c[k] for k in ENVELOPE}, 'attempt': 1,
                       'candidates': [{'id': 'final', 'text': p['state']['lines'][index-1], 'line_index': index}]}
            f = filter_candidates(state, c, None if c['locked_candidate'] else payload,
                                  engine=self.engine, readings=p.get('readings'))
            if f.get('duplicate_request', {}).get('reviews'):
                self._job('final_duplicate', c, f, index)
                return
            if f.get('reading_request', {}).get('reviews'):
                self._job('final_reading', c, f, index)
                return
            if f.get('content_request', {}).get('candidates'):
                self._job('final_content', c, f, index)
                return
            p['final_content'].append(f)
            index += 1
        due = [r for f in p['final_content'] for r in f['records'][0]['content_result']['requirements'] if r['blocking']]
        expected = sum(item['required'] if kind == 'allusion' else item['mode'] != 'inspiration'
                       for kind, item in requirements(p['state']['request']))
        if len(due) != expected or any(r['status'] != 'satisfied' for r in due):
            self._fail('FINAL_CONTENT_FAILED_OR_UNKNOWN')
            return
        state.set_status('reviewing') if p['state']['status'] != 'reviewing' else None
        p['state'] = state.to_dict()
        p['final_content_binding'] = make_binding(p['state'])
        self.data['drafts'].append(deepcopy(p))
        self.data['cursor'] += 1
        self._stop('ready_for_review', 'DRAFT_RULES_AND_CONTENT_PASSED')

    def submit(self, response):
        """Response = job identity fields + payload (the unchanged Day 3 JSON).

        Wrong identity is rejected without mutation. Validly addressed malformed model
        payloads consume a bounded repair issuance, never commit partial children.
        Replays of successful jobs return idempotently; changed replays are rejected.
        """
        require(self.data['status'] != 'legacy_readonly', 'POEM_PLAN_REQUIRED',
                'Legacy snapshots are read-only; start a new planned search')
        require(isinstance(response, dict), 'INVALID_RESPONSE', 'Expected response object')
        identity = ('run_id', 'job_id', 'path_id', 'batch_id', 'context_sha256')
        require(set(response) == set(identity) | {'payload'}, 'INVALID_RESPONSE', 'Invalid response fields')
        require(all(isinstance(response[k], str) and response[k] for k in identity),
                'INVALID_RESPONSE', 'Work identity fields must be nonempty strings')
        response_digest = digest(response)
        if response_digest in self.data['invalid_responses']:
            return {'status': 'already_rejected', 'error': self.data['invalid_responses'][response_digest]}
        old = self.data['processed'].get(response['job_id'])
        if old is not None:
            require(old == digest(response), 'CHANGED_REPLAY', 'Processed result differs')
            return {'status': 'already_processed'}
        job = self.data['pending']
        require(job is not None and self.data['status'] == 'waiting_for_llm', 'NO_PENDING_JOB', 'No pending work')
        require(all(response[k] == job[k] for k in identity), 'WRONG_JOB', 'Result belongs to another job/path/batch')
        model_payload = response['payload']
        if isinstance(model_payload, dict):
            model_payload = {k: v for k, v in model_payload.items() if k != 'attempt'}
        repair_key = 'repair:' + digest({**{k: response[k] for k in identity}, 'payload': model_payload})
        if job['attempt'] > 1 and repair_key in self.data['invalid_responses']:
            return {'status': 'already_rejected', 'error': self.data['invalid_responses'][repair_key]}
        staged = Search(self.data, engine=self.engine)
        staged.data['pending'] = None
        staged.data['status'] = 'running'
        try:
            current_state = PoemState.from_dict(staged._current()['state'])
            if job['context'].get('contract_version') == UNIT_CONTRACT_VERSION:
                validate_unit_context(current_state, job['context'])
            else:
                _validate_context(current_state, job['context'])
            payload = response['payload']
            if isinstance(payload, dict) and set(payload) == {'attempt', 'raw_json'}:
                require(payload['attempt'] == job['attempt'] and isinstance(payload['raw_json'], str),
                        'WRONG_ATTEMPT', 'Raw model text needs the issued attempt')
                payload = read_json(payload['raw_json'])
            if isinstance(payload, str):
                payload = read_json(payload)
            require(isinstance(payload, dict) and payload.get('attempt') == job['attempt'], 'WRONG_ATTEMPT', 'Use the issued attempt')
            staged._receive(job, payload)
        except ValueError as exc:
            # Discard all staged candidate/history mutations, retain only repair audit.
            d = deepcopy(self.data)
            event = {'code': getattr(exc, 'code', 'INVALID_MODEL_RESULT'),
                     'job_id': job['job_id'], 'message': str(exc)}
            if getattr(exc, 'details', None):
                event['details'] = deepcopy(exc.details)
            d['events'].append(event)
            if job['attempt'] >= d['config']['max_attempts']:
                d['pending'] = None
                failed = Search(d, engine=self.engine)
                failed.data['status'] = 'running'
                failed._fail('FORMAT_RETRIES_EXHAUSTED')
                failed._advance()
                self.data = failed.data
            elif d['used']['interactions'] >= d['config']['max_interactions']:
                d['status'] = 'budget_exhausted'
                d['events'].append({'code': 'MAX_INTERACTIONS'})
                self.data = d
            else:
                d['pending']['attempt'] += 1
                d['used']['interactions'] += 1
                self.data = d
            self.data['invalid_responses'][response_digest] = getattr(exc, 'code', 'INVALID_MODEL_RESULT')
            if getattr(exc, 'code', None) != 'WRONG_ATTEMPT':
                self.data['invalid_responses'][repair_key] = event['code']
            return {'status': self.data['status'], 'error': getattr(exc, 'code', 'INVALID_MODEL_RESULT'),
                    'feedback': {k: deepcopy(event[k]) for k in ('code', 'message', 'details') if k in event}}
        except Exception as exc:
            self._stop('run_error', str(exc))
            return {'status': 'run_error'}
        staged.data['processed'][job['job_id']] = digest(response)
        try:
            staged._advance()
        except Exception as exc:
            staged._stop('run_error', str(exc))
        self.data = staged.data
        return {'status': self.data['status']}

    def comparison_summary(self):
        """Count completed candidate comparisons from the persisted audit trail."""
        steps = [deepcopy(e) for e in self.data['events'] if e['code'] == 'CANDIDATE_COMPARISON']
        completed = {e['job_id'] for e in steps}
        pending = [deepcopy(e) for e in self.data['events'] if e['code'] == 'CANDIDATES_SUBMITTED'
                   and e['job_id'] not in completed]
        return {'steps': steps, 'unranked_submissions': pending,
                'comparison_count': sum(e['compared'] for e in steps)}

    def _record_submission(self, job, filtered):
        count = len(filtered['records'])
        self.data['events'].append({'code': 'CANDIDATES_SUBMITTED', 'job_id': job['job_id'],
                                   'batch_id': job['batch_id'], 'path_id': job['path_id'],
                                   'submitted': count})

    def _record_comparison(self, filtered, report=None):
        batch_id = filtered['context']['batch_id']
        source = next((e for e in reversed(self.data['events'])
                       if e['code'] == 'CANDIDATES_SUBMITTED' and e['batch_id'] == batch_id), None)
        if source is None or any(e['code'] == 'CANDIDATE_COMPARISON' and e['batch_id'] == batch_id
                                 for e in self.data['events']):
            return
        count = len(report['ranking']) if report else 0
        self.data['events'].append({**source, 'code': 'CANDIDATE_COMPARISON',
                                   'comparable': count, 'compared': count >= 2})

    def _receive(self, job, payload):
        state = PoemState.from_dict(self._current()['state'])
        stage, c, f = job['stage'], job['context'], job['filtered']
        if stage == 'unit_generate':
            filtered = filter_units(state, c, payload, engine=self.engine)
            if filtered['status'] != 'replan_required':
                self._record_submission(job, filtered)
            self._after_unit_filter(filtered)
        elif stage == 'poem_duplicate':
            require(set(payload) == {'attempt', 'review'} and payload['attempt'] == job['attempt'],
                    'INVALID_POEM_DUPLICATE', 'Use issued attempt and duplicate review')
            review = apply_duplicate_review(job['duplicate_request'], payload['review'])
            if not review['complete']:
                self._fail('FINAL_DUPLICATE_UNRESOLVED')
            else:
                self._current()['duplicate_review'] = review
        elif stage == 'final_duplicate':
            reviewed = review_duplicates(state, f, payload, engine=self.engine)
            if reviewed.get('reading_request', {}).get('reviews'):
                self._job('final_reading', c, reviewed, job['final_index'])
            elif reviewed.get('content_request', {}).get('candidates'):
                self._job('final_content', c, reviewed, job['final_index'])
            else:
                self._current()['final_content'].append(reviewed)
                self._final_content(job['final_index'] + 1)
        elif stage == 'final_reading':
            reviewed = review_readings(state, f, payload, engine=self.engine)
            if reviewed.get('duplicate_request', {}).get('reviews') and reviewed.get('duplicate_input') is None:
                self._job('final_duplicate', c, reviewed, job['final_index'])
            elif reviewed.get('content_request', {}).get('candidates'):
                self._job('final_content', c, reviewed, job['final_index'])
            else:
                self._current()['final_content'].append(reviewed)
                self._final_content(job['final_index'] + 1)
        elif stage == 'poem_reading':
            require(set(payload) == {'attempt', 'review'} and payload['attempt'] == job['attempt'],
                    'INVALID_POEM_READING', 'Use the issued attempt and reading review')
            review = apply_reading_review(job['reading_request'], payload['review'])
            if not review['complete']:
                self._fail('FINAL_READING_UNRESOLVED')
            else:
                self._current()['readings'].update(review['readings'])
        elif stage == 'duplicate':
            self._after_filter(c, review_duplicates(state, f, payload, engine=self.engine))
        elif stage == 'unit_reading':
            self._after_unit_filter(review_unit_readings(state, f, payload, engine=self.engine))
        elif stage == 'unit_duplicate':
            self._after_unit_filter(review_unit_duplicates(state, f, payload, engine=self.engine))
        elif stage == 'unit_content':
            reviewed = review_unit_content(state, f, payload, engine=self.engine)
            if reviewed['score_request']['candidates']:
                self._job('unit_score', c, reviewed)
            else:
                self._record_comparison(reviewed)
                self._fail('UNIT_CONTENT_FAILED_OR_UNKNOWN')
        elif stage == 'unit_score':
            self._commit_unit(f, payload)
        elif stage == 'generate':
            if c.get('planned_unit') is not None and c.get('locked_candidate') is None:
                enhanced = {'id', 'text', 'line_index', 'expression_path', 'semantic_increment',
                            'risks', 'semantic_lock'}
                base = {'id', 'text', 'line_index'}
                for index, item in enumerate(payload.get('candidates', [])):
                    if isinstance(item, dict) and base <= set(item) and not enhanced <= set(item):
                        missing = sorted(enhanced - set(item))
                        raise RoundError('SEMANTIC_LOCK_REQUIRED',
                                         f'Candidate {index} is missing planned generation fields: {missing}',
                                         [{'candidate_index': index, 'missing_fields': missing,
                                           'unexpected_fields': sorted(set(item) - enhanced),
                                           'expected_fields': sorted(enhanced)}])
            filtered = filter_candidates(state, c, payload, engine=self.engine,
                                         readings=self._current().get('readings'))
            self._record_submission(job, filtered)
            self._after_filter(c, filtered)
        elif stage == 'reading':
            self._after_filter(c, review_readings(state, f, payload, engine=self.engine))
        elif stage in ('content', 'final_content'):
            reviewed = review_content(state, f, payload, engine=self.engine)
            if stage == 'final_content':
                self._current()['final_content'].append(reviewed)
                self._final_content(job['final_index'] + 1)
            elif reviewed['score_request']['candidates']:
                self._job('score', c, reviewed)
            else:
                self._commit_round(reviewed, None)
        elif stage == 'score':
            self._commit_round(f, payload)
        else:
            raise RoundError('INVALID_STAGE', 'Unknown search work stage')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'next', 'submit'])
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--content-plan')
    parser.add_argument('--poem-plan')
    parser.add_argument('--response')
    parser.add_argument('--work-dir')
    parser.add_argument('--compact', action='store_true',
                        help='export a model-facing job view without redundant filter data')
    args = parser.parse_args()
    if args.compact and args.command != 'next':
        parser.error('--compact is only valid with next')
    configure_workspace(args.work_dir)
    def load(path):
        return read_json(input_path(path).read_text(encoding='utf-8-sig'))
    if args.command == 'start':
        if not args.poem_plan:
            raise ValueError('--poem-plan is required for new searches')
        s = Search.create(PoemState.from_dict(load(args.input)), poem_plan=load(args.poem_plan),
                          content_plan=load(args.content_plan) if args.content_plan else None)
    else:
        s = Search.from_dict(load(args.input))
        if args.command == 'submit':
            s.submit(load(args.response))
    output = (s.next_compact_job() if args.compact else s.next_job()) if args.command == 'next' else s.to_dict()
    write_new_json(output_path(args.output), output)


def compact_job_view(job):
    """Project a full Search job to the context and request needed for its stage."""
    if job is None:
        return None
    result = {key: deepcopy(value) for key, value in job.items()
              if key not in ('filtered', 'reading_request', 'duplicate_request')}
    stage = job['stage']
    request_key = {
        'reading': 'reading_request', 'unit_reading': 'reading_request',
        'final_reading': 'reading_request',
        'duplicate': 'duplicate_request', 'unit_duplicate': 'duplicate_request',
        'final_duplicate': 'duplicate_request',
        'content': 'content_request', 'unit_content': 'content_request',
        'final_content': 'content_request',
        'score': 'score_request', 'unit_score': 'score_request',
    }.get(stage)
    if stage in ('generate', 'unit_generate'):
        result['generation_guidance'] = (
            '当目标候选数至少为二时，尽量提交至少两条实质不同的表达路径：'
            '可改变入题角度、意象组织或叙述动作；只换近义词不算。资源不足时允许单候选，不自动重试。')
    if stage in ('score', 'unit_score'):
        result['evidence_guidance'] = (
            '评分 reason 必须用「原文」引用当前候选内的字词，解释如何兑现 planned_unit.content_scope。'
            '若没有兑现，引用造成偏离的原文并降低任务分；生成阶段发现规划无法实现时用整联 replan_required，'
            '不要在采用后回改规划。对仗逐字与逐结构分别评价；句法相同不自动扣分，语义动作或视角重复且妨碍推进才扣分。')
    if request_key:
        request = job.get(request_key)
        if request is None and isinstance(job.get('filtered'), dict):
            request = job['filtered'].get(request_key)
        if request is not None:
            result['request'] = deepcopy(request)
    elif stage == 'poem_reading' and job.get('reading_request') is not None:
        result['request'] = deepcopy(job['reading_request'])
    elif stage == 'poem_duplicate' and job.get('duplicate_request') is not None:
        result['request'] = deepcopy(job['duplicate_request'])
    return result


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        import sys
        error = exc.to_dict() if isinstance(exc, RoundError) else {
            'status': 'error', 'code': 'INVALID_INPUT', 'message': str(exc)}
        print(json.dumps(error, ensure_ascii=True), file=sys.stderr)
        raise SystemExit(2) from exc
