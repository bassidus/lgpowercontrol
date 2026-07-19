# LGPowerControl — Architecture & Troubleshooting Guide

Internal documentation for developers. Explains how the pieces fit together,
why they are built the way they are, and how to debug them. For user-facing
docs, see [README.md](README.md).

Most TV behavior described here was verified empirically against an LG
OLED42C35LA (WebOS) on KDE Plasma/Wayland with NetworkManager. Other models
may differ in timing, but the state machine has held so far.

## Big picture

The project mirrors the PC's display power state onto an LG WebOS TV used as
a monitor:

| PC event | TV action | Triggered by |
|---|---|---|
| Boot | ON (WoL + `turn_screen_on`) | `lgpowercontrol-boot.service` |
| Screen blanks on idle (DPMS off) | `turn_screen_off` | monitor service |
| Screen off ≥ 10 min | escalate to full `power_off` | monitor service |
| Activity returns (DPMS on) | ON | monitor service |
| Suspend | full `power_off` | NM dispatcher `pre-down` (sleep-hook fallback) |
| Resume | ON | NM dispatcher `up` **and** monitor (deduplicated) |
| Shutdown (not reboot) | `power_off` | `lgpowercontrol-shutdown.service` |
| ~2 min before idle screen-off | desktop warning notification (Plasma only) | notify service |

Everything is plain bash + systemd + one Python tool
([bscpylgtv](https://github.com/chros73/bscpylgtv), installed in a private
venv) that speaks the WebOS WebSocket API. Deliberately no other
dependencies.

## Installed layout

The git repo is *not* the runtime. `install.sh` copies everything to fixed
locations; editing a script in the repo does nothing until reinstalled
(`sudo ./install.sh` is idempotent and preserves conf + pairing).

```
/opt/lgpowercontrol/
├── lgpowercontrol              # core CLI: ON | OFF | SCREEN_OFF
├── lgpowercontrol-monitor.sh   # DPMS watcher daemon (root)
├── lgpowercontrol-notify.sh    # Plasma TV-off warning daemon (user session)
├── lgpowercontrol.conf         # settings, sourced by every script
├── authorize.sh                # (re)pair with the TV
├── update.sh                   # self-updater (release or --dev)
├── VERSION
├── .aiopylgtv.sqlite           # TV pairing key — survives reinstall/update
└── bscpylgtv/                  # python venv with bscpylgtvcommand

/etc/systemd/system/
├── lgpowercontrol-boot.service      # oneshot: ON at boot
├── lgpowercontrol-shutdown.service  # oneshot: OFF at poweroff/halt, Conflicts=reboot.target
└── lgpowercontrol-monitor.service   # the DPMS watcher

/etc/systemd/user/
└── lgpowercontrol-notify.service    # per-session, WantedBy=graphical-session.target

/etc/NetworkManager/dispatcher.d/
├── 90-lgpowercontrol                # handles the 'up' event (resume)
└── pre-down.d/90-lgpowercontrol     # symlink to the same file — 'pre-down' event (suspend)

/usr/lib/systemd/system-sleep/
└── lgpowercontrol                   # fallback TV-off at suspend when pre-down never fires
```

## The core script: `lgpowercontrol`

Single entry point for all TV commands. Everything else — services,
dispatcher, user at the CLI — goes through it.

```
lgpowercontrol ON | OFF | SCREEN_OFF
```

- `ON` — broadcast WoL, then poll `get_power_state` (up to 10 × 1 s),
  resending WoL while the TV looks asleep; `turn_screen_on` once awake;
  optionally `set_input $HDMI_INPUT` (up to 15 retries — the TV may still be
  booting).
- `OFF` — `power_off` + touch `/run/lgpowercontrol-tv-off`.
- `SCREEN_OFF` — `turn_screen_off` (panel off, WebOS stays up).

Key internals:

- **`bscpylgtv()` wrapper** — runs `bscpylgtvcommand` against the pairing db,
  leaves raw output in `$bscpylgtv_out`, and condenses Python tracebacks to
  one log line. It maps `turn_screen_on` error `-102` to return code **102**
  without logging, because that error is ambiguous (see below).
- **`LGPC_SOURCE`** — env var set by each caller so every journal line shows
  who triggered the command: `boot`, `shutdown`, `dpms-monitor`,
  `nm-dispatcher`, `resume`, or `cli` (default).
- **`send_wol()`** — magic packet on UDP port 9, built and sent by
  `lgpc-wol.py`, a small stdlib-python script run with the system python3
  (no external `wol` tool). Every send goes out twice: broadcast *and*
  routed unicast to
  `$LGTV_IP`. The routed copy covers TVs on a different subnet/VLAN where
  broadcast can't reach (issue #12; relies on the TV answering ARP in
  standby, which WebOS networked standby does); each copy is a harmless
  no-op in the other's setup, so there is no setting to choose between them.

### Why ON is a polling loop, not one command

Two hard-won facts:

1. **WoL packets get lost.** On the TV's own subnet they must be *broadcast*
   (unicast needs an ARP reply a sleeping TV doesn't reliably give — the
   packet is silently dropped and the send still succeeds). On WiFi, packets sent right after
   resume are lost while the link settles, *even after `nm-online`
   succeeds*. So the loop keeps resending WoL until the TV's own state
   proves a packet has bitten.
2. **`turn_screen_on` error -102 is ambiguous.** It fires both when the TV
   is still asleep ("not waking") *and* when the screen is already on
   ("current sub state must be Screen Off"). Treating it as success without
   proof caused a false-success bug on WiFi resume (fixed in v2.8.1). Rule:
   -102 counts as success **only after** `get_power_state` has shown an
   awake state.

The loop therefore reads `get_power_state` each second and branches on it:

| Response | Meaning | Action |
|---|---|---|
| `{'state': 'Active'}` | on, screen on | `turn_screen_on` (−102 ⇒ ok) |
| `{'state': 'Screen Off'}` / `'Screen Saver'` | on, panel dark | `turn_screen_on` |
| contains `'processing': ...` | mid-transition — WoL has bitten | wait, don't resend |
| `{'state': 'Active Standby'}` | Always Ready standby — WoL was lost | resend WoL |
| `{'state': 'Suspend'}` | deep standby — WoL was lost | resend WoL |
| connection error | asleep or unreachable | resend WoL |

Unknown states fall into the catch-all "resend WoL" branch, which is safe by
design. Typical wake times once WoL bites: ~4 s from Always Ready, ~5 s from
deep standby, ~10 s on TVs without Always Ready.

Caveat on the `state` values: while `processing` is present they are not a
reliable indicator of which standby the TV woke from. Observed on the
OLED42C35LA (2026-07-16): waking 5 min after a `power_off` (which lands in
Always Ready) reported `'state': 'Suspend', 'processing': 'Screen On'` yet
completed in ~3 s — i.e. the TV can report `Suspend` mid-wake even from
Always Ready. Only trust the plain (no-`processing`) states for
asleep/awake decisions.

### ON deduplication

On resume, ON fires from **both** the NM dispatcher and the DPMS monitor
(display comes back). `turn_tv_on` takes a non-blocking `flock` on
`/run/lgpowercontrol-on.lock`; the loser exits 0 immediately.

## The monitor: `lgpowercontrol-monitor.sh`

Root daemon (`lgpowercontrol-monitor.service`), 1 s loop. Reads DPMS state
straight from sysfs — `/sys/class/drm/card*-*/{status,dpms}` — no session or
compositor dependency. "on" if *any* connected output is On; "off" if all
connected outputs are off; empty (indeterminate) if nothing readable, which
is ignored rather than acted on.

On a state change: new state `on` ⇒ `lgpowercontrol ON`, new state `off` ⇒
`lgpowercontrol SCREEN_OFF` — **unless** `/run/lgpowercontrol-sleep` exists, which means the DPMS-off is
part of a suspend and the dispatcher already powered the TV off (and the
network may already be gone).

### The 10-minute escalation (why OFF instead of staying at SCREEN_OFF)

The TV drops from screen-off into **deep standby on an internal ~13 min
timer that cannot be reset** — it ignores incoming WebSocket connections, so
keep-alive polling is impossible (tested; don't re-propose). Deep standby
means ~10 s wakes. The workaround: after 600 s of continuous DPMS-off the
monitor escalates to a full `lgpowercontrol OFF`. On TVs with *Always Ready*
enabled, `power_off` lands in Always Ready standby (~3–4 s wake) — note that
Always Ready only engages on `power_off`, never from screen-off. On TVs
without it, the escalation is wake-time-neutral. One-shot per screen-off
period; skipped while the sleep flag exists.

This is also why the old `BOOT_SHUTDOWN_MODE`/`MONITOR_MODE` conf options
were removed: with these constraints there is exactly one sensible command
per event, so they are hardcoded. Old confs still defining those keys are
harmless (sourced, never read).

## Suspend/resume: the NM dispatcher

`scripts/90-lgpowercontrol`, installed into
`/etc/NetworkManager/dispatcher.d/` with a symlink from `pre-down.d/`.
This is the **only** mechanism that works — the graveyard:

- **systemd sleep.target units**: NetworkManager tears the network down
  **~17 ms after** logind's `PrepareForSleep` signal; by the time a sleep
  unit runs, the network is gone. Projects that use sleep units (LG_Buddy
  et al.) work only by winning that race. (`NetworkManager-sleep.service`
  doesn't even exist as a unit to order against.)
- **Own logind delay inhibitor**: delays the kernel entering sleep, not NM's
  teardown — NM reacts to the signal, not the sleep itself.
- **PowerDevil `aboutToSuspend`**: milliseconds of margin, same race.
- **NM config options**: there is no "wait before sleep" setting.

What does work: scripts in `pre-down.d/` run **blocking** while the
connection is still up, and NM holds its own logind inhibitor until they
finish. So:

- **`pre-down` event** — only acts if logind's `PreparingForSleep` property
  is true (ordinary disconnects are ignored). Fires once per NIC, so the
  first invocation touches `/run/lgpowercontrol-sleep` and later ones exit.
  If `/run/lgpowercontrol-tv-off` exists (monitor already escalated to OFF),
  it skips the `power_off` — a second one would hang against a standby TV
  until the connect timeout and delay suspend. Otherwise: `lgpowercontrol
  OFF`, synchronously, before the network drops.
- **`up` event** — if the sleep flag exists this is a resume (flag absent =
  boot or cable replug ⇒ no-op). Removes the flag and launches
  `lgpowercontrol ON` **detached** via `systemd-run --collect`, because
  dispatcher scripts run sequentially and ON can retry for up to a minute —
  blocking would stall NM's whole dispatcher queue.

### The sleep-hook fallback: `scripts/lgpowercontrol-sleep`

Installed as `/usr/lib/systemd/system-sleep/lgpowercontrol`. Exists because
NM **skips devices whose NIC has Wake-on-LAN enabled** at sleep (trace:
`sleep: device eno1 has wake-on-lan, skipping`) — no deactivation, no
pre-down, and the network stays up through suspend. Found via issue #12 on
a stock Fedora KDE install; likely common on HTPCs set up to be woken over
the network.

The hook runs at systemd-sleep's `pre` phase, *after* NM's dispatcher queue
(NM holds a logind inhibitor until it finishes), so:

- sleep flag present ⇒ the dispatcher handled this suspend ⇒ no-op. This is
  what makes the hook safe to install everywhere.
- flag absent ⇒ pre-down never fired ⇒ the hook sends `lgpowercontrol OFF`
  itself. On WoL-NIC setups the network is still up here — this is **not**
  the teardown race that killed the pre-v2.3 sleep-unit designs, because on
  these setups there is no teardown at all.
- It respects `/run/lgpowercontrol-tv-off` (like the dispatcher) and passes
  `connect_retries=1` so setups where the network *is* already gone (e.g.
  bridges) waste one fast failed attempt instead of a retry cycle.
- **It owns the resume side too**: with the device never taken down there is
  no dispatcher `up` at resume, and the monitor can't be relied on — it may
  freeze before observing the DPMS-off, in which case resume shows no
  off→on transition and it stays silent (seen on p600s, 2026-07-17). So the
  hook's `post` phase fires `lgpowercontrol ON`, detached via `systemd-run
  --collect` like the dispatcher's, gated on its own flag.
- It uses its own flag (`/run/lgpowercontrol-hook-sleep`), never the
  dispatcher's: nothing would reliably clear the dispatcher's flag on these
  setups (no `up` fires), and a stale sleep flag causes the misbehavior
  described under "Flag files".

### Known suspend limitations (by design)

- **Bridged NICs**: NM detaches bridge ports ~1 ms into deactivation,
  *before* the pre-down window — no TV-off at suspend on bridged setups
  (the sleep hook fires but the network is already gone by then).
  Resume still works (dispatcher `up` on the bridge + the monitor).
- **systemd-networkd-only systems**: no dispatcher; the sleep hook may
  cover TV-off at suspend if networkd leaves the link up into the `pre`
  phase, but this is untested and not a supported claim. Boot/shutdown/idle
  still work.

## Flag files (all in `/run`, cleared on reboot)

| File | Set by | Cleared by | Meaning |
|---|---|---|---|
| `/run/lgpowercontrol-sleep` | dispatcher `pre-down` | dispatcher `up` | suspend in progress; monitor must not react to DPMS-off, and `up` = resume |
| `/run/lgpowercontrol-tv-off` | `turn_tv_off` | `turn_tv_on` | TV already powered off; suspend hook skips redundant `power_off` |
| `/run/lgpowercontrol-hook-sleep` | sleep hook `pre` | sleep hook `post` | this suspend is the sleep hook's (dispatcher didn't fire); `post` turns the TV on |
| `/run/lgpowercontrol-on.lock` | `turn_tv_on` (flock fd 9) | released on exit | dedupes concurrent ON from dispatcher + monitor |

A stale `lgpowercontrol-sleep` flag (dispatcher `up` never fired — e.g. the
network never came back after resume) would make the monitor ignore every
future screen-off and misroute the next `up` event as a resume. The monitor
self-heals this: on a DPMS-off transition with the flag present it verifies
logind's `PreparingForSleep`; if false, the flag is stale — it is removed
(logged as "Stale sleep flag removed") and the screen-off is handled
normally. The check runs only at off transitions, so a flag legitimately
still present right after resume (WiFi not settled yet, dispatcher `up`
pending) is left alone for the dispatcher's late ON.

## Notify service: `lgpowercontrol-notify.sh`

Plasma-only nicety: a desktop notification `OFF_WARNING_SECONDS` before the
screen (and thus TV) turns off on idle. Runs as a **user** service in the
graphical session (it needs the session D-Bus). Exits silently when
`OFF_WARNING_SECONDS=0` or when `kscreen-doctor`/`kreadconfig6` are missing
(non-Plasma).

How it works, and why it's this weird:

- **Plasma's idle dim is invisible on D-Bus.** It doesn't touch
  `org.kde.ScreenBrightness`, activates no KWin effect, emits no session-bus
  signals. Two earlier designs shipped and never fired (a ScreenBrightness
  listener and a kscreen-effect watcher). The *only* observable is the
  per-output "dimming" property in `kscreen-doctor -o` **text** output — it
  is not present in the `-j` JSON.
- So the service polls `kscreen-doctor -o` every `NOTIFY_POLL_SECONDS` for
  `dimming to <N>%` with N < 100.
- On dim: reads PowerDevil's timeouts from `powerdevilrc` (per power profile
  — AC/Battery/LowBattery via a `busctl --user` call; the Battery/LowBattery
  *default* timeouts are unverified estimates) and arms a background `sleep`
  timer for `off − dim − OFF_WARNING_SECONDS` seconds.
- On undim (user came back): kills the timer and closes any shown
  notification (ID kept in `$XDG_RUNTIME_DIR/lgpowercontrol-notify.id`).
- Notification is sent with raw `busctl call org.freedesktop.Notifications`
  — no libnotify dependency.
- If "Turn off screen" is disabled in PowerDevil the service exits (a
  warning would lie); if "Dim automatically" is disabled it stays up but
  logs that it can never fire.

Gotcha: PowerDevil's display group names (`display1`, `display3`, …) change
across sessions — never hardcode them.

## Install / update / pairing

- **`install.sh`** requires the TV to be **on** (ping check + MAC
  auto-detection from the neighbor table + pairing dialog). Offline install
  and installer-side WoL were considered and rejected — keep it simple.
  It runs `uninstall.sh --quiet` first (fresh start, also removes legacy
  artefacts from pre-2.x versions), preserving `.aiopylgtv.sqlite` across
  the wipe. Supported package managers: pacman/apt/dnf; the only packages
  it may install are python3 (+venv on Debian).
- **`authorize.sh`** loops `get_power_state` (which both triggers the TV's
  pairing dialog and validates the key). A *denied* dialog leaves a broken
  key file behind — hence the `rm -f` + retry loop.
- **`update.sh`** fetches the latest GitHub release tarball (or `--dev` for
  the dev branch head, which skips the version comparison since dev's
  VERSION file lags), copies the live conf over the extracted tree, and
  re-runs `install.sh`.

## Troubleshooting

All components log to the journal with one tag:

```
journalctl -t lgpowercontrol -f          # follow live
journalctl -t lgpowercontrol -b          # since boot
```

Every line is prefixed with its source: `boot:`, `shutdown:`,
`dpms-monitor:`, `nm-dispatcher:`, `resume:`, `notify-service:`, `cli:`.

Useful probes:

```bash
/opt/lgpowercontrol/lgpowercontrol ON          # exercise the full wake path by hand
/opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand \
    -p /opt/lgpowercontrol/.aiopylgtv.sqlite $LGTV_IP get_power_state
cat /sys/class/drm/card*-*/dpms                # what the monitor sees
ls /run/lgpowercontrol*                        # flag state
kscreen-doctor -o | grep -i dimming            # what the notify service sees
systemctl status lgpowercontrol-monitor
systemctl --user status lgpowercontrol-notify
```

Common symptoms:

| Symptom | Likely cause / where to look |
|---|---|
| TV doesn't wake on resume (WiFi) | WoL lost while link settles — normal; the loop should recover. Check journal for repeated "resending WoL"; if it gives up after 10 attempts, the link took >10 s. |
| TV "wakes" per log but screen stays dark | -102 false-success class of bug — verify the state table above still matches the TV's firmware. |
| Suspend hangs ~15 s | `power_off` against an already-off TV (tv-off flag missing?) or TV unreachable during pre-down. |
| No TV-off at suspend | Bridged NIC (unsupported), networkd-only (unsupported), or dispatcher not installed — check `/etc/NetworkManager/dispatcher.d/pre-down.d/`. |
| Monitor reacts to nothing | DPMS not exposed for the connector — inspect `/sys/class/drm/card*-*/dpms`. |
| Notify never fires | "Dim automatically" off in Plasma, or `kscreen-doctor -o` no longer prints "dimming to" (Plasma version change). |
| Pairing errors after TV factory reset | `sudo /opt/lgpowercontrol/authorize.sh`. |
| TV on another subnet/VLAN never wakes | The routed unicast copy should cover this — check that the TV answers ARP/ping in standby and that UDP port 9 isn't filtered between the subnets. |

## Development workflow

- `main` is what users clone and what `update.sh` installs from (releases);
  `dev` is the working branch. Release: bump `VERSION` on dev, fast-forward
  main, tag `vX.Y.Z`, `gh release create`. Pre-rewrite history (before
  2026-07-06) lives in the `legacy-main` tag.
- Test cycle on a live machine: edit in the repo, `sudo ./install.sh`
  (preserves conf + pairing), watch `journalctl -t lgpowercontrol -f`.
- Ground rules: no new dependencies; simplicity beats edge-case coverage;
  and don't relitigate the dead ends documented above — they were all
  tested.
