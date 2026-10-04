# Target lock lifecycle

With social processing enabled, the app server maintains one logical
interaction lock per recording session.
The lock has a stable `lock_id` and currently binds to one
`(session_id, uid, track_epoch)`. It produces a
`target_lock` object alongside the existing pure `policy_decision` in each
successful social response. The robot's dry-run dispatcher uses
`target_lock.effective_decision`. The pure rule remains available for audit.

## States

| State | Meaning | Effective decision |
| --- | --- | --- |
| `UNLOCKED` | No selected target; acquire only when exactly one track is visible | Pure `CONTINUE` or `DEFER` |
| `LOCKED` | Bound UID and track epoch visible, including after a guarded UID handoff | Pure proposal if it targets this track; otherwise `DEFER` |
| `MISSING` | Selected track absent within the hold window | `DEFER / LOCKED_TARGET_MISSING` |
| `TENTATIVE_RETURN` | One different UID or epoch is visible; handoff evidence is accumulating | `DEFER` with the current evidence reason |
| `AMBIGUOUS` | Several other tracks are visible | `DEFER / MULTIPLE_RETURN_CANDIDATES` |
| `COOLDOWN` | Lock released by timeout, stream gap, or valid no-attention/away evidence | `DEFER / LOCK_COOLDOWN` |

The default hold is **2 seconds** from the selected track's last observation.
The default release cooldown is **1 second**. Both use robot source timestamps,
so offline replay has the same transitions as live HTTP. A source-frame gap
longer than the hold releases the lock even if a custom tracker retains the
same UID. New server sessions start with no lock.

Same UID **and** track epoch within the hold returns immediately to `LOCKED`.
When Navel assigns a new UID, a guarded handoff can bind that new raw track to
the **same logical lock ID**. It requires all of the following:

1. The new track is acquired within 0.8 seconds of the old track's last sighting.
2. The new track is the only visible candidate; no competing person was seen
   alongside the old target or during the gap.
3. Both tracks have measured distance, and the distance change stays within
   `0.25 m + 1.5 m/s × elapsed time`. The candidate also moves smoothly between
   its own frames.
4. The same candidate appears in at least three frames spanning 0.15 seconds,
   with no inter-frame gap over 0.35 seconds. Neither side can use UID `0`, and
   each logical lock permits at most three handoffs by default.

While evidence accumulates, the effective decision is `DEFER`. The handoff
frame also returns `DEFER / UID_REBOUND_OBSERVE`. The new raw track retains its
own epoch and builds fresh gaze and motion evidence; no measurements are copied
from the old UID. The trace's `bound_tracks` lists the raw tracks associated
with the logical lock. If the checks fail, the old lock stays unresolved until
it returns or expires. After release and cooldown, a sole visible person may
get a *new* lock ID.

**This is a continuity heuristic, not proof of identity.** A different person
who enters alone at a similar distance can satisfy these checks. The pilot
recordings have no ground-truth identity labels, so their handoffs cannot
establish a false-transfer rate. Keep physical approach and speech disabled
until return-versus-impostor trials establish acceptable error rates.

## Run and inspect

Start the receiver with social processing and a lock trace:

```bash
python3 -m app.server \
  --social-output var/temporal-validation/live-social.jsonl \
  --lock-output var/temporal-validation/live-locks.jsonl
```

Either output flag enables the social pipeline; `--lock-output` writes one
lock state per accepted frame. Output paths must be new and distinct. The
configuration file is [config/target-lock.json](../config/target-lock.json).
Pass `--lock-config PATH` to the receiver to use other values.

Replay a raw recording with the same pipeline:

```bash
python3 -m app.lock var/recordings/01_approach_gaze.jsonl \
  --output var/temporal-validation/01-locks.jsonl
```

Replace the recording name with an existing file and use a new output path.
The replay accepts `--tracking-config`, `--temporal-config`, and `--lock-config`.
The lock trace includes `lock_id`, selected UID/epoch, candidate UID/epoch,
candidate frame count, bound raw tracks, expiry, transition events, and the
effective decision with its reason.

With `--head-focus`, a fresh, validated server response pins the robot's
provisional head selection to the server's locked UID. During `MISSING`,
`TENTATIVE_RETURN`, and `AMBIGUOUS`, it will not issue a new head command for
a different UID. On `REBOUND`, the same lock ID names the new UID and head
acquisition switches to it. If server responses stop for three seconds, this pin expires
and local provisional focus resumes. The public Navel SDK still has no
documented focus cancel command, so a local lock release does not prove that
physical head motion stops.

## Remaining work

The handoff thresholds need labeled return and impostor trials. Stronger
reassociation needs reliable spatial evidence under head motion; the current
recordings lack identity labels and measured robot head pose. Completion feedback, a greeting
cooldown tied to actual completion, route pause/resume, and physical approach
and speech commands remain to be built. `APPROACH` and `ENGAGE` here are still
dry-run proposals, not executable robot commands.
