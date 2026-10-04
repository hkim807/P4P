# Target lock lifecycle

With social processing enabled, the app server maintains one logical
interaction lock per recording session.
The lock identifies an exact `(session_id, uid, track_epoch)` and produces a
`target_lock` object alongside the existing pure `policy_decision` in each
successful social response. The robot's dry-run dispatcher uses
`target_lock.effective_decision`. The pure rule remains available for audit.

## States

| State | Meaning | Effective decision |
| --- | --- | --- |
| `UNLOCKED` | No selected target; acquire only when exactly one track is visible | Pure `CONTINUE` or `DEFER` |
| `LOCKED` | Exact UID and track epoch visible | Pure proposal if it targets this track; otherwise `DEFER` |
| `MISSING` | Selected track absent within the hold window | `DEFER / LOCKED_TARGET_MISSING` |
| `TENTATIVE_RETURN` | One different UID or epoch is visible | `DEFER / IDENTITY_UNRESOLVED` |
| `AMBIGUOUS` | Several other tracks are visible | `DEFER / MULTIPLE_RETURN_CANDIDATES` |
| `COOLDOWN` | Lock released by timeout, stream gap, or valid no-attention/away evidence | `DEFER / LOCK_COOLDOWN` |

The default hold is **2 seconds** from the selected track's last observation.
The default release cooldown is **1 second**. Both use robot source timestamps,
so offline replay has the same transitions as live HTTP. A source-frame gap
longer than the hold releases the lock even if a custom tracker retains the
same UID. New server sessions start with no lock.

Same UID **and** track epoch within the hold returns to `LOCKED`. A changed UID
or a new epoch is only a candidate. No samples or social evidence are copied
between tracks, and no candidate is automatically rebound to the old lock.
Time and distance alone cannot establish that the candidate is the same human.
Once the old lock expires and cooldown ends, a sole visible person may get a
*new* lock ID. The system does not claim that this is a return of the old person.

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
expiry, transition events, and the effective decision with its reason.

With `--head-focus`, a fresh, validated server response pins the robot's
provisional head selection to the server's locked UID. During `MISSING`,
`TENTATIVE_RETURN`, and `AMBIGUOUS`, it will not issue a new head command for
a different UID. If server responses stop for three seconds, this pin expires
and local provisional focus resumes. The public Navel SDK still has no
documented focus cancel command, so a local lock release does not prove that
physical head motion stops.

## Remaining work

Cross-UID reassociation needs labeled return and impostor trials plus reliable
spatial evidence under head motion. The current recordings lack the required
identity labels and measured head pose. Completion feedback, a greeting
cooldown tied to actual completion, route pause/resume, and physical approach
and speech commands remain to be built. `APPROACH` and `ENGAGE` here are still
dry-run proposals, not executable robot commands.
