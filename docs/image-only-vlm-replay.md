# Image-only VLM replay (Step 5)

`python -m app.vlm_replay` reads existing Step 4 associated replay JSONL and adds
`vlm_inference`. It preserves every original field, row and row order, including
frozen SocialState/canonical JSON, source moments, image_matching and any existing
LLM result. It performs no sampling, estimation, rematching or robot execution.

## Files and interfaces

- `app/replay/vlm_inputs.py`: dedicated associated reader, provenance validation and
  exact selected-frame loading/PNG encoding.
- `app/policy/vlm.py`: `build_vlm_prompt(encoded_image)` and
  `decide_vlm(encoded_image, client)`; the result retains the original
  `OllamaResult` and prompt version.
- `app/replay/vlm.py`: focused CLI, separate diagnostics, call cap, output
  protection and complete/partial summary.
- `app/camera/recordings.py`: `read_validated_stored_image(event)` returns the
  existing validator's metadata and the same bounded file read;
  `read_selected_camera_event(path, line_number)` revalidates only the recorded
  manifest line, isolating unrelated broken frame paths. The old
  `validate_stored_image(event)` interface and validation behavior remain intact.
- `app/camera/matching.py`: one optional validation seam; its default remains
  `allow_image_matching=False`, so Step 4 still rejects already-associated input.
- `requirements.txt`: adds `Pillow>=12.0,<13`; computer-side dependency only.
- `tests/test_vlm_inputs.py`, `tests/test_vlm_policy.py`,
  `tests/test_vlm_replay.py`: offline pixel, prompt, transport and runner tests.

The Ollama client validates the same shared `FinalDecision` contract as the LLM.
See [action meanings and output shape](social-policy.md).

## Integrity and encoding

The associated reader reuses base replay validation and checks selected manifest
record, source capture/session/time correlation, camera, dimensions, matching
method, signed difference, receipt opt-in/age policy and Step 4 digest metadata.
It rejects duplicate keys, nonfinite values, malformed metadata, empty inputs and
input already containing `vlm_inference`.

For an eligible matched row the loader reopens its exact stored manifest path and
line, compares the complete recorded manifest event and resolved image path, then
reuses the stored P6 RGB8 validator. Dimensions, format, byte count and SHA-256
must equal Step 4's `image_validation`. Missing/unreadable/changed manifests or
images become per-row image input failures. A different frame is never selected.

Pillow supplies lossless in-memory PNG encoding through `Image.frombytes('RGB',
...)` and `BytesIO`. The existing narrow P6 parser supplies the already-validated
RGB raster boundary. This preserves valid CRLF headers and leading whitespace or
`#` pixel bytes: Pillow 12.3.0's direct PPM decoder can shift the raster boundary
for a CRLF maxval separator. No general image decoder is added. Dimensions and
RGB bytes are unchanged; no crop, resize, annotation, converted files or original
file writes occur. The PNG bytes are encoded as raw base64, without a data URL,
and passed as the single `OllamaMessage.images` item.

## Actual prompt

Version: `image-only-vlm-v2`. The static system and user instructions are
`SYSTEM_PROMPT` and `USER_PROMPT` in [`app/policy/vlm.py`](../app/policy/vlm.py).

Only that user message has an `images` list, containing one raw PNG base64
string. Neither function accepts a replay row or SocialState. Paths, scenario
labels, times, telemetry, person IDs, matching information and previous decisions
stay outside the messages. The existing strict four-action ModelDecision schema
is also sent as the Ollama response format. Invalid outputs remain failures;
there is no fallback or action substitution.

## Commands

Run from the repository root. Install declared dependencies if needed:

```sh
.venv/bin/python -m pip install -r requirements.txt
```

These commands use the existing Step 4 file verified during this step. Replace
`your-installed-vision-model` with an already-installed suitable vision model.
The runner does not inspect capabilities, start Ollama or download a model.
Choose new output paths if the illustrated paths already exist.

Small pass, at most two actual calls, retaining all 73 rows:

```sh
VLM_MODEL='your-installed-vision-model'
.venv/bin/python -m app.vlm_replay \
  /tmp/p4p-image-match-step4.ig4HON/receipt-1s-final.jsonl \
  --base-url http://127.0.0.1:11434 --model "$VLM_MODEL" \
  --timeout 30 --temperature 0 --seed 42 --num-predict 256 \
  --max-calls 2 --output /tmp/p4p-step5-small.jsonl
```

Full pass over the same already-associated rows (no call cap):

```sh
VLM_MODEL='your-installed-vision-model'
.venv/bin/python -m app.vlm_replay \
  /tmp/p4p-image-match-step4.ig4HON/receipt-1s-final.jsonl \
  --base-url http://127.0.0.1:11434 --model "$VLM_MODEL" \
  --timeout 60 --temperature 0 --seed 42 --num-predict 256 \
  --output /tmp/p4p-step5-full.jsonl
```

The previously generated input lives in temporary storage. If unavailable, use
another existing Step 4 associated output or regenerate it using the commands in
[camera-replay-matching.md](camera-replay-matching.md) before running Step 5. The
Step 5 CLI itself never generates or changes association.

## Status, limit and output semantics

The cap counts actual VLM request attempts globally, including failed requests.
Unmatched rows and image loading failures do not consume it. The result records
the configured `max_calls` separately from original Step 3 sampling. Once reached,
subsequent matched rows are written as `not_run_limit` without image loading or
calls; unmatched rows retain `input_unavailable` even after the cap. Every valid
input row is written in its original order.

| VLM status | `ok` | Meaning |
| --- | --- | --- |
| `succeeded` | true | Existing ModelDecision validation succeeded |
| `failed` | false | Original Ollama request/response/decision error retained |
| `input_unavailable` | null | Step 4 did not select a usable frame; no call |
| `input_invalid` | null | Selected file/manifest/encoding failed; no call |
| `not_run_limit` | null | Global request cap reached; no call |

`error_stage` separates `image_input` from `ollama`. Raw content, returned model,
HTTP status and request duration survive model failures as provided by the
original OllamaResult. Source correlation, complete selected frame reference,
verified PPM hash, PNG format/dimensions/hash and model/generation configuration
are separate metadata. Base64 and model messages are never written. The result's
UTC `completed_at` is replay-host wall clock, independent of source clocks.

Original `image_matching` is retained verbatim, including exact versus receipt
method, clock limitations and signed camera-minus-perception difference. Receipt
proximity does not prove simultaneous exposure; a single image does not prove
motion or sustained gaze.

A JSONL subtree excerpt from **fake-response tests**, not real model inference:

```json
{
  "vlm_inference": {
    "schema_version": 1,
    "status": "succeeded",
    "prompt_version": "image-only-vlm-v2",
    "ok": true,
    "requested_model": "vision-model",
    "returned_model": "actual-vision-model",
    "decision": {"action": "CONTINUE", "reason": "Visible evidence."},
    "error": null,
    "error_stage": null,
    "raw_content": "{\"action\": \"CONTINUE\", \"reason\": \"Visible evidence.\"}",
    "request_duration_s": 0.125,
    "image_encoding": {"encoding_format": "PNG", "width": 2, "height": 1},
    "matching_method": "reception_monotonic",
    "matching_time_difference": {
      "value": -100000,
      "units": "microseconds",
      "sign_convention": "camera receipt minus perception receipt; negative means earlier camera"
    }
  }
}
```

The excerpt omits full frame/source correlation, configuration, digests, byte
counts and UTC completion fields for readability. Actual output includes them.
Original top-level status, ok, decision and error remain unchanged.

The CLI validates the entire input into a frozen in-memory list before output or
HTTP, including later rows. This protects all referenced recordings, replay input,
manifests and images, even missing references. Current manifests additionally
supply unmatched candidate image paths when available. Exclusive output creation
refuses existing files, hard links and symlinks. Unreferenced earlier replay paths
cannot be recovered from metadata that never recorded them; existing paths are
still protected by exclusive creation. Output ancestors are checked too, so
directory creation cannot turn a missing protected source file into a directory.

The summary contains calls, successes, failures (invalid images plus inference
failures), separate invalid/unavailable input counts, skipped rows, rows written,
failure categories and output path. `complete_with_unsuccessful_rows` means all
rows were processed, including unavailable/capped rows. It is distinct from
`failed` (preflight/configuration/open failure) and `partial` (write/interrupt
failure after output writing began). Partial output is retained. Exit codes are
0 for complete success, 1 for a completed pass with unsuccessful rows, 2 for a
fatal or partial run. The illustrated input has three unavailable rows, so even
successful inference for all 70 matched rows produces exit code 1.

## Verification and remaining limits

Fake-response tests verify all four actions, exact image-only request payloads,
strict response failures, original Ollama errors, call accounting, preservation,
output collisions and interrupted/write-failed partial passes. They provide no
evidence of action quality.

Relevant regression command:

```sh
.venv/bin/python -m unittest \
  tests.test_vlm_inputs tests.test_vlm_policy tests.test_vlm_replay \
  tests.test_camera_recordings tests.test_image_matching tests.test_image_match_cli \
  tests.test_llm_replay_inputs tests.test_llm_replay tests.test_llm_policy \
  tests.test_ollama tests.test_model_decision tests.test_llm_runner \
  tests.test_camera_capture tests.test_sdk_capture
```

All 275 tests passed: 84 new focused tests (44 input, 16 policy, 24 runner) and
191 existing regressions. Capture regression servers use local loopback test
sockets; there were no real robot or Ollama requests. `git diff --check` passed.

Actual encoding evidence is saved in
[results/vlm-step5-encoding.json](results/vlm-step5-encoding.json). The existing
73-row exact input verified all 26 selected images; the 73-row receipt-opted input
verified all 70 selected images (26 exact, 44 receipt), with three unavailable
rows. Every image was 640x480 RGB8. Original PPM and generated PNG were decoded
independently with Pillow 12.3.0 and compared byte-for-byte: all RGB pixels and
dimensions agreed. All manifest provenance and Step 4 hashes agreed, and input
JSONL hashes were unchanged. The 70 frames totaled 64,513,050 original bytes and
16,755,240 PNG bytes; no converted images or model calls were made for this check.

The bounded existing-service probe to `http://127.0.0.1:11434/api/tags` returned
connection refused. **No real VLM inference was performed**, service started or
model downloaded. Installed vision capability and model decision quality remain
unverified. Limits also include original capture/receipt-clock uncertainty,
single-image temporal ambiguity, memory proportional to replay metadata during
preflight, and PNG bytes/digest potentially varying between library versions
while pixels remain lossless. Live backend selection, comparisons/scoring,
target mapping and robot actions remain deferred beyond Step 5.
