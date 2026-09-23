"""Validation helpers for character-by-character couplet assessments."""

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
