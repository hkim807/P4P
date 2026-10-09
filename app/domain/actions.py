"""Experimental actions; controller stop/orient/cooldown are deliberately separate."""
from typing import Literal, get_args

Action = Literal["CONTINUE", "YIELD", "APPROACH", "ENGAGE"]
ACTIONS = get_args(Action)
ACTION_VERSION = "social-actions-v3"
ACTION_DEFINITIONS = {
    "CONTINUE": "Follow the fixed route without initiating interaction.",
    "YIELD": "Give a person priority for a likely path conflict, including stopping, slowing, or moving aside.",
    "APPROACH": "Move towards the person to a suitable conversation distance.",
    "ENGAGE": "Stop and begin a conversation without moving closer.",
}
