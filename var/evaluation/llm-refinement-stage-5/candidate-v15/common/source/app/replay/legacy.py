"""Offline migration of unsupported UNKNOWN placeholders in archived states.

Current live contracts reject these fields. Old snapshots stay untouched on
disk; replay may remove only unavailable placeholders, never measured cues.
"""
from copy import deepcopy


def remove_legacy_unknown_cues(record):
    result = deepcopy(record)
    for person in result.get('people', []):
        for name in ('path_relation', 'pass_gesture'):
            if name in person:
                if person[name] != 'UNKNOWN':
                    raise ValueError(f'archived {name} measurement is unsupported; use the frozen implementation')
                person.pop(name)
    return result
