"""Read-only verification of the supplied survey workbook and protocol.

Requires optional openpyxl/pdfplumber (available in Codex's bundled Python).
No respondent timestamps or free text are exported.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from statistics import mean


def verify(workbook, protocol, reference):
    import openpyxl
    import pdfplumber
    sheet = next(iter(openpyxl.load_workbook(workbook, read_only=True, data_only=True)))
    rows = list(sheet.values)
    headers, responses = rows[0], [r for r in rows[1:] if r[0] is not None]
    checked = {}
    for n in range(1, 10):
        ref = reference['scenarios'][f'S{n}']
        preference_col = next(i for i, name in enumerate(headers)
                              if name and name.startswith(f'Scenario {n}: Which action'))
        counts = Counter(r[preference_col] for r in responses)
        if dict(counts) != ref['preferred_counts'] or len(responses) != ref['respondents']:
            raise ValueError(f'S{n}: preferred counts differ')
        checked[f'S{n}'] = {'preferred_counts_match': True, 'ratings': {}}
        for action in ('YIELD', 'CONTINUE', 'ENGAGE', 'APPROACH'):
            col = next(i for i, name in enumerate(headers) if name and
                       name.startswith(f'Scenario {n}: How appropriate') and name.endswith(f'[{action}]'))
            values = [r[col] for r in responses if type(r[col]) in (int, float)]
            if not all(1 <= v <= 5 for v in values):
                raise ValueError('ratings outside protocol scale')
            if mean(values) != ref['mean_appropriateness'][action] or len(values) != ref['rating_n'][action]:
                raise ValueError(f'S{n}.{action}: rating differs')
            checked[f'S{n}']['ratings'][action] = {'mean': mean(values), 'n': len(values),
                                                  'unrated': len(responses) - len(values)}
    with pdfplumber.open(protocol) as pdf:
        texts = [p.extract_text() for p in pdf.pages]
    text = re.sub(r'\s+', ' ', ' '.join(texts))
    phrases = ['immediate next action at the moment each video ends', 'rate each action independently',
               'do not use a rating of 3 to indicate uncertainty']
    if not all(phrase in text for phrase in phrases):
        raise ValueError('protocol instructions could not be verified')
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return {'respondents': len(responses), 'scenarios': checked,
            'workbook_sha256': digest(workbook), 'protocol_sha256': digest(protocol),
            'protocol_pages': len(texts), 'protocol_checks': phrases,
            'source_files_preserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--reference', type=Path, default=Path('config/human-reference.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.workbook, args.protocol, json.loads(args.reference.read_text()))
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)


if __name__ == '__main__':
    main()
