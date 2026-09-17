"""Deterministic failure -> checkpoint recovery -> draft demonstration, no model calls."""
import argparse
import json
from poem_state import PoemState
from poem_templates import TemplateLibrary
from constraint_engine import ConstraintEngine
from rhyme_lexicon import RhymeLexicon
from poetry_round import ENVELOPE, WEIGHTS, write_new_json
from poetry_search import Search
from runtime_workspace import configure_workspace, output_path

LEXICON = {'上平声部': {'一东': list('天地人')}, '上声部': {'一董': list('水火土')}}


def fixture_state(form='五言绝句', **budget):
    s = PoemState.create({'raw_request': '山中测试', 'theme': '山中', 'poem_type': form,
                          'meter': {'start': '仄起', 'first_line_rhymes': False}})
    TemplateLibrary().bind(s)
    s.set_plan(rhyme={'system': '平水韵', 'group': '一东', 'first_line_rhymes': False},
               structure=[{'unit': '颔联', 'intent': '由远及近'}])
    s.set_budget(**{'beam_width': 1, **budget})
    return s


def fixture_engine():
    return ConstraintEngine(RhymeLexicon(LEXICON))


def line_for(context, variant=0):
    tones = context['tone_requirements']
    out = []
    for i, item in enumerate(tones):
        tone = item['tune']
        if tone == '中':
            tone = tones[i+1]['tune']
        out.append(('天' if variant == 0 else '地') if tone == '平' else ('水' if variant == 0 else '火'))
    return ''.join(out)


def response_for(job, *, fail_high=False, two=False, value=0.8):
    c = job['context']
    payload = {**{k: c[k] for k in ENVELOPE}, 'attempt': job['attempt']}
    if job['stage'] == 'generate':
        texts = [line_for(c)]
        if two or not c['lines']:
            texts.append(line_for(c, 1))
        if fail_high and c['lines'] and '水' in c['lines'][0]:
            texts = ['错']
        payload['candidates'] = [{'id': str(i), 'text': t, 'line_index': c['line_index']} for i, t in enumerate(texts)]
    elif job['stage'] == 'score':
        f = job['filtered']
        payload.update(filter_sha256=f['filter_sha256'], rubric_version='literary-1',
                       scores=[{'id': item['id'], 'evaluation': {'dimensions': {k: value - i * .1 for k in WEIGHTS},
                               'reason': '确定性控制流程夹具评分。'}} for i, item in enumerate(f['score_request']['candidates'])])
    else:
        f = job['filtered']
        payload.update(filter_sha256=f['filter_sha256'], content_version='round-content-1', reviews=[
            {'id': item['id'], 'line_index': item['line_index'], 'text_sha256': item['text_sha256'],
             'requirements': [{'kind': r['kind'], 'requirement_id': r['requirement_id'], 'status': 'satisfied',
                               'reason': '固定语义夹具', 'interpretation': '仅验证契约',
                               'evidence': [{'line_index': item['line_index'], 'quote': item['text']}]}
                              for r in item['requirements']]}
            for item in f['content_request']['candidates']])
    return {**{k: job[k] for k in ('run_id', 'job_id', 'path_id', 'batch_id', 'context_sha256')}, 'payload': payload}


def drive(search, **kwargs):
    for _ in range(200):
        if search.data['status'] != 'waiting_for_llm':
            return search
        search.submit(response_for(search.next_job(), **kwargs))
    raise AssertionError('Controller failed to terminate')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='examples/day4-result.json')
    parser.add_argument('--work-dir')
    args = parser.parse_args()
    configure_workspace(args.work_dir)
    search = drive(Search.create(fixture_state(), engine=fixture_engine()), fail_high=True)
    assert search.data['status'] == 'ready_for_review', search.data['events']
    assert search.data['used']['backtracks'] == 1
    write_new_json(output_path(args.output), search.to_dict())
    print(json.dumps({'status': search.data['status'], 'used': search.data['used'],
                      'events': search.data['events']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
