# Judgement review

The selected Qwen 2.5 7B still misses S4. Its first reason prioritises absent sustained looking and conversation range, despite measured IN_WAY corridor position and DECREASING relative separation. In S6 it says no recent looking despite 0.170852 seconds of measured looking. In S7 it says no closing distance despite an unavailable distance trend.

Qwen 2.5 3B fixes S4 with YIELD, but also wrongly yields in S1/S2/S6/S7/S9. S6's reason says within corridor although the state says TO_SIDE. S7's reason says within conversation distance although the state says APPROACHABLE at 2.4046 metres. Looking-away, interest and interaction-request claims exceed the measured cues.

Qwen 3 4B Instruct wrongly approaches S1/S4. Both reasons infer a person approaching from face detection and decreasing relative separation; neither establishes individual motion or interest. S7's action is aligned but its reason claims no interest and unknown position despite measured TO_SIDE. Several correct interaction actions also claim interest as an established fact. S3's APPROACH is aligned under the unchanged allowed set APPROACH/ENGAGE, even though the frozen rule chooses ENGAGE.

Gemma 3 4B chooses CONTINUE for every first scenario. It misses S2/S3/S4/S9; even S3 with interaction-range proximity and sustained gaze remains CONTINUE. These are behavioural errors, not parsing or freshness failures.

All statements describe the fixed nine tuning recordings. Later outputs and acceptance counts cannot repair these first-action mistakes. No model output was physically executed, and no semantic validator was added.
