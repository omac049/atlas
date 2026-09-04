# deploy/ — always-on runtime

Atlas runs as eight launchd agents that start at login and restart themselves on
failure. Verified working from `/Users/ocorral/Atlas` on 2026-08-18.

## Install

```bash
cp deploy/com.atlas.*.plist ~/Library/LaunchAgents/
for l in com.atlas.api com.atlas.monitor com.atlas.healthcheck com.atlas.backup com.atlas.study com.atlas.intel com.atlas.awake com.atlas.site; do
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$l.plist
done
launchctl list | grep com.atlas   # third column 0 = healthy
```

To stop everything: `launchctl bootout gui/$(id -u)/<label>` for each label.
After editing a plist, re-copy it and `bootout` + `bootstrap` that label — launchd
reads the installed copy in `~/Library/LaunchAgents`, not the one in this repo.

## Files

- **`com.atlas.api.plist`** — uvicorn on port 8010, bound to `0.0.0.0` so the
  dashboard is reachable from other devices on the local network (owner-accepted
  decision, 2026-08-24). What that exposes: a read-only, unauthenticated research
  API — paper-only flags, market evidence, no credentials, no order path, and no
  write endpoints beyond the internal overview capture. Revisit before ever
  carrying this plist to an untrusted network; reverting is deleting the two
  `--host` lines and `kickstart`-ing the agent. `KeepAlive` restarts it if it exits.
- **`com.atlas.monitor.plist`** — `pairs watch --live`. One instance only, per the
  one-monitor rule. Sets `PYTHONUNBUFFERED=1`, which is required, not optional:
  without it Python block-buffers stdout to a file and the log lags reality by
  many minutes, so a healthy monitor looks dead.
- **`com.atlas.backup.plist` + `atlas_backup.py`** — daily 03:30 snapshot via sqlite3's online
  backup API (safe while the services hold the database open; a plain `cp` of a live
  SQLite file can capture a torn write). Verifies each snapshot with `quick_check`
  before rotating, keeps 3, and only rotates its own `atlas-auto-*` files so
  hand-made checkpoints are never deleted. After a verified snapshot it also
  `VACUUM`s the live database — the monitor's nightly prune deletes rows but
  SQLite keeps the pages, so without this the file only grows (it hit 1.05 GB
  with 76% free pages before the first manual vacuum on 2026-08-20). Labels are the irreplaceable asset:
  Kalshi prunes settled market detail after ~6 weeks, so a lost database cannot
  simply be re-harvested.
- **`com.atlas.study.plist`** — Mondays 07:00, runs `atlas gaps study` to write the
  weekly 90-day-study report into `data/study/`. Installed 2026-08-19 but only
  added to the install loop above on 2026-08-20 — if `launchctl list` does not
  show it, bootstrap it from the loop.
- **`com.atlas.intel.plist`** — Mondays 07:15 (after the study report), runs
  `atlas intel report` to write the weekly Contract Divergence Report into
  `data/intel/` as markdown + JSON. Read-only over persisted evidence; this is
  the contract-intelligence deliverable adopted 2026-08-20.
- **`com.atlas.awake.plist` + `atlas_awake.py`** — holds a `caffeinate -si`
  assertion from ten hours before each scheduled release (the evening before an
  05:30 jobs/CPI print) through the end of its burst window, so the radar's
  30-second sampling is not slept through. Honest limits: a user agent cannot
  WAKE a sleeping Mac and cannot override lid-close sleep — the lid must be open
  (or an external display attached), AC power strongly recommended. The stronger
  alternative is a one-time root schedule, e.g.
  `sudo pmset repeat wakeorpoweron MTWRFSU 05:15:00`, which wakes the machine
  regardless; this agent is the no-sudo fallback and the two compose safely.
  Release times come from `atlas/release_calendar.py` — prune/extend that table
  and the holds follow.
- **`com.atlas.site.plist` + `atlas_site.py`** — daily 04:00 (after the backup),
  rebuilds the demand-test site (`docs/SITE.md`) into `dist/site` with
  `atlas site build --live`. Publishing is an opaque command the owner sets in the
  plist's `ATLAS_SITE_PUBLISH_CMD` once a host exists (with `ATLAS_SITE_BASE_URL`
  for canonicals); unset, the build stays local and the log says so. Hosting
  credentials live in the host CLI's own config, never in this repo.
- **`com.atlas.healthcheck.plist` + `atlas_healthcheck.py`** — a 60s liveness probe
  covering what `KeepAlive` cannot see: a process that is alive but wedged.
  Restarts the API when `/health` stops answering (after 2 consecutive misses, so a
  restart already in flight is not compounded) and the monitor when its log goes
  stale for 30+ minutes (six missed sweeps). Restarts go through
  `launchctl kickstart -k` so launchd stays the single owner; a bare kill plus a
  hand start would orphan the service from launchd. It refuses to restart into a
  health response that does not report `trading_enabled=false` — a broken
  paper-only invariant is a stop-and-report condition, not something to bounce.

Verified 2026-08-18: `kill -9` on the API and on the monitor each produced a new
PID within seconds, and a `SIGSTOP`-frozen API (alive, port dead, invisible to
`KeepAlive`) was detected and replaced by the watchdog.

## Do not move this checkout back under OneDrive

This repo previously lived in `~/Library/Group Containers/UBF8T346G9.../Documents/Atlas`
(the OneDrive standalone-suite container), where **launchd agents cannot run at all**.
macOS TCC blocks launchd-spawned processes from reading that path, in escalating stages:

| Symptom | Cause |
| --- | --- |
| exit 126 | launchd may exec a Mach-O binary there but may not **read** a text script (`/bin/bash <script>`) |
| exit 78 | `.venv/bin/atlas` is a console script (also text) |
| exit 78 | launchd opens `StandardOutPath` itself before exec and cannot create files there |
| fatal | the process cannot read `.venv/pyvenv.cfg` (`init_import_site`) or open `data/atlas.sqlite3` (`unable to open database file`) |

The first three have plist-level workarounds — which is why the agents here still
invoke `.venv/bin/python -m atlas.cli` rather than the `atlas` console script, and
log to `~/Library/Logs` rather than `data/`. The fourth has none. The API appeared
healthy for several minutes before dying the same way, so a short smoke test is not
sufficient evidence there.

Two further reasons to stay off OneDrive: a 1 GB live SQLite database syncing to the
cloud risks being copied mid-write, and `uv` fails outright on that path because it
contains colons (`error: path segment contains separator ':'`). At `~/Atlas`, plain
`uv sync` and `uv run` work normally.

## Limits of always-on

These agents keep Atlas alive while the Mac is **awake and logged in**. A closed lid
pauses collection regardless of any restart policy, and macOS may also throttle or
suspend background work on battery. For genuine 24/7 coverage — so releases like the
monthly CPI print and FOMC decisions are never missed — Atlas needs an always-on
host (a machine that does not sleep, or a small cloud server).
