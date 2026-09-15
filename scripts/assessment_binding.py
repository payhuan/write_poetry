"""Canonical, versioned object/context binding shared by state and check engine."""

import hashlib
import json

BINDING_FIELDS = {'binding_version', 'scope', 'subject', 'context_sha256'}
CONTEXT_FIELDS = ('request', 'poem_type', 'form', 'template_id', 'meter_template',
                  'rhyme', 'lines', 'constraints', 'theme', 'emotion', 'imagery', 'structure')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def subject_for(state, candidate=None):
    if candidate is None:
        return {'text_sha256': digest(state['lines'])}
    return {'candidate_id': candidate['id'], 'line_index': candidate['line_index'],
            'text_sha256': digest(candidate['text'])}


def make_binding(state, candidate=None):
    return {'binding_version': 1, 'scope': 'poem' if candidate is None else 'candidate',
            'subject': subject_for(state, candidate),
            'context_sha256': digest({k: state.get(k) for k in CONTEXT_FIELDS})}


def validate_binding_shape(result):
    present = BINDING_FIELDS & result.keys()
    if not present:
        return  # legacy records remain readable
    if present != BINDING_FIELDS or type(result['binding_version']) is not int or result['binding_version'] != 1:
        raise ValueError('Incomplete or unsupported assessment binding')
    scope, subject = result['scope'], result['subject']
    if scope not in ('poem', 'candidate') or not isinstance(subject, dict):
        raise ValueError('Invalid assessment scope/subject')
    keys = {'text_sha256'} | ({'candidate_id', 'line_index'} if scope == 'candidate' else set())
    if set(subject) != keys:
        raise ValueError('Invalid assessment subject fields')
    if scope == 'candidate' and (not isinstance(subject['candidate_id'], str)
            or not subject['candidate_id'].strip() or type(subject['line_index']) is not int
            or subject['line_index'] < 1):
        raise ValueError('Invalid candidate binding')
    for value in (subject['text_sha256'], result['context_sha256']):
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid assessment digest')


def require_binding(result, state, candidate=None, *, historical=False):
    validate_binding_shape(result)
    expected = make_binding(state, candidate)
    fields = ('binding_version', 'scope', 'subject') if historical else BINDING_FIELDS
    if any(result.get(k) != expected[k] for k in fields):
        raise ValueError('Assessment object or context does not match; run detection again')
