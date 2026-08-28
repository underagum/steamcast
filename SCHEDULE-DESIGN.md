# SteamCast Absolute Schedule Architecture — Design Review (v2)

**Status:** Approved design · **Scope:** daemon scheduling only (interactive TUI cast scheduling untouched)
**Date:** 2026-08-27 · **Target:** v1.6.2+ daemon schedule redesign

---

## 0. Design Summary (TL;DR)

Replace the duration-based schedule (`duration_hours` in `~/.steamcast/config.json` + one
`steamcast.timer`) with **two absolute one-shot systemd timers** driving two **oneshot
dispatcher services**, plus a **`~/.steamcast/schedule.json`** file as the single
authoritative record of the window. The daemon itself reads that file and self-stops at the
absolute end time (defense-in-depth against any timer failure). Reboot survival comes from
`Persistent=true` + window-checking triggers — with **zero auto-start leak** because the main
service stays `disabled` and nothing ever re-enables it.

**Winner (one design, no alternatives offered):**

| Concern | Decision |
|---|---|
| Start trigger | `steamcast-schedule-start.timer` (one-shot, `Persistent=true`) → `steamcast-schedule-start.service` (oneshot, `steamcast daemon schedule --start-trigger`) |
| Stop trigger | `steamcast-schedule-stop.timer` (one-shot, `Persistent=true`) → `steamcast-schedule-stop.service` (oneshot, `steamcast daemon schedule --stop-trigger`) |
| Authoritative record | `~/.steamcast/schedule.json` `{"start": ISO, "end": ISO, "created": ISO}` |
| Daemon-side stop | Monitor loop re-reads `schedule.json` every tick; `now >= end` → self-stop. No `duration_hours` anywhere in the daemon |
| Reboot survival | `Persistent=true` on both timers + window-checking triggers. Main service stays **disabled** → no auto-start leak |
| Privilege model | Scoped NOPASSWD sudoers (`/etc/sudoers.d/steamcast`), auto-installed by the CLI on first `schedule` set. Verified: agam currently has **no** NOPASSWD sudo, so this is a hard prerequisite |
| Self-cleanup | Stop-trigger removes both timers + `schedule.json` at window end → pristine end state |
| `duration_hours` | Removed from daemon config entirely (interactive TUI `cast` keeps its own unrelated duration feature) |

**Why not a single timer with two `OnCalendar=` lines?** A timer has exactly ONE `Unit=`
directive — both calendar events activate the *same* service. Start and stop have different
ExecStarts, and systemd does not tell the activated unit which calendar event fired. A
self-dispatching service that guesses from `now` vs. `schedule.json` is ambiguous at the end
moment (`now >= end` may be false by a second → daemon started at end time, no second fire →
runs forever). Two timers encode each moment unambiguously in the unit itself. Two timers also
give clean `systemctl list-timers` output and per-action journald units.

---

## 1. Unit Files (full contents)

All paths use `/home/agam/.local/bin/steamcast` and `User=agam` (matches existing service).
The two timer files are **rewritten on every `schedule` set**; the two dispatcher service
files are **static** (written on first set, removed only by `service uninstall`).

### 1.1 `/etc/systemd/system/steamcast-schedule-start.service` (static)

```ini
[Unit]
Description=SteamCast schedule — start broadcast window
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=agam
ExecStart=/home/agam/.local/bin/steamcast daemon schedule --start-trigger
```

No `[Install]` — never enabled, only activated by its timer. No `Restart=`, no `RemainAfterExit`.

### 1.2 `/etc/systemd/system/steamcast-schedule-stop.service` (static)

```ini
[Unit]
Description=SteamCast schedule — stop broadcast window
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=agam
ExecStart=/home/agam/.local/bin/steamcast daemon schedule --stop-trigger
```

### 1.3 `/etc/systemd/system/steamcast-schedule-start.timer` (dynamic — written per schedule)

```ini
[Unit]
Description=SteamCast schedule — start at configured time

[Timer]
OnCalendar=2026-09-01 09:00:00
Unit=steamcast-schedule-start.service
Persistent=true
AccuracySec=1s

[Install]
WantedBy=timers.target
```

### 1.4 `/etc/systemd/system/steamcast-schedule-stop.timer` (dynamic)

```ini
[Unit]
Description=SteamCast schedule — stop at configured time

[Timer]
OnCalendar=2026-09-01 18:00:00
Unit=steamcast-schedule-stop.service
Persistent=true
AccuracySec=1s

[Install]
WantedBy=timers.target
```

Notes:
- `OnCalendar` accepts `YYYY-MM-DD HH:MM:SS` absolute local time (verified:
  `systemd-analyze calendar` normalizes it and resolves "Next elapse" correctly in WIB).
- `Persistent=true` = "fire immediately at next activation (boot) if the trigger time was
  missed while the timer was inactive (system powered off)". This is the reboot-survival
  mechanism. It is **safe here** because the triggers re-check the window against
  `schedule.json` — a boot after the end time can never start the daemon.
- `AccuracySec=1s` honors "absolute" semantics (default 1-min slack would drift start/stop).
- Timezone: timers evaluate in local time (WIB on The Box, no DST in Indonesia — non-issue;
  if TZ ever changes, re-verify armed timers).

### 1.5 `/etc/sudoers.d/steamcast` (one-time, self-service install)

```sudoers
# Installed by: steamcast daemon schedule <start> <end>   (first use)
# Removed by:   steamcast daemon service uninstall
agam ALL=(ALL) NOPASSWD: /usr/bin/systemctl *steamcast*, /usr/bin/systemctl daemon-reload, /usr/bin/rm /etc/systemd/system/steamcast*
```

**Why required:** start/stop triggers run inside systemd context (no TTY, no password
prompt). The box was verified to have **no** NOPASSWD sudo (`sudo -n true` → "interactive
authentication is required"). The rule is scoped to steamcast-named units + daemon-reload +
removal of steamcast unit files only — no blanket `ALL`. The CLI installs it via the
interactive sudo the user already has, then verifies with `sudo -n true`. If the user refuses,
`schedule` set **aborts with instructions** — never a silent partial install.

---

## 2. Exact Command Sequences

### 2.1 SET (`steamcast daemon schedule "20260901 09:00" "20260901 18:00"` → CLI runs these)

```bash
# 0. One-time only: ensure passwordless sudo (CLI self-service via interactive sudo)
echo 'agam ALL=(ALL) NOPASSWD: /usr/bin/systemctl *steamcast*, /usr/bin/systemctl daemon-reload, /usr/bin/rm /etc/systemd/system/steamcast*' \
  | sudo tee /etc/sudoers.d/steamcast >/dev/null
sudo chmod 440 /etc/sudoers.d/steamcast
sudo -n true || { echo "passwordless sudo still failing"; exit 1; }

# 1. Clear any existing schedule FIRST (idempotent; covers legacy steamcast.timer too)
sudo -n systemctl stop steamcast.timer steamcast-schedule-start.timer steamcast-schedule-stop.timer 2>/dev/null || true
sudo -n systemctl disable steamcast.timer steamcast-schedule-start.timer steamcast-schedule-stop.timer 2>/dev/null || true
sudo -n rm -f /etc/systemd/system/steamcast.timer \
             /etc/systemd/system/steamcast-schedule-start.timer \
             /etc/systemd/system/steamcast-schedule-stop.timer
rm -f ~/.steamcast/schedule.json

# 2. Write the 4 unit files (2 timers dynamic + 2 dispatcher services static) via sudo tee

# 3. Reload + arm both timers
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now steamcast-schedule-start.timer steamcast-schedule-stop.timer

# 4. Keep the main service boot-neutral (only timers may start the daemon)
sudo -n systemctl disable --quiet steamcast.service

# 5. Write authoritative record
printf '{\n  "start": "2026-09-01T09:00:00",\n  "end": "2026-09-01T18:00:00",\n  "created": "2026-08-27T17:30:00"\n}\n' > ~/.steamcast/schedule.json

# 6. Verify
systemctl list-timers steamcast-schedule-start.timer steamcast-schedule-stop.timer --all --no-pager
```

Ordering matters: **old schedule fully cleared before new units are written** (prevents a
half-armed state), and `schedule.json` written **after** the timers arm (a trigger firing in
the gap would find no file and no-op — safe).

### 2.2 CLEAR (`steamcast daemon schedule --clear`)

```bash
sudo -n systemctl stop steamcast.timer steamcast-schedule-start.timer steamcast-schedule-stop.timer 2>/dev/null || true
sudo -n systemctl disable steamcast.timer steamcast-schedule-start.timer steamcast-schedule-stop.timer 2>/dev/null || true
sudo -n rm -f /etc/systemd/system/steamcast.timer \
             /etc/systemd/system/steamcast-schedule-start.timer \
             /etc/systemd/system/steamcast-schedule-stop.timer
rm -f ~/.steamcast/schedule.json
sudo -n systemctl daemon-reload
# NOTE: a running daemon is NOT touched — clear cancels automation only.
# Prints: "Daemon still running — stop manually: steamcast daemon stop" if active.
```

### 2.3 STATUS (`steamcast daemon schedule` — no args, **no sudo needed**)

```bash
cat ~/.steamcast/schedule.json                       # authoritative window
systemctl show steamcast-schedule-start.timer -p ActiveState -p NextElapseOnRealTime --no-pager
systemctl show steamcast-schedule-stop.timer  -p ActiveState -p NextElapseOnRealTime --no-pager
systemctl is-active steamcast.service
```

Sample output (CLI formats it):

```
📅 Schedule armed:
   Start:  2026-09-01 09:00  (in 2d 3h)
   End:    2026-09-01 18:00  (9h window)
   Start timer: active — next Tue 2026-09-01 09:00:00 WIB
   Stop timer:  active — next Tue 2026-09-01 18:00:00 WIB
   Daemon:  not running (will start via timer)
   Clear:   steamcast daemon schedule --clear
```

Window-active variant adds `Daemon: 🔵 running (PID x)` or `Daemon: ⚪ down (window active —
start manually or reboot to auto-resume)`. Stale state (no `schedule.json` but timers present)
prints `⚠ completed/interrupted schedule — run --clear to tidy`.

### 2.4 Trigger handlers (what the dispatcher services execute)

**`--start-trigger`** (fires at window start, and at boot if start was missed):
1. Read `~/.steamcast/schedule.json`. Missing → print + exit 0.
2. `now = datetime.now()`. If `start <= now < end` → `sudo -n systemctl start steamcast.service`
   (plain `start`, **never** `enable`). Already active → no-op success. Log outcome.
3. `now < start` (clock jump) or `now >= end` (missed window) → print + exit 0. **Never starts
   outside the window** — this is what makes `Persistent=true` leak-proof.

**`--stop-trigger`** (fires at window end, and at boot if end was missed):
1. Read `schedule.json`. Missing → exit 0.
2. **Stale-trigger guard:** if `schedule.json.end > now` → the schedule was replaced after
   this trigger armed → exit 0, touch nothing (protects a fresh schedule from a racing cleanup).
3. Stop daemon: `steamcast daemon stop` (its `INVOCATION_ID` guard routes to direct PID
   SIGTERM — **no sudo needed for the stop itself**). Daemon not running → treated as success
   (idempotent; see daemon.py change #4).
4. Cleanup (best-effort, `|| true`): `sudo -n systemctl disable <both timers>`,
   `sudo -n rm -f <both timer files>`, `sudo -n systemctl daemon-reload`,
   `os.unlink(~/.steamcast/schedule.json)`. Exit 0.

After a completed window the box is pristine: no timers, no `schedule.json`, service disabled.

---

## 3. Edge-Case Table

| # | Situation | Behavior | Why it's right |
|---|---|---|---|
| 1 | `end <= start` | Rejected at parse (CLI + TUI) | Existing validation, kept |
| 2 | `start <= now` | Rejected | Existing validation, kept |
| 3 | Re-schedule while armed | Set clears old units first, then writes new (2.1 step 1) | No half-armed state |
| 4 | Re-schedule while daemon running in old window | Daemon re-reads `schedule.json` each tick → obeys **new** end (≤5s); stale stop-trigger guard (end > now) blocks racing cleanup | New schedule wins; no zombie end |
| 5 | Manual `daemon start` while schedule armed | `systemctl start` only (no `enable`) + warning "📅 schedule armed — stop at END applies; will NOT survive reboot; clear schedule for persistent start" | Kills the auto-start leak the old `_unified_start` would cause |
| 6 | Manual `daemon stop` mid-window | Daemon stops, service stays disabled; **timers stay armed**; stop-trigger later no-ops (idempotent). Window goes dark; status shows "window active, daemon down" | Stop means stop; schedule promise intact; `--clear` is the explicit cancel |
| 7 | **Reboot mid-window** | Boot → start timer `Persistent` fires (start missed while off) → trigger checks window → `systemctl start`. Broadcast resumes, stops at absolute end | **Problem B solved** — no manual intervention |
| 8 | Boot before start | Start timer not yet due → no fire; fires normally at start | Correct |
| 9 | Off before start, boots mid-window | `Persistent` fires at boot → window check passes → broadcast runs for the remainder | Predictable: "as soon as possible within window, stop at end" |
| 10 | Off entire window, boots after end | Start-trigger no-ops (now ≥ end); stop-trigger cleans up → pristine state | Missed window, zero side effects |
| 11 | Suspend across end | Timer fires on resume; **and** daemon self-stop via `schedule.json` | Double coverage |
| 12 | Daemon crash mid-window | `Restart=on-failure` → daemon re-reads `schedule.json` → continues; crash after end → self-stops immediately (exit 0, no restart loop) | Self-healing, bounded |
| 13 | Stop fires, daemon not running | Idempotent exit 0 (daemon.py change #4) | One-shot oneshot must not error |
| 14 | Stop fires, daemon is a non-systemd (double-fork) instance | `cmd_stop` PID-file SIGTERM works regardless of supervision mode | Robust |
| 15 | `--clear` while daemon running | Daemon keeps running; no more auto-stop (file + timers gone) | Clear = cancel automation, never a surprise kill |
| 16 | `service install` while schedule armed | Warning printed; install proceeds (user's explicit action). Documented: service enable + armed schedule = daemon will boot-start — clear first for the designed behavior | Explicit over implicit |
| 17 | `service uninstall` | Also removes both schedule timers, both dispatcher services, legacy `steamcast.timer`, `schedule.json`; daemon-reload | No residue |
| 18 | Upgrade with legacy schedule armed | New clear/set handle legacy `steamcast.timer` + `duration_hours` config (migration §6) | Forward-compatible |
| 19 | No NOPASSWD sudo | Set aborts with one-line install instructions (`sudo tee /etc/sudoers.d/steamcast ...`) | No silent partial state |
| 20 | Windows / `.exe` build | All schedule/trigger paths guarded `sys.platform != "win32"`; daemon's `schedule.json` read is a plain file check → no-op | Cross-platform intact |
| 21 | Start fails (ffmpeg missing, port busy) | Trigger exits non-zero (journald); if service started then died → `on-failure` retries every 10s; stop-trigger ends the loop at window end | Failure is loud, bounded by window |
| 22 | Clock jump / NTP slew | Trigger window-checks make wrong-time fires no-ops; next boot re-evaluates | Fail-safe |

---

## 4. Code Changes (described — no code written)

### 4.1 `steamcast.py`

| Function | Change |
|---|---|
| New constants | `SCHEDULE_FILE = ~/.steamcast/schedule.json`; the four unit paths; sudoers path |
| New `_read_schedule()` | Tolerant JSON parse → `{start, end}` datetimes or `None` (missing/corrupt → None) |
| New `_ensure_nopasswd_sudo()` | Installs `/etc/sudoers.d/steamcast` via interactive sudo (`tee`), `chmod 440`, verifies `sudo -n true`; returns bool |
| New `_systemctl(args, check=...)` | Thin wrapper: `sudo -n systemctl <args>` with `check` semantics (all schedule paths use `-n`; interactive CLI paths unchanged) |
| Rewrite `_do_schedule(start_dt, end_dt, clear)` | Clear: rm legacy + both new timers, rm `schedule.json`, daemon-reload (2.2). Set: ensure NOPASSWD → clear-old → write 2 timers + 2 static dispatcher services → daemon-reload → `enable --now` both timers → `disable --quiet steamcast.service` → write `schedule.json` → verify. **No `duration_hours` write** |
| `_cmd_schedule()` | Status branch reads `schedule.json` + `systemctl show` both timers + `systemctl is-active steamcast.service` (no sudo). New verbs: `--start-trigger`, `--stop-trigger` (both: pure logic, no prompts, fast, `sys.platform`-guarded) |
| `_unified_start()` | If `schedule.json` exists → `systemctl start` only (no enable) + warning (edge #5). Else unchanged (`enable --now`) |
| `_schedule_menu()` (TUI) | Replace `steamcast.timer` existence check with `schedule.json` + both timers; same UX |
| `_install_systemd_service()` | Print warning if schedule armed (edge #16) |
| `_uninstall_systemd_service()` | Also remove schedule timers, dispatcher services, legacy timer, `schedule.json` (edge #17) |

### 4.2 `daemon.py`

| Function | Change |
|---|---|
| `load_config()` | Stop merging `duration_hours` (key ignored; deleted from file by the next `schedule` set). Defaults dict drops the key |
| `DaemonManager.start()` (both branches) | Drop `duration = config.get("duration_hours")`; call `_run_engine(games, restart_every)` |
| `_run_engine()` | Remove `end_at = start + timedelta(hours=duration)`. **Each monitor-loop tick** (or every 6th tick): `end_at = _schedule_end_from_file()`; `if end_at and now >= end_at: _stop_all_streams("schedule_end")`. Logs "Absolute schedule end reached — stopping." |
| New `_schedule_end_from_file()` | Read `~/.steamcast/schedule.json`; return `datetime.fromisoformat(end)` or `None` (missing/corrupt). A 3-line file read every 5s is negligible; **this is the drift fix and the timer-failure backstop** |
| `stop()` | No-PID case: if `INVOCATION_ID` set → log + return success (idempotent for ExecStop and stop-trigger contexts — a missing PID is normal there). Otherwise current behavior (systemctl fallback / raise) |

Safety guards preserved: `INVOCATION_ID` recursion guard, `_disable_service_if_enabled`
guard, PID-guarded `remove_pid`, port-release verification, `SO_REUSEADDR`, HTTP-API status
fallback — all untouched.

---

## 5. Reboot-Survival Recommendation (question d — clear winner)

**Winner: `Persistent=true` on both timers + window-checking triggers.**
Survives reboot (edges 7–10), self-heals, and **cannot leak**: the main service is `disabled`
and the only start path (start-trigger) refuses to start outside `[start, end)`. A leak would
require someone to explicitly `systemctl enable steamcast.service` (edge #16 warns).

Rejected:
- **Accept reboot death** — fails Problem B outright; user already complained.
- **Enable service during window** — survives reboot but any forgotten cleanup auto-starts the
  daemon on every future boot forever; exactly the "unexpected auto-start" the user forbids.
- **Periodic guard timer** (OnBootSec reconcile) — redundant once `Persistent` + window-check
  triggers exist; extra unit, extra failure surface, no additional guarantee.
- **Single timer + self-dispatching service** — ambiguous at the end moment (see §0).

---

## 6. Migration Path (current single-timer + duration → v2)

1. **Ship code** (v1.6.2 → v2.0.0): daemon.py + steamcast.py changes above; version bump;
   CHANGELOG entry; README schedule section.
2. **Legacy armed schedule?** Current box state: no timer installed, service disabled, daemon
   not running (verified 2026-08-27) → no action. General case: run
   `steamcast daemon schedule --clear` with the **new** binary — new clear also removes the
   legacy `steamcast.timer`. If a legacy daemon is mid-window, it still honors its old
   in-memory duration end; restart the daemon after upgrade to pick up the new logic.
3. **Config cleanup:** `duration_hours` in `~/.steamcast/config.json` is ignored by the new
   `load_config()` and deleted on the next `schedule` set. Manual `daemon start` now means
   **indefinite until `daemon stop`** (predictable, documented).
4. **First new `schedule` set** installs the sudoers file (interactive sudo prompt), then arms
   the two timers.
5. **Verification checklist** (§7), then update `references/systemd-daemon-patterns.md` in the
   steam-broadcast skill with the two-timer + schedule.json pattern.

---

## 7. Verification Plan (battle-test before calling it done)

1. `systemd-analyze verify` on all four unit files → no errors.
2. `systemd-analyze calendar "<OnCalendar value>"` → "Next elapse" equals the intended moment.
3. **Live 5-minute window test:** `schedule "now+3min" "now+6min"`;
   `journalctl -f -u steamcast-schedule-start.service -u steamcast-schedule-stop.service -u steamcast.service`;
   confirm: timer fires → daemon starts → streams LIVE → stop-trigger fires at end → daemon
   exits 0 (no restart) → timers dead + `schedule.json` gone → `systemctl is-enabled steamcast.service` = disabled.
4. **Reboot mid-window test:** arm a window, wait for start, `sudo reboot`, confirm daemon
   returns within ~2 min of boot (Persistent fire + network-online) and stops at the absolute end.
5. **Missed-window test:** `--start-trigger` by hand with an expired `schedule.json` → exit 0,
   nothing started. `--stop-trigger` twice → second is a clean no-op.
6. **Race test:** re-set a new schedule while the old stop-trigger would fire (end < new end)
   → new schedule intact, no cleanup ran.
7. **Idempotency:** `--clear` twice; `schedule` set twice in a row.
