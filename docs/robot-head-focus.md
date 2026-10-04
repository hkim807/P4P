# Immediate robot head focus

The robot client can issue `look_at_person(uid, head)` as soon as an unambiguous
person appears in an SDK perception packet. This occurs before conversion,
rate limiting, HTTP delivery, and rule evaluation. It is a **provisional visual
focus**, not the interaction lock and not permission to approach or speak.

Head focus is off by default. On Navel, after checking ordinary sensor output,
run:

```bash
python3 -m robot.navel_client.main \
  --server http://192.168.1.100:6060 \
  --head-focus \
  --head-focus-magnitude 0.5 \
  --head-focus-grace 0.75
```

Use the computer's real LAN address, or the tunnel address in the runbook.
`--head-focus` also works with `--print-only` for a local trial. It sends real
head commands even if `--decision-dry-run` is present; that flag only keeps
policy response handlers in logging mode.

## Selection behavior

- One detected person with a valid nonnegative integer UID triggers one SDK
  `look_at_person` command. The robot's SDK is responsible for following that
  UID after the command.
- The current UID remains selected while it appears, even if other people enter
  the frame. Repeated packets do not resend the command.
- If that UID disappears or perception stops, the local selection is held for
  the grace period. A different UID cannot take over during that period.
- Once the grace period expires, the next frame containing exactly one person
  with a valid UID can start a new provisional focus. Frames with multiple
  people or unknown UIDs cannot start one.
- A failed SDK send is logged, and acquisition can be retried after one second.
  Raw sensor streaming continues.

When the app server returns a validated target lock, its selected UID pins
local head acquisition. A changed UID cannot take over during the server's
missing or unresolved state. The pin expires after three seconds without a
valid response; see the [target lock guide](target-lock.md).

The local selection is cleared when the client stops. The
[public Navel SDK reference](https://doc.navelrobotics.com/api/communication.html)
documents `look_at_person` but no explicit focus cancel operation. The local
expiry and stop paths therefore **do not promise that the physical head stops
tracking**. Verify the installed robot's behavior before relying on focus
release or using this while the fixed route is moving.

## On-robot validation

With space around the robot and the route stopped, check one visible person:
the stderr log should show `head_focus=acquired uid=...` immediately and the
head should follow. Keep the same person in view for several packets; there
should be one acquisition command. Add a second person; the current focus
should stay on the original UID. Have the selected person look away briefly,
then return with the same UID; focus should remain selected. Repeat with a
changed UID and check that no new command is issued during the grace period.
Finally, leave the view, wait past grace, and stop the client. Observe what the
physical head does after loss and shutdown; record whether the installed SDK
needs a separate neutral gaze or head reset command.

The existing seven recordings contain raw observations and can exercise future
logical locking, but they cannot demonstrate actual head motion or the SDK's
focus release behavior. A changed UID is still a new raw track; this feature
does not reidentify the same person.
