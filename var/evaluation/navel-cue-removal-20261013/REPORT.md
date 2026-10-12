# Remove unsupported Navel cues

Removed pass-gesture and measured path-relation fields, descriptions, readiness bypasses and rule branches throughout the active raw/state/policy pipeline. DecisionState is v3. Supported numeric evidence, categories, thresholds, proximity descriptions and instructions remain unchanged.

All 574 frames match the original frozen v7 implementation for readiness and complete rule decisions, reasons, targets and IDs. There are still 402 eligible frames, and first rule actions align on all nine scenarios. The oracle loads its own frozen SocialState model. All 18 frozen first/later inputs retain supported full evidence exactly, removing only historical UNKNOWN placeholders and preserving original hashes separately.

The full suite ran 666 methods; no new failures or HTTP errors occurred. The same pre-existing VLM/LLM verbatim-wording test fails four subcases. Contract tests cover schema/output absence, strict live rejection and archival migration without editing source records.

Original stage 1–4 artifacts were verified unchanged. No new LLM calls were made: previous stage 4 scores measure its frozen v2 payload and cannot be attributed to this cleanup.

Proximity remains unchanged. Measured metres and configured conversation/social labels represent different things; see the contract guide for the distinction and possible future experiments.

- [Rule parity](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/navel-cue-removal-20261013/rule-parity.json)
- [Tests](/Users/eddiekim/Documents/Projects/P4P/var/evaluation/navel-cue-removal-20261013/verification/full-tests.log)
- [Supported cue contract](/Users/eddiekim/Documents/Projects/P4P/docs/navel-supported-cues.md)
