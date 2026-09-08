# Pipeline Recorder, Replayer, and Monitor Plan

## 1. Goal

Build a local-first web app that makes the social-navigation pipeline observable
from input to outcome. It must:

- monitor one or more live observation sources, including connected robots;
- record canonical inputs and the result of every pipeline stage;
- replay a recording at adjustable speed or one frame at a time;
- inspect how values and decisions changed between frames and stages;
- compare an original run with a reprocessed run; and
- remain outside the robot actuation and safety path.

The first release is a shadow-mode engineering tool. "Replay" means replaying
recorded observations through an isolated software pipeline. It must never send
recorded intents or commands to a connected robot.

## 2. Starting Point in This Repository

The existing runtime already has the canonical path the monitor should expose:

```text
ObservationFrame
  -> Pydantic validation
  -> TemporalSocialStateEstimator
  -> SocialState
  -> DecisionScheduler
  -> LLMPolicyBridge (only when scheduled)
  -> BehaviorIntent or explicit error/no-decision
```

Useful foundations already present are:

- versioned `ObservationFrame`, `SocialState`, and `BehaviorIntent` contracts;
- a streaming JSONL reader with monotonic timestamp validation;
- deterministic synthetic recordings;
- per-adapter estimator and scheduler isolation in `ObservationPipeline`;
- an HTTP observation endpoint used by the Navel collector; and
- a read-only Navel integration that does not execute decisions.

The main missing capability is structured instrumentation. The current HTTP
response identifies the state and final intent, but does not retain the
intermediate state, scheduler result, policy request/response, latency, or
transition history.

## 3. Product Scope

### MVP

1. Show live source/robot connection status and the most recent observation.
2. Start and stop a recording for a selected source.
3. Persist observations, social states, scheduler results, policy outcomes,
   errors, timestamps, and stage durations.
4. Import the existing observation-only JSONL format.
5. Replay with play, pause, step, restart, and 0.25x/0.5x/1x/2x/max speed.
6. Inspect raw JSON, a semantic summary, and changes from the previous frame.
7. Display the pipeline as a stage flow plus a synchronized event timeline.
8. Reprocess a recording in a new isolated run and compare results.
9. Support multiple interleaved robot sources without sharing temporal state.

### Later

- Side-by-side rule/LLM/VLM policy comparisons.
- Checkpointed random access for long reprocessing runs.
- Annotation, bookmarks, comments, and exportable evaluation reports.
- Video/image synchronization when collection has been approved.
- Remote multi-user access, roles, and centrally managed storage.
- Closed-loop simulation. Physical command replay is explicitly excluded.

## 4. Architecture

```text
Navel / synthetic / imported JSONL
              |
              v
      existing observation API
              |
              v
       Pipeline Run Context  <---- one isolated context per live or replay run
              |
     +--------+---------+
     | pipeline stages  |----> TraceEmitter ----> bounded persistence queue
     +--------+---------+                              |
              |                                        v
              v                               SQLite run/event catalog
       existing response                       + content-addressed artifacts
                                                       |
                              +------------------------+
                              v
                      Monitor/Replay API
                         |          |
                       REST        SSE
                         |          |
                         +---- web UI
```

### 4.1 Preserve One Processing Path

Extract the orchestration now embedded in the Flask route and
`ObservationPipeline.process()` into a reusable `PipelineService`. Both live
HTTP ingestion and the replay worker must call this same service. The monitor
must observe the real code path rather than maintain a parallel diagnostic
implementation.

Create an explicit `PipelineRunContext` containing:

- `run_id` and source identity;
- a fresh estimator and scheduler;
- the selected policy and its immutable configuration;
- schema, prompt, model, application, and git revision metadata; and
- a trace emitter.

Live robots, synthetic inputs, and every replay get separate contexts. A replay
must not reuse the existing `capabilities.adapter_id` state held by a live
source.

### 4.2 Instrument Stage Boundaries

Add a small `TraceSink` protocol and emit append-only events at these boundaries:

1. `observation.received`
2. `observation.validated` or `observation.rejected`
3. `state.started`
4. `state.completed` or `state.failed`
5. `scheduler.completed` or `scheduler.failed`
6. `policy.started`
7. `policy.completed` or `policy.failed`
8. `intent.validated` or `intent.rejected`
9. `cycle.completed`

Every event should contain `run_id`, `trace_id`, source ID, frame sequence,
source timestamp, server receive timestamp, stage monotonic timestamps, status,
parent event ID, payload reference, error details, and duration where relevant.

Do not put tracing code inside estimator or policy algorithms unless a deeper
algorithm-specific event is genuinely needed. Boundary instrumentation is less
intrusive, easier to test, and adequate for the first release.

### 4.3 Storage

Use SQLite in WAL mode for the local catalog and indexed event metadata. Store
larger immutable JSON/image artifacts by SHA-256 under a configurable data
directory; events point to their hashes. This avoids repeating a full
`SocialState` or prompt in many database rows.

Recommended logical records:

- `Source`: adapter/robot identity, robot type, capabilities, first/last seen.
- `Run`: live/replay/synthetic mode, source, parent recording, status, versions,
  configuration, start/end times, and completeness/dropped-event counters.
- `Cycle`: one accepted or rejected observation and its final disposition.
- `StageEvent`: ordered stage transition, status, timing, and artifact hashes.
- `Artifact`: content hash, media type, size, location, and redaction class.
- `Bookmark` (later): user annotation tied to a cycle or stage event.

Keep the checked-in `recordings/synthetic` fixtures unchanged. Runtime data
should live in an ignored directory such as `var/recordings`.

Provide a portable recording bundle:

```text
<run-id>/
  manifest.json
  observations.jsonl   # compatible with JsonlObservationIterator
  trace.jsonl          # stage events and artifact references
  artifacts/           # optional deduplicated JSON or approved media
```

Store both the received observation payload and its validated canonical form.
The manifest must report whether the recording is complete and whether any
trace events or media were dropped.

### 4.4 Reliability and Performance

Tracing must not become part of the robot safety path. Stage events go through a
bounded, non-blocking persistence queue. Queue saturation, disk-full errors, and
dropped events must be visible in the UI and health endpoint. Explicit
recordings should be marked incomplete instead of silently appearing valid.

Record both source time and server time. Source time drives deterministic state
estimation and replay; server monotonic time measures processing latency. Never
compare timestamps from different clock domains as if they share an epoch.

### 4.5 Live Transport

Use REST for queries and controls, and Server-Sent Events (SSE) for live updates.
The browser only needs server-to-client streaming; SSE is simpler than a
WebSocket and supports reconnection with event IDs. Throttle UI broadcasts to a
configurable rate while still recording every pipeline event.

The Navel collector continues posting to the existing observation endpoint.
The browser connects only to the gateway, never directly to the robot.

## 5. Replay Semantics

Support two distinct modes so users always know what they are viewing:

### Playback

Read a previously recorded trace without invoking the estimator or model.
Seeking is immediate, recorded latencies are shown, and no external dependency
is required.

### Reprocess

Feed the recorded `ObservationFrame` sequence through a fresh
`PipelineRunContext`. Source timestamp deltas determine pacing. The output is a
new child run so the original recording is immutable.

Controls are play, pause, single-step, restart, speed, and cancel. For the MVP,
seeking during reprocessing resets the run and deterministically processes from
the beginning to the requested frame. Add state checkpoints only if long runs
make that too slow.

Policy handling must be explicit:

- `recorded`: show the original policy result without calling a model;
- `stub`: deterministic offline policy for tests and UI development; or
- `current`: call the configured current policy and record its model/prompt
  versions, output, and latency.

Do not claim byte-identical replay when a nondeterministic external model is
rerun. Instead compare typed outputs and clearly label model-dependent changes.

## 6. Web Experience

Use a small React + TypeScript client built with Vite and served by Flask in
production. The synchronized timeline, selection state, JSON diff, and
side-by-side comparison justify a component-based client; the backend remains
Python and owns all pipeline behavior.

### 6.1 Dashboard

- Source/robot cards: online, stale, offline, last frame, rate, clock domain,
  capabilities, controller status, and active recording.
- Active runs: mode, duration, frame count, errors, dropped trace events.
- Recent recordings with duration, source, schema/model version, size, and
  completeness.
- Gateway/model/storage health.

A source is online based on recent observation/heartbeat time, not merely
because it exists in the catalog. Make thresholds configurable.

### 6.2 Live and Replay Workspace

Use one workspace for both live monitoring and replay:

```text
[source/run] [LIVE | PLAYBACK | REPROCESS] [record / replay controls]

Observation -> Validation -> State -> Scheduler -> Policy -> Intent
    green completed | amber skipped | red failed | blue currently active

event timeline / latency waterfall

2-D robot-frame scene      selected-stage summary      transition inspector
robot + humans + trails    status, triggers, action    semantic diff / JSON
```

The pipeline strip shows one selected cycle, not an uncontrolled animation of
every frame. The timeline is the primary navigation control and should be
virtualized for long runs.

The 2-D scene plots the robot at the origin, observed humans, predicted-only
tracks, short trails, velocity vectors, free-space ranges, and selected target.
It must label the coordinate convention (`X forward, Y left`) and visually
distinguish missing data from zero.

### 6.3 Transition Inspector

For the selected cycle/stage, show:

- exact input and output IDs and timestamps;
- a semantic change list, such as `track 17: GLANCE -> SUSTAINED`,
  `distance trend: STABLE -> DECREASING`, or `scheduler: skipped -> triggered`;
- scheduler triggers and why policy invocation did or did not occur;
- policy prompt/schema and raw response when capture is enabled;
- validated `BehaviorIntent`, reason codes, and confidence;
- structured errors with the stage that produced them;
- a raw, searchable JSON tree; and
- duration plus a per-cycle latency waterfall.

Generate semantic deltas from typed models for the fields engineers use most.
Also retain a generic JSON Patch-style diff for completeness. Derived fields
must link back to the source observation/state artifact that produced them.

### 6.4 Comparison View

Compare an original run with a child reprocess run aligned by observation ID
and source timestamp. Highlight:

- first stage where outputs diverge;
- added/removed scheduler triggers;
- changed intent/action/target/reason codes;
- schema, configuration, prompt, or model version differences; and
- latency deltas.

## 7. Proposed API

Keep `POST /api/v1/observations` compatible. Add:

```text
GET    /api/v1/sources
GET    /api/v1/runs
POST   /api/v1/runs/:id/recording/start
POST   /api/v1/runs/:id/recording/stop
GET    /api/v1/runs/:id
GET    /api/v1/runs/:id/cycles?cursor=&limit=
GET    /api/v1/runs/:id/cycles/:sequence
GET    /api/v1/runs/:id/events              # SSE
POST   /api/v1/recordings/import
GET    /api/v1/recordings/:id/export
POST   /api/v1/replays
POST   /api/v1/replays/:id/play
POST   /api/v1/replays/:id/pause
POST   /api/v1/replays/:id/step
POST   /api/v1/replays/:id/seek
POST   /api/v1/replays/:id/cancel
GET    /api/v1/compare?left_run=&right_run=
GET    /api/v1/monitor/health
```

Control endpoints affect only recorder/replay workers. None may map to robot
execution APIs.

## 8. Implementation Phases

### Phase 1: Traceable Pipeline Foundation

- Extract `PipelineService` and `PipelineRunContext` without changing endpoint
  behavior.
- Define versioned run, cycle, stage event, and error schemas.
- Add `TraceSink`, an in-memory test sink, correlation IDs, and stage timings.
- Expose the complete `SocialState` and scheduler result to the trace layer.
- Add tests proving live source isolation and unchanged observation responses.

**Gate:** an existing synthetic recording produces an ordered, validated trace
for every frame, including skipped decisions and failures.

### Phase 2: Recorder and Recording Catalog

- Add SQLite migrations, artifact storage, async persistence, and storage health.
- Add start/stop recording and run-list/detail APIs.
- Export/import the portable bundle while accepting existing observation-only
  JSONL as a partial recording.
- Add quotas, retention configuration, incomplete-run recovery, and checksums.

**Gate:** restart the server, reopen a recording, verify artifact hashes, and
reconstruct every displayed cycle without Ollama or Navel.

### Phase 3: Replay Engine

- Implement playback and isolated reprocessing workers.
- Add pacing, pause, step, restart, cancel, and deterministic seek-from-start.
- Record parent/child run provenance and configuration snapshots.
- Add original-versus-reprocessed alignment and typed diff service.

**Gate:** with a deterministic stub policy, two reprocess runs have identical
canonical state, scheduler, and intent artifact hashes, excluding run metadata
and measured timing.

### Phase 4: Basic Web App

- Add the dashboard, recording library, and shared live/replay workspace.
- Implement SSE reconnection, pipeline stage strip, timeline, 2-D scene,
  transition inspector, JSON viewer, and error states.
- Add comparison view and clear live/playback/reprocess badges.
- Add browser end-to-end coverage for record, replay, step, inspect, and compare.

**Gate:** a developer can find the first divergent stage between two runs
without reading server logs or opening raw recording files.

### Phase 5: Connected Robot Hardening

- Add explicit source registration/heartbeat or derive presence consistently
  from observation traffic.
- Test at expected Navel rates with multiple interleaved adapter IDs.
- Add stale clock, duplicate/out-of-order frame, disconnect/reconnect, slow LLM,
  queue saturation, and disk-full failure tests.
- Make the deployment local/LAN-safe and document firewall/storage setup.

**Gate:** one live Navel source and one concurrent replay remain isolated; a
failure in tracing, storage, the browser, or replay cannot command or stall the
robot collector.

## 9. Verification Strategy

- Unit tests for event ordering, typed semantic deltas, run isolation, and
  artifact hashing.
- Golden replay tests using checked-in synthetic JSONL and a deterministic
  policy.
- Flask integration tests for recording lifecycle, SSE resume, pagination,
  malformed import, and replay controls.
- Multi-source tests with interleaved frames and intentionally colliding track
  IDs.
- Failure injection for invalid observations, non-monotonic timestamps, LLM
  timeout/invalid output, full queue, unwritable storage, and interrupted runs.
- Browser tests for timeline synchronization and inspector correctness.
- A load target based on measured Navel traffic; initially validate at least
  four 10 Hz sources while the UI is connected and a replay is running.

Suggested provisional performance budgets are under 5 ms p95 instrumentation
overhead per observation (excluding asynchronous disk flush), zero unreported
drops, and a throttled UI update rate of 5-10 Hz. Confirm or revise these from a
short load-test baseline rather than treating them as safety guarantees.

## 10. Privacy and Safety

- Default to recording structured observations only. Image, audio, gaze,
  expression, and identity-related storage is opt-in and visibly indicated.
- Use pseudonymous source/participant identifiers and keep consent metadata
  separate from sensor records.
- Make retention, deletion, export, and storage location explicit.
- Do not expose raw files through arbitrary paths; artifacts are retrieved by
  validated IDs.
- Bind to localhost by default. Add authentication, authorization, CSRF
  protection, and TLS before exposing the monitor beyond a trusted lab network.
- Redact secrets and configurable sensitive fields from prompts/errors.
- Keep replay and UI services unable to import or call any robot actuation API.

## 11. Recommended First Vertical Slice

Implement Phases 1-3 for one checked-in synthetic recording before building the
full dashboard:

1. Reprocess `newcomer_requests_guidance.jsonl` through a fresh run context.
2. Persist the observation, social state, scheduler result, and intent at every
   frame.
3. Expose one run-detail endpoint and SSE feed.
4. Build a single workspace page with the stage strip, timeline, step control,
   semantic transition list, and JSON inspector.
5. Only then attach the same instrumentation to live Navel ingestion.

This slice proves the difficult parts—trace fidelity, temporal replay,
transition inspection, and run isolation—before expanding into catalog and
fleet-style dashboard features.
