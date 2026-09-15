"""Run the bundled fixed model outputs through one round, using the real rhyme data."""

import argparse
from copy import deepcopy
import json

from constraint_engine import ConstraintEngine
from poem_state import PoemState
from poem_templates import TemplateLibrary
from poetry_round import (ENVELOPE, PROJECT, RUBRIC_VERSION, export_context, filter_candidates,
                          finish_round, project_path, read_json, write_new_json)


def run(output_dir):
    fixture = read_json((PROJECT / 'examples' / 'day3' / 'fixture.json').read_text(encoding='utf-8'))
    state = PoemState.create(fixture['request'])
    TemplateLibrary().bind(state)
    state.set_plan(**fixture['plan'])
    initial = state.to_dict()
    context = export_context(state, fixture['line_index'])
    envelope = {k: context[k] for k in ENVELOPE}
    candidates = {**envelope, 'attempt': 1, 'candidates': deepcopy(fixture['candidates'])}
    engine = ConstraintEngine()
    filtered = filter_candidates(state, context, candidates, engine=engine)
    scores = {**envelope, 'attempt': 1, 'filter_sha256': filtered['filter_sha256'],
              'rubric_version': RUBRIC_VERSION, 'scores': deepcopy(fixture['scores'])}
    report = finish_round(state, filtered, scores, engine=engine)
    output = project_path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    for filename, value in [('state.json', initial), ('context.json', context),
                            ('candidates.json', candidates), ('filtered.json', filtered),
                            ('scores.json', scores), ('result.json', {'state': state.to_dict(), 'report': report})]:
        write_new_json(output / filename, value)
    return {'output': output.relative_to(PROJECT).as_posix(), 'status': report['status'],
            'counts': report['counts'], 'ranking': report['ranking'],
            'recommended_id': report['recommended_id'], 'adopted_id': report['adopted_id'],
            'lines': state.to_dict()['lines']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='examples/day3/run')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output_dir), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Example error: {exc}\n')
