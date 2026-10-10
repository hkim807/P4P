"""Experimental actions; controller stop/orient/cooldown are deliberately separate."""
from typing import Literal, get_args

Action = Literal["CONTINUE", "YIELD", "APPROACH", "ENGAGE"]
ACTIONS = get_args(Action)
ACTION_VERSION = "social-actions-v3"
