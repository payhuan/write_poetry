"""Validation helpers for character-by-character couplet assessments."""

from math import prod

from text_normalizer import normalize_line


def validate_character_alignment(lines, alignment, line_indices, width):
    if not isinstance(lines, (dict, list)) or not isinstance(alignment, list) or len(alignment) != width:
        raise ValueError('Character alignment must cover every corresponding position')

    def line(index):
        if isinstance(lines, list):
            return lines[index - 1]
        return lines[index] if index in lines else lines[index - 1]

    upper = normalize_line(line(line_indices[0]), line_indices[0])['text']
    lower = normalize_line(line(line_indices[1]), line_indices[1])['text']
    if len(upper) != width or len(lower) != width:
        raise ValueError('Couplet lines do not match the declared character width')
    expected_fields = {'position', 'upper_char', 'lower_char', 'upper_function',
                       'lower_function', 'status', 'reason'}
    for position, item in enumerate(alignment, 1):
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise ValueError('Each character alignment needs exact paired-character fields')
        if (type(item['position']) is not int or item['position'] != position or
                item['upper_char'] != upper[position - 1] or
                item['lower_char'] != lower[position - 1]):
            raise ValueError('Character alignment differs from the couplet text')
        if (not isinstance(item['upper_char'], str) or not isinstance(item['lower_char'], str) or
                not isinstance(item['upper_function'], str) or not item['upper_function'].strip() or
                not isinstance(item['lower_function'], str) or not item['lower_function'].strip() or
                not isinstance(item['status'], str) or
                item['status'] not in ('pass', 'weak', 'fail', 'unknown') or
                not isinstance(item['reason'], str) or not item['reason'].strip()):
            raise ValueError('Character functions, status and reason are required')


def validate_structure_alignment(lines, alignment, line_indices, width):
    """Validate ordered phrase/constituent spans that partition both couplet lines."""
    if not isinstance(lines, (dict, list)) or not isinstance(alignment, list) or not alignment:
        raise ValueError('Structural alignment needs ordered phrase pairs')

    def line(index):
        if isinstance(lines, list):
            return lines[index - 1]
        return lines[index] if index in lines else lines[index - 1]

    upper = normalize_line(line(line_indices[0]), line_indices[0])['text']
    lower = normalize_line(line(line_indices[1]), line_indices[1])['text']
    if len(upper) != width or len(lower) != width:
        raise ValueError('Couplet lines do not match the declared character width')
    expected_fields = {'upper_span', 'lower_span', 'upper_text', 'lower_text',
                       'upper_function', 'lower_function', 'correspondence',
                       'status', 'reason'}
    cursors = [1, 1]
    for item in alignment:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise ValueError('Each structural alignment needs exact paired-phrase fields')
        spans = (item['upper_span'], item['lower_span'])
        texts = (item['upper_text'], item['lower_text'])
        source_lines = (upper, lower)
        for side, (span, phrase, source) in enumerate(zip(spans, texts, source_lines)):
            if (not isinstance(span, list) or len(span) != 2 or
                    any(type(position) is not int for position in span) or
                    span[0] != cursors[side] or span[1] < span[0] or span[1] > width):
                raise ValueError('Structural spans must partition both lines in order without gaps or overlap')
            if not isinstance(phrase, str) or phrase != source[span[0]-1:span[1]]:
                raise ValueError('Structural span text differs from the couplet text')
            cursors[side] = span[1] + 1
        if (not isinstance(item['upper_function'], str) or not item['upper_function'].strip() or
                not isinstance(item['lower_function'], str) or not item['lower_function'].strip() or
                not isinstance(item['correspondence'], str) or not item['correspondence'].strip() or
                item['status'] not in ('pass', 'weak', 'fail', 'unknown') or
                not isinstance(item['reason'], str) or not item['reason'].strip()):
            raise ValueError('Structural functions, correspondence, status and reason are required')
    if cursors != [width + 1, width + 1]:
        raise ValueError('Structural spans must cover every character in both lines')


def validate_soft_rating(alignment, score, label):
    """Accumulate character penalties; cap structure by its worst span."""
    caps = {'pass': 1.0, 'weak': .74, 'unknown': .59, 'fail': .49}
    limits = [caps[item['status']] for item in alignment]
    ceiling = prod(limits) if label == 'Character' else min(limits, default=1.0)
    if score > ceiling + 1e-12:
        raise ValueError(f'{label} score conflicts with reported alignment status')
