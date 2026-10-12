"""Load frozen rules with their own SocialState contract for offline parity."""
import importlib.util
from pathlib import Path
import sys


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def load_frozen_rules(source):
    source = Path(source).resolve()
    # Different frozen trees must not share an oracle module.
    import hashlib
    name = '_frozen_rule_reference_' + hashlib.sha256(str(source).encode()).hexdigest()[:16]
    models = _load(name + '_models', source/'app/state/social_models.py')
    rules = _load(name + '_rules', source/'app/policy/rules.py')
    rules.SocialState = models.SocialState
    rules.observation_hold_reason = models.observation_hold_reason
    return rules
