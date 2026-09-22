"""Day 5: bounded whole-poem review and transactional local revision, via JSON jobs."""
from copy import deepcopy
from fractions import Fraction
from uuid import uuid4
import argparse
import math

from assessment_binding import digest, make_binding, require_binding
from poem_state import PoemState
from constraint_engine import ConstraintEngine
from poetry_search import Search
from poetry_round import (require, read_json, write_new_json, ENVELOPE,
                          _context, _verify_filter, filter_candidates, review_content)
from round_content import export_content_plan, validate_content_plan, requirements, _exact
from runtime_workspace import configure_workspace, input_path, output_path

VERSION = 'poetry-polish-2'
REVIEW_VERSION = 'whole-poem-review-1'
REDLINE_VERSION = 'final-redline-1'
REDLINE_CHECKS = ('user_facts_reversed', 'allusion_facts_reversed',
                  'meaning_sacrificed_for_rhyme', 'scene_identity_tone_mismatch')
WEIGHTS = dict(theme=.20, naturalness=.20, imagery=.15, structure=.15,
               consistency=.10, expression=.10, parallelism=.10)
IDENTITY = ('contract_version', 'run_id', 'job_id', 'path_id', 'revision',
            'binding', 'work_sha256', 'attempt')


def fields(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys), 'INVALID_FIELDS', str(keys))


def text(value):
    return isinstance(value, str) and bool(value.strip())


def quotes(items, state):
    require(isinstance(items, list) and bool(items), 'INVALID_QUOTES', 'Quote the relevant poem lines')
    seen = set()
    for item in items:
        fields(item, ('line_index', 'quote'))
        i = item['line_index']
        require(type(i) is int and 1 <= i <= len(state['lines']) and i not in seen,
                'INVALID_POSITION', 'Unique, existing one-based positions required')
        require(text(item['quote']) and item['quote'] in state['lines'][i-1],
                'QUOTE_MISMATCH', 'Quote must occur verbatim at its position')
        seen.add(i)
    return seen


def validate_review(payload, state):
    """Model reasons are evidence to inspect, not independently proven literary truth."""
    fields(payload, ('review_version', 'binding', 'revision', 'dimensions', 'issues',
                     'uncertainties', 'recommend_revision', 'conclusion', 'user_constraints'))
    require(payload['review_version'] == REVIEW_VERSION and
            type(payload['revision']) is int and payload['revision'] == state['revision'],
            'STALE_REVIEW', 'Review version/revision mismatch')
    require(payload['binding'] == make_binding(state), 'STALE_REVIEW', 'Review object/context mismatch')
    dims = payload['dimensions']
    fields(dims, WEIGHTS)
    scores = {}
    for name, entry in dims.items():
        fields(entry, ('status', 'score', 'reason', 'couplets'))
        require(text(entry['reason']), 'MISSING_REASON', name)
        na = name == 'parallelism' and state['form']['line_count'] == 4
        require(entry['status'] == ('not_applicable' if na else 'assessed'),
                'DIMENSION_STATUS', name)
        if na:
            require(entry['score'] is None, 'NA_SCORE', 'Not applicable is excluded, never given a high score')
        else:
            v = entry['score']
            require(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1,
                    'INVALID_SCORE', name)
            scores[name] = v
        if name == 'parallelism' and not na:
            require(isinstance(entry['couplets'], list) and len(entry['couplets']) == 2,
                    'PARALLELISM_REQUIRED', 'Review both middle couplets')
            for c, positions in zip(entry['couplets'], ([3, 4], [5, 6])):
                fields(c, ('line_indices', 'semantic', 'syntax'))
                require(c['line_indices'] == positions and text(c['semantic']) and text(c['syntax']),
                        'PARALLELISM_REQUIRED', 'Both semantic and syntactic reasons required')
        else:
            require(entry['couplets'] == [], 'INVALID_COUPLETS', name)
    require(isinstance(payload['issues'], list), 'INVALID_ISSUES', 'issues must be an array')
    ids = set()
    for issue in payload['issues']:
        fields(issue, ('id', 'dimension', 'locations', 'severity', 'suggestion', 'requires_user_change'))
        require(text(issue['id']) and issue['id'] not in ids, 'INVALID_ISSUE_ID', 'Unique issue IDs required')
        ids.add(issue['id'])
        require(isinstance(issue['dimension'], str) and issue['dimension'] in scores,
                'INVALID_ISSUE_DIMENSION', 'Issue must concern an applicable dimension')
        quotes(issue['locations'], state)
        require(issue['severity'] in ('minor', 'major', 'blocking') and text(issue['suggestion'])
                and type(issue['requires_user_change']) is bool, 'INVALID_ISSUE', 'Severity, suggestion and lock impact required')
        if issue['severity'] != 'minor':
            require(scores[issue['dimension']] < (.6 if issue['severity'] == 'blocking' else .75),
                    'ISSUE_SCORE_CONFLICT', 'Major/blocking issue conflicts with high score')
    for name, score in scores.items():
        if score < .6:
            require(any(x['dimension'] == name and x['severity'] != 'minor' for x in payload['issues']),
                    'MISSING_SCORE_ISSUE', 'Weak dimension requires a concrete issue')
    require(isinstance(payload['uncertainties'], list), 'INVALID_UNCERTAINTIES', 'Expected array')
    for u in payload['uncertainties']:
        fields(u, ('reason', 'blocking'))
        require(text(u['reason']) and type(u['blocking']) is bool, 'INVALID_UNCERTAINTY', 'Explain uncertainty')
    constraints = state['request']['constraints']
    require(isinstance(payload['user_constraints'], list) and len(payload['user_constraints']) == len(constraints),
            'MISSING_USER_CONSTRAINT', 'Review every original free-text user constraint')
    for item, original in zip(payload['user_constraints'], constraints):
        fields(item, ('constraint', 'status', 'reason', 'evidence'))
        require(item['constraint'] == original and item['status'] in ('satisfied', 'violated', 'unknown')
                and text(item['reason']), 'INVALID_USER_CONSTRAINT', 'Original constraint and explicit verdict required')
        quotes(item['evidence'], state)
    revise = payload['recommend_revision']
    require(type(revise) is bool and (not revise or bool(ids)), 'REVISION_CONFLICT', 'Revision needs concrete issues')
    clarification = any(x['requires_user_change'] for x in payload['issues'])
    blocked = (any(x['blocking'] for x in payload['uncertainties']) or
               any(x['status'] != 'satisfied' for x in payload['user_constraints']))
    major = any(x['severity'] != 'minor' for x in payload['issues'])
    require(not major or revise or clarification, 'REVISION_CONFLICT', 'Major issues cannot be declared ready')
    expected = 'needs_clarification' if clarification else 'unresolved' if blocked else 'revise' if revise else 'ready'
    require(payload['conclusion'] == expected, 'CONCLUSION_CONFLICT', expected)
    total = sum(Fraction(str(scores[k])) * Fraction(str(WEIGHTS[k])) for k in scores)
    total /= sum(Fraction(str(WEIGHTS[k])) for k in scores)
    return {**deepcopy(payload), 'total': float(total)}


def validate_redline(payload, state):
    """Require an explicit, text-bound verdict for every final semantic red line."""
    fields(payload, ('redline_version', 'binding', 'revision', 'checks', 'conclusion'))
    require(payload['redline_version'] == REDLINE_VERSION and
            type(payload['revision']) is int and payload['revision'] == state['revision'],
            'STALE_REDLINE', 'Red-line version/revision mismatch')
    require(payload['binding'] == make_binding(state), 'STALE_REDLINE',
            'Red-line object/context mismatch')
    fields(payload['checks'], REDLINE_CHECKS)
    answers = []
    for name, item in payload['checks'].items():
        fields(item, ('answer', 'reason', 'evidence'))
        require(item['answer'] in ('yes', 'no', 'unknown') and text(item['reason']),
                'INVALID_REDLINE', name)
        quotes(item['evidence'], state)
        answers.append(item['answer'])
    expected = 'pass' if all(answer == 'no' for answer in answers) else 'blocked'
    require(payload['conclusion'] == expected, 'REDLINE_CONCLUSION_CONFLICT', expected)
    return deepcopy(payload)


class Polish:
    """Persist complete snapshots. Model jobs are read-only; submit transacts on a copy."""
    def __init__(self, data, engine):
        self.data, self.engine = deepcopy(data), engine

    @classmethod
    def create(cls, snapshot, path_id, *, engine=None, max_rounds=3,
               candidates_per_round=3, max_interactions=40, max_format_repairs=2):
        return cls._start(snapshot, path_id, engine or ConstraintEngine(), uuid4().hex,
                          dict(max_rounds=max_rounds, candidates_per_round=candidates_per_round,
                               max_interactions=max_interactions, max_format_repairs=max_format_repairs))

    @classmethod
    def _start(cls, snapshot, path_id, engine, run_id, config):
        fields(config, ('max_rounds', 'candidates_per_round', 'max_interactions', 'max_format_repairs'))
        for k, v in config.items():
            require(type(v) is int and v >= (0 if k in ('max_rounds', 'max_format_repairs') else 1),
                    'INVALID_CONFIG', k)
        require(config['max_format_repairs'] <= 2, 'INVALID_CONFIG', 'At most two format repairs')
        search = Search.from_dict(snapshot, engine=engine)
        require(search.data['status'] == 'ready_for_review', 'INVALID_HANDOFF', 'Search must be ready_for_review')
        require(text(path_id), 'DRAFT_SELECTION_REQUIRED', 'Explicit path_id required')
        paths = [p for p in search.data['drafts'] if p['path_id'] == path_id]
        require(len(paths) == 1, 'INVALID_DRAFT', 'Select exactly one existing draft')
        p = paths[0]
        state = PoemState.from_dict(p['state'])
        d = state.to_dict()
        require(d['status'] == 'reviewing' and len(d['lines']) == d['form']['line_count'],
                'INVALID_HANDOFF', 'Complete reviewing poem required')
        for key in ('request', 'poem_type', 'form', 'template_id', 'meter_template', 'rhyme',
                    'constraints', 'theme', 'emotion', 'imagery', 'structure'):
            require(d.get(key) == search.data['initial'].get(key), 'HANDOFF_CONTEXT_CHANGED', key)
        require(p.get('final_content_binding') == make_binding(d), 'INVALID_HANDOFF', 'Content binding missing or stale')
        require_binding(d['checks'], d)
        require(d['checks'].get('revision') == d['revision'] and d['checks'].get('valid') is True,
                'INVALID_HANDOFF', 'Current Day 4 checks required')
        fresh = engine.check_poem(state)
        require(fresh['valid'] is True, 'HANDOFF_RULES_FAILED_OR_UNKNOWN', 'Current rule engine must pass')
        assignments = search.data['assignments']
        cls._verify_content(state, p.get('final_content'), assignments, engine)
        state.set_assessment(fresh)  # Historical candidate scores are deliberately discarded.
        s = cls(dict(version=VERSION, source=deepcopy(snapshot), path_id=path_id, run_id=run_id,
                     config=deepcopy(config), assignments=deepcopy(assignments), accepted=state.to_dict(),
                     status='running', reason=None, used=dict(rounds=0, interactions=0), next_job=0,
                     pending=None, review=None, content=None, queue=[], candidate=None, history=[],
                     reviews=[], redline=None, processed={}, invalid={}, transcript=[], output=None), engine)
        s._job('review')
        return s

    @staticmethod
    def _verify_content(state, artifacts, assignments, engine):
        d = state.to_dict()
        require(isinstance(artifacts, list), 'MISSING_CONTENT', 'Full content artifacts required')
        has = bool(requirements(d['request']))
        require(len(artifacts) == (len(d['lines']) if has else 0), 'MISSING_CONTENT', 'Every line must be checked')
        due = []
        for i, f in enumerate(artifacts, 1):
            require(f['context']['line_index'] == i and f['context']['content_plan']['assignments'] == assignments,
                    'STALE_CONTENT', 'Content arrangement mismatch')
            verified = _verify_filter(state, f, engine)
            require(len(verified['records']) == 1 and verified['records'][0]['text'] == d['lines'][i-1],
                    'STALE_CONTENT', 'Content evidence is not for current text')
            due.extend(x for x in verified['records'][0]['content_result']['requirements'] if x['blocking'])
        expected = sum(item['required'] if kind == 'allusion' else item['mode'] != 'inspiration'
                       for kind, item in requirements(d['request']))
        require(len(due) == expected and all(x['status'] == 'satisfied' for x in due),
                'CONTENT_FAILED_OR_UNKNOWN', 'Every required item needs current satisfaction')

    def _state(self):
        return PoemState.from_dict(self.data['accepted'])

    def _stop(self, status, reason):
        self.data.update(status=status, reason=reason, pending=None)

    def _evidence(self):
        """Only expose content evidence for the accepted body, never an earlier text."""
        d = self.data
        if d['content'] is not None:
            return deepcopy(d['content'])
        for record in reversed(d['history']):
            if record['decision'] == 'adopted' and make_binding(record['state']) == make_binding(d['accepted']):
                return deepcopy(record['content'])
        original = next(p for p in d['source']['search']['drafts'] if p['path_id'] == d['path_id'])
        return deepcopy(original['final_content']) if make_binding(original['state']) == make_binding(d['accepted']) else None

    def _job(self, stage, **extra):
        d = self.data
        if d['used']['interactions'] >= d['config']['max_interactions']:
            self._stop('budget_exhausted', 'MAX_INTERACTIONS')
            return
        d['next_job'] += 1
        d['used']['interactions'] += 1
        target = d['candidate']['state'] if stage in ('candidate_content', 'compare') else d['accepted']
        context = dict(accepted=deepcopy(d['accepted']), target=deepcopy(target),
                       assignments=deepcopy(d['assignments']), review=deepcopy(d['review']),
                       candidate=deepcopy(d['candidate']), config=deepcopy(d['config']),
                       search_run_id=d['source']['search']['run_id'],
                       handoff_sha256=d['source']['sha256'],
                       content_evidence=self._evidence(),
                       weights=WEIGHTS.copy(), review_version=REVIEW_VERSION, **deepcopy(extra))
        job = dict(contract_version=VERSION, run_id=d['run_id'], job_id='w' + str(d['next_job']),
                   path_id=d['path_id'], revision=target['revision'], binding=make_binding(target),
                   stage=stage, context=context, attempt=1)
        job['work_sha256'] = digest({k: v for k, v in job.items() if k != 'attempt'})
        d.update(pending=job, status='waiting_for_llm', reason=None)

    def next_job(self):
        return deepcopy(self.data['pending'])

    def _validate_job(self, job):
        d = self.data
        target = d['candidate']['state'] if job['stage'] in ('candidate_content', 'compare') else d['accepted']
        require(job['work_sha256'] == digest({k: v for k, v in job.items() if k not in ('attempt', 'work_sha256')}),
                'STALE_WORK', 'Pending work was changed')
        require(job['binding'] == make_binding(target) and job['revision'] == target['revision']
                and job['context']['target'] == target and job['context']['accepted'] == d['accepted'],
                'STALE_WORK', 'Current accepted/candidate body or context changed after work issuance')
        for key in ('assignments', 'review', 'candidate', 'config'):
            require(job['context'][key] == d[key], 'STALE_WORK', key + ' changed after work issuance')
        require(job['context']['content_evidence'] == self._evidence(), 'STALE_WORK', 'Content evidence changed')

    def _record_review(self, job, payload, target):
        review = validate_review(payload, target)
        record = dict(identity={k: deepcopy(job[k]) for k in IDENTITY}, review=review)
        self.data['reviews'].append(deepcopy(record))
        return record

    def _after_review(self):
        review = self.data['review']['review']
        conclusion = review['conclusion']
        if conclusion in ('needs_clarification', 'unresolved'):
            self._stop('needs_clarification' if conclusion == 'needs_clarification' else 'incomplete', conclusion)
        elif conclusion == 'revise':
            if self.data['used']['rounds'] >= self.data['config']['max_rounds']:
                self._stop('budget_exhausted', 'MAX_POLISH_ROUNDS')
            else:
                self.data['used']['rounds'] += 1
                self._job('generate')
        else:
            self.data['content'] = []
            self._content_next(False)

    def _plan(self, state):
        p = export_content_plan(state)
        p['assignments'] = deepcopy(self.data['assignments'])
        validate_content_plan(state, p)
        return p

    def _content_next(self, candidate):
        d = self.data
        target = d['candidate'] if candidate else d
        state = PoemState.from_dict(target['state'] if candidate else d['accepted'])
        if not requirements(state.to_dict()['request']):
            self._content_done(candidate)
            return
        while len(target['content']) < len(state.to_dict()['lines']):
            i = len(target['content']) + 1
            batch = digest([d['run_id'], d['next_job'], state.to_dict()['revision'], i,
                            d['candidate']['package'] if candidate else None])[:32]
            c = _context(state, i, batch, self._plan(state))
            line = state.to_dict()['lines'][i-1]
            require(not c['locked_candidate'] or c['locked_candidate']['text'] == line,
                    'LOCKED_EXACT_LINE', 'Current line differs from locked text')
            p = {**{k: c[k] for k in ENVELOPE}, 'attempt': 1,
                 'candidates': [dict(id='whole', text=line, line_index=i)]}
            f = filter_candidates(state, c, None if c['locked_candidate'] else p, engine=self.engine)
            if f['content_request']['candidates']:
                self._job('candidate_content' if candidate else 'final_content', filtered=f)
                return
            target['content'].append(f)
        self._content_done(candidate)

    def _content_done(self, candidate):
        d = self.data
        target = d['candidate'] if candidate else d
        state = PoemState.from_dict(target['state'] if candidate else d['accepted'])
        try:
            self._verify_content(state, target['content'], d['assignments'], self.engine)
        except ValueError as exc:
            if candidate:
                self._reject(getattr(exc, 'code', 'CONTENT_FAILED_OR_UNKNOWN'))
            else:
                self._stop('incomplete', getattr(exc, 'code', 'CONTENT_FAILED_OR_UNKNOWN'))
            return
        if candidate:
            self._job('compare')
        else:
            self._job('redline')

    def _packages(self, payload):
        fields(payload, ('packages', 'needs_user_change', 'reason'))
        require(type(payload['needs_user_change']) is bool and text(payload['reason']),
                'INVALID_GENERATION', 'Explain proposal or retention')
        require(isinstance(payload['packages'], list) and len(payload['packages']) <= self.data['config']['candidates_per_round'],
                'CANDIDATE_LIMIT', 'Too many packages')
        if payload['needs_user_change']:
            require(not payload['packages'], 'LOCK_CHANGE', 'Clarification must retain original')
            self._stop('needs_clarification', payload['reason'])
            return
        ids = {h['package']['id'] for h in self.data['history']}
        state = self.data['accepted']
        issues = {x['id']: x for x in self.data['review']['review']['issues']}
        for p in payload['packages']:
            fields(p, ('id', 'binding', 'revision', 'edits', 'issue_ids', 'reason'))
            require(text(p['id']) and p['id'] not in ids, 'DUPLICATE_MODIFICATION_ID', 'Unique modification ID required')
            ids.add(p['id'])
            require(p['binding'] == make_binding(state) and type(p['revision']) is int and p['revision'] == state['revision'],
                    'STALE_MODIFICATION', 'Modification base differs')
            require(text(p['reason']) and isinstance(p['issue_ids'], list) and bool(p['issue_ids'])
                    and all(isinstance(x, str) and x in issues for x in p['issue_ids'])
                    and len(set(p['issue_ids'])) == len(p['issue_ids']), 'INVALID_ISSUE_LINK', 'Link concrete review issues')
            require(isinstance(p['edits'], list) and 1 <= len(p['edits']) <= 2,
                    'INVALID_EDIT_SCOPE', 'One line or one necessary couplet per atomic package')
            allowed = {q['line_index'] for k in p['issue_ids'] for q in issues[k]['locations']}
            positions = set()
            for e in p['edits']:
                fields(e, ('line_index', 'old', 'new'))
                i = e['line_index']
                require(type(i) is int and i in allowed and i not in positions,
                        'INVALID_EDIT_SCOPE', 'Only unique issue locations may change')
                require(e['old'] == state['lines'][i-1] and text(e['new']) and e['new'] != e['old'],
                        'INVALID_EDIT_TEXT', 'Original must match; new text must be nonempty and different')
                positions.add(i)
            if len(positions) == 2:
                lo, hi = sorted(positions)
                require(lo % 2 == 1 and hi == lo + 1, 'INVALID_EDIT_SCOPE', 'Two-line edits must form a couplet')
        self.data['queue'] = deepcopy(payload['packages'])
        self._next_candidate()

    def _next_candidate(self):
        d = self.data
        if not d['queue']:
            # An honest new whole-poem review can decide that retaining minor issues is better.
            self._job('review', retention_history=deepcopy(d['history']))
            return
        package = d['queue'].pop(0)
        state = self._state()
        candidate = state.to_dict()
        for edit in package['edits']:
            candidate['lines'][edit['line_index']-1] = edit['new']
        candidate.update(revision=candidate['revision'] + 1, checks={}, quality_score=None, status='reviewing')
        state = PoemState.from_dict(candidate)  # Entire package constructs one revision, never partial writes.
        d['candidate'] = dict(package=package, state=state.to_dict(), content=[], base=deepcopy(d['accepted']))
        for kind, item in requirements(candidate['request']):
            if kind == 'fragment' and item['mode'] == 'exact':
                index = next(x['line_index'] for x in d['assignments'] if x['kind'] == kind and x['requirement_id'] == item['id'])
                if (not _exact(item, candidate['lines'][index-1], index) or
                        item['kind'] == 'line' and candidate['lines'][index-1] != item['text']):
                    self._reject('LOCKED_EXACT_CONTENT')
                    return
        checks = self.engine.check_poem(state)
        state.set_assessment(checks)
        d['candidate']['state'] = state.to_dict()
        if checks['valid'] is not True:
            self._reject('RULES_FAILED_OR_UNKNOWN')
            return
        self._content_next(True)

    def _reject(self, reason):
        d = self.data
        d['history'].append({**deepcopy(d['candidate']), 'decision': 'rejected', 'reason': reason})
        d['candidate'] = None
        self._next_candidate()

    def _compare(self, job, payload):
        fields(payload, ('candidate_review', 'dimensions', 'resolved_issue_ids', 'decision', 'reason'))
        d = self.data
        c = d['candidate']
        reviewed = self._record_review(job, payload['candidate_review'], c['state'])
        fields(payload['dimensions'], WEIGHTS)
        old, new = d['review']['review'], reviewed['review']
        regressions = []
        for name, entry in payload['dimensions'].items():
            fields(entry, ('verdict', 'reason'))
            a, b = old['dimensions'][name]['score'], new['dimensions'][name]['score']
            expected = 'not_applicable' if a is None else 'better' if b > a else 'worse' if b < a else 'same'
            require(entry['verdict'] == expected and text(entry['reason']), 'COMPARISON_CONFLICT', name)
            if expected == 'worse':
                regressions.append(name)
        resolved = payload['resolved_issue_ids']
        require(isinstance(resolved, list) and all(isinstance(x, str) and x in c['package']['issue_ids'] for x in resolved)
                and len(set(resolved)) == len(resolved), 'INVALID_RESOLVED_ISSUES', 'Only addressed issues may be resolved')
        require(payload['decision'] in ('adopt', 'retain') and text(payload['reason']), 'INVALID_COMPARISON', 'Explain whole-poem choice')
        c['comparison'] = dict(identity={k: deepcopy(job[k]) for k in IDENTITY}, payload=deepcopy(payload))
        c['review'] = reviewed
        if payload['decision'] == 'retain':
            self._reject(payload['reason'])
            return
        eligible = (new['conclusion'] == 'ready' and not regressions and bool(resolved)
                    and new['total'] - old['total'] >= .05 - 1e-12)
        if not eligible:
            self._reject('ADOPTION_STANDARD_NOT_MET')
            return
        # Recheck on the transaction copy immediately before publishing an accepted revision.
        state = PoemState.from_dict(c['state'])
        checks = self.engine.check_poem(state)
        if checks['valid'] is not True:
            self._reject('RULES_FAILED_OR_UNKNOWN')
            return
        self._verify_content(state, c['content'], d['assignments'], self.engine)
        state.set_assessment(checks)
        d['history'].append({**deepcopy(c), 'decision': 'adopted', 'reason': payload['reason']})
        d.update(accepted=state.to_dict(), candidate=None, queue=[], review=None, content=None, redline=None)
        self._job('review')  # Comparison never doubles as the accepted version's final review.

    def _redline(self, job, payload):
        d = self.data
        state = self._state()
        redline = validate_redline(payload, state.to_dict())
        record = dict(identity={k: deepcopy(job[k]) for k in IDENTITY}, redline=redline)
        d['redline'] = deepcopy(record)
        if redline['conclusion'] != 'pass':
            self._stop('incomplete', 'FINAL_REDLINE_BLOCKED')
            return
        self._job('final', redline=record)

    def _final(self, job, payload):
        fields(payload, ('title', 'notes'))
        require(text(payload['title']) and len(payload['title']) <= 80 and '\n' not in payload['title']
                and isinstance(payload['notes'], list) and all(text(x) for x in payload['notes']),
                'INVALID_OUTPUT', 'A separate title and optional brief notes are required')
        d = self.data
        state = self._state()
        require(d['review'] is not None, 'MISSING_REVIEW', 'Current whole-poem review required')
        require(d['redline'] is not None, 'MISSING_REDLINE', 'Current final red-line check required')
        redline_raw = d['redline']['redline']
        redline = validate_redline(redline_raw, state.to_dict())
        require(redline['conclusion'] == 'pass', 'FINAL_REDLINE_BLOCKED', 'Final red-line check did not pass')
        require(d['redline']['identity']['run_id'] == d['run_id'] and
                d['redline']['identity']['path_id'] == d['path_id'] and
                d['redline']['identity']['binding'] == make_binding(state.to_dict()) and
                d['redline']['identity']['revision'] == state.to_dict()['revision'],
                'STALE_REDLINE', 'Wrong red-line run/path/body')
        raw = {k: v for k, v in d['review']['review'].items() if k != 'total'}
        review = validate_review(raw, state.to_dict())
        require(review['conclusion'] == 'ready', 'FINAL_REVIEW_BLOCKED', 'Review is not ready')
        require(d['review']['identity']['run_id'] == d['run_id'] and
                d['review']['identity']['path_id'] == d['path_id'], 'STALE_REVIEW', 'Wrong review run/path')
        checks = self.engine.check_poem(state)
        if checks['valid'] is not True:
            self._stop('incomplete', 'FINAL_RULES_FAILED_OR_UNKNOWN')
            return
        try:
            self._verify_content(state, d['content'], d['assignments'], self.engine)
        except ValueError as exc:
            self._stop('incomplete', getattr(exc, 'code', 'FINAL_CONTENT_FAILED'))
            return
        state.set_assessment(checks, quality_score=review['total'])
        state.set_status('completed')
        d['accepted'] = state.to_dict()
        d['output'] = dict(output_version=VERSION, title=payload['title'], lines=state.to_dict()['lines'],
                           notes=deepcopy(payload['notes']), status='completed', binding=make_binding(state.to_dict()),
                           revision=state.to_dict()['revision'], identity={k: deepcopy(job[k]) for k in IDENTITY})
        self._stop('completed', 'CURRENT_RULES_CONTENT_REVIEW_AND_REDLINE_PASSED')

    def _receive(self, job, payload):
        stage = job['stage']
        if stage == 'review':
            self.data['review'] = self._record_review(job, payload, self.data['accepted'])
            self._after_review()
        elif stage == 'generate':
            self._packages(payload)
        elif stage in ('candidate_content', 'final_content'):
            candidate = stage == 'candidate_content'
            target = self.data['candidate'] if candidate else self.data
            state = PoemState.from_dict(target['state'] if candidate else self.data['accepted'])
            require(isinstance(payload, dict) and payload.get('attempt') == job['attempt'], 'WRONG_ATTEMPT', 'Use issued attempt')
            reviewed = review_content(state, job['context']['filtered'], payload, engine=self.engine)
            target['content'].append(reviewed)
            self._content_next(candidate)
        elif stage == 'compare':
            self._compare(job, payload)
        elif stage == 'redline':
            self._redline(job, payload)
        elif stage == 'final':
            self._final(job, payload)

    def submit(self, response):
        fields(response, (*IDENTITY, 'payload'))
        fingerprint = digest(response)
        if fingerprint in self.data['invalid']:
            return {'status': 'already_rejected'}
        require(isinstance(response['job_id'], str), 'WRONG_JOB', 'job_id must be a string')
        old = self.data['processed'].get(response['job_id'])
        if old:
            require(old == fingerprint, 'CHANGED_REPLAY', 'Previously processed job changed')
            return {'status': 'already_processed'}
        job = self.data['pending']
        require(job is not None and self.data['status'] == 'waiting_for_llm', 'NO_PENDING_JOB', 'No pending work')
        require(all(response[k] == job[k] for k in IDENTITY) and type(response['attempt']) is int
                and type(response['revision']) is int, 'WRONG_JOB', 'Wrong work, attempt, text, context, run or path')
        self._validate_job(job)
        staged = Polish(self.data, self.engine)
        staged.data.update(pending=None, status='running')
        try:
            payload = response['payload']
            if isinstance(payload, str):
                payload = read_json(payload)
            staged._receive(job, payload)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            staged = Polish(self.data, self.engine)
            d = staged.data
            code = getattr(exc, 'code', 'INVALID_MODEL_RESULT')
            d['invalid'][fingerprint] = dict(code=code, reason=str(exc), job_id=job['job_id'])
            if job['attempt'] > d['config']['max_format_repairs']:
                staged._stop('incomplete', 'FORMAT_REPAIRS_EXHAUSTED')
            elif d['used']['interactions'] >= d['config']['max_interactions']:
                staged._stop('budget_exhausted', 'MAX_INTERACTIONS')
            else:
                d['pending']['attempt'] += 1
                d['used']['interactions'] += 1
        else:
            staged.data['processed'][job['job_id']] = fingerprint
        staged.data['transcript'].append(deepcopy(response))
        self.data = staged.data
        return dict(status=self.data['status'], reason=self.data['reason'])

    def result(self, *, include_notes=False):
        if self.data['status'] != 'completed':
            return dict(status=self.data['status'], reason=self.data['reason'],
                        lines=deepcopy(self.data['accepted']['lines']), title=None)
        out = {k: deepcopy(self.data['output'][k]) for k in ('title', 'lines', 'status')}
        if include_notes:
            out['notes'] = deepcopy(self.data['output']['notes'])
        return out

    def to_dict(self):
        return {'polish': deepcopy(self.data), 'sha256': digest(self.data)}

    @classmethod
    def from_dict(cls, artifact, *, engine=None):
        fields(artifact, ('polish', 'sha256'))
        d = artifact['polish']
        require(isinstance(d, dict) and digest(d) == artifact['sha256'] and d.get('version') == VERSION,
                'INVALID_SNAPSHOT', 'Unsupported or corrupt snapshot')
        # Replay validates budgets, pending identities, histories and completion, not just a checksum.
        restored = cls._start(d['source'], d['path_id'], engine or ConstraintEngine(), d['run_id'], d['config'])
        require(isinstance(d['transcript'], list) and len(d['transcript']) <= d['config']['max_interactions'],
                'INVALID_SNAPSHOT', 'Transcript exceeds issued model budget')
        for response in d['transcript']:
            restored.submit(response)
        require(restored.data == d, 'SNAPSHOT_REPLAY_MISMATCH', 'Snapshot or rule resources changed; cannot reuse proof')
        return restored

    def save(self, path):
        write_new_json(output_path(path), self.to_dict())

    @classmethod
    def load(cls, path, **kwargs):
        return cls.from_dict(read_json(input_path(path).read_text(encoding='utf-8-sig')), **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('start', 'next', 'submit', 'result'):
        sub = commands.add_parser(name)
        sub.add_argument('--input', required=True)
        sub.add_argument('--output', required=True)
        sub.add_argument('--work-dir')
        if name == 'start':
            sub.add_argument('--path-id', required=True)
            sub.add_argument('--max-rounds', type=int, default=3)
            sub.add_argument('--candidates-per-round', type=int, default=3)
            sub.add_argument('--max-interactions', type=int, default=40)
            sub.add_argument('--max-format-repairs', type=int, default=2)
        elif name == 'submit':
            sub.add_argument('--response', required=True)
        elif name == 'result':
            sub.add_argument('--include-notes', action='store_true')
    args = parser.parse_args()
    def load(path):
        return read_json(input_path(path).read_text(encoding='utf-8-sig'))
    try:
        configure_workspace(args.work_dir)
        if args.command == 'start':
            polish = Polish.create(load(args.input), args.path_id, **{k: getattr(args, k) for k in
                ('max_rounds', 'candidates_per_round', 'max_interactions', 'max_format_repairs')})
        else:
            polish = Polish.load(args.input)
        if args.command == 'submit':
            polish.submit(load(args.response))
        output = (polish.next_job() if args.command == 'next' else
                  polish.result(include_notes=args.include_notes) if args.command == 'result' else polish.to_dict())
        write_new_json(output_path(args.output), output)
    except (ValueError, OSError) as exc:
        parser.exit(2, 'Polish error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
