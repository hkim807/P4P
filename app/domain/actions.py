"""Experimental actions; controller stop/orient/cooldown are deliberately separate."""
from typing import Literal, get_args

Action = Literal["CONTINUE", "YIELD", "APPROACH", "ENGAGE"]
ACTIONS = get_args(Action)
ACTION_VERSION = "social-actions-v2"
ACTION_DEFINITIONS = {
    "CONTINUE": "Continue the fixed route; insufficient social reason to interrupt or interact.",
    "YIELD": "Temporarily give the human priority for a likely path conflict, then resume the route.",
    "APPROACH": "Interaction is justified; move to conversational distance before engaging.",
    "ENGAGE": "Initiate interaction at the current conversational distance; the controller orients, stops and greets.",
}
