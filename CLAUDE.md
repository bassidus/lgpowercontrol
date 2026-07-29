# CLAUDE.md

Project notes for lgpowercontrol — accumulated findings and working rules from development sessions (2026-07). Most findings below were verified empirically on Basse's machine ("p600s": CachyOS, KDE Plasma/Wayland, wired LAN + WiFi) with an LG OLED42C35LA.

## Working rules

- **Propose first, act after approval.** Questions like "can X be improved?" are requests for assessment, not implementation. Never edit code or commit without an explicit go-ahead; committing needs its own approval unless asked for in the same message.
- **No new dependencies without permission.** The project is deliberately minimal. Never add third-party programs or packages (to install.sh or the system) without asking first; present zero-dependency alternatives when they exist. (swayidle/libnotify were rejected on these grounds; kscreen-doctor is OK since it ships with Plasma.)
- **Keep it simple over covering edge cases.** Offline install and installer-side WoL wake were considered and rejected — pairing needs the TV on anyway ("keep it simple"). Don't re-propose unless Basse raises the pain point.

## Repository layout

- `main` is what users clone; `dev` is the experiment branch. Work happens on dev, main is fast-forwarded for releases.
- The lean multi-distro rewrite replaced the old main on 2026-07-06 (force-push); old history lives in the `legacy-main` tag. Deliberately excluded from the rewrite: wizard UX/colors, common.sh, installed_deps.
- Releases: bump `VERSION` on dev, fast-forward main, tag `vX.Y.Z`, `gh release create` with notes in the established style (What's new / Fixes / Updating via `sudo /opt/lgpowercontrol/update.sh`).

## TV behavior (LG WebOS, verified on OLED42C35LA)

### Power states (`get_power_state`)

| TV situation | Response |
|---|---|
| On, screen on | `{'state': 'Active'}` |
| After `turn_screen_off` | `{'state': 'Screen Off'}` |
| Always Ready standby (after `power_off`) | `{'state': 'Active Standby'}` |
| Deep standby (off a long time / cold boot) | `{'state': 'Suspend'}` |
| Waking (WoL has bitten) | state + `'processing': 'Screen On'` |

- A `processing` field means mid-transition; a plain standby state means the WoL was lost — resend it. Unknown sleep states are safe by design: the wake loop's catch-all branch resends WoL.
- While `processing` is present the `state` value is unreliable as a which-standby indicator: waking 5 min after `power_off` (= Always Ready) has been observed reporting `Suspend (Screen On)` yet completing in ~3 s (2026-07-16). Only plain states are trustworthy.
- Wake takes ~4 s from Always Ready standby, ~5 s from deep standby (once WoL bites), ~10 s without Always Ready.
- **`turn_screen_on` error -102 is ambiguous**: it fires both from standby ("not waking") and when the screen is already on ("current sub state must be Screen Off"). It may only be treated as success after `get_power_state` has proven the TV awake. This ambiguity caused a false-success bug on WiFi resume (fixed in v2.8.1 by polling `get_power_state`).
- **Keep-alive is impossible**: deep standby is driven by an internal timer (~13 min after screen-off) that ignores incoming WebSocket connections. Don't re-propose polling keep-alives. The fix that shipped: the monitor escalates screen-off to a full `power_off` after 10 min, landing the TV in Always Ready standby (fast wake). Always Ready only engages on `power_off`, not from screen-off.
- This made mode settings pointless: `BOOT_SHUTDOWN_MODE`/`MONITOR_MODE` were removed; commands are hardcoded ON (WoL + verified `turn_screen_on`), OFF (`power_off`), SCREEN_OFF (`turn_screen_off`). Old confs defining the keys are harmless.

### Wake-on-LAN

- **WoL must be broadcast on the TV's own subnet** — unicast to the TV's IP needs an ARP reply a sleeping TV doesn't always give; the packet is silently dropped and the WoL tool exits 0 anyway.
- On WiFi, WoL packets sent right after resume get lost while the link settles — even though `nm-online` passes and unicast works. The wake loop must keep resending until the TV's state proves the packet bit.
- WoL is built and sent in-house: a stdlib-python script (`scripts/lgpc-wol.py`, system python3) sends the magic packet on UDP port 9, both broadcast *and* routed unicast to `$LGTV_IP` — the external `wol` tool and the `WOL_L3` conf option were dropped 2026-07-19 since each copy is a harmless no-op in the other's setup (WebOS networked standby answers ARP, so routed unicast covers cross-VLAN, issue #12). Old confs defining `WOL_L3` are harmless.

## Suspend/resume architecture (hard-won, don't relitigate)

- **NM kills the network 17 ms after logind's PrepareForSleep** and does not wait for foreign delay inhibitors. Proven dead ends: sleep.target units (network already down), own logind delay inhibitor (delays the kernel, not NM), PowerDevil's aboutToSuspend (ms of margin), NM sleep config options (none exist). Guides/projects using sleep units (e.g. LG_Buddy and the Reddit HTPC guide built on it) work by racing NM's teardown — `NetworkManager-sleep.service` doesn't even exist as a unit.
- **What works: NM dispatcher `pre-down.d`** — runs blocking at sleep with network still up (plain-NIC setups), gated on logind's `PreparingForSleep` property. Shipped as `scripts/90-lgpowercontrol` (v2.3). Almost-true caveat (seen on p600s 2026-07-27): pre-down blocks NM's device-state transition but not its parallel DHCP-cancel/IP-flush, which can finish before the script spawns (~10 ms vs ~70 ms after PrepareForSleep) → power_off fails with Errno 101, link still up. Only exercised when the TV is still on at suspend (typically manual suspend) — auto-suspend usually takes the "TV already off, skipping" path (19 of 23 suspends since the flag shipped); when exercised it has failed 1 of 4 times, so it's a real race, not a fluke. Opt-in fix documented in README: enable NIC WoL so NM skips the device and the race-free sleep hook takes over. Default-enabling NIC WoL was considered and rejected. Since 2026-07-28, `scripts/lgtvpc-wol.py --enable`/`--disable` (installed to `/opt/lgtvpc/`) wraps the `nmcli` dance (auto-detects the wired device, sets `802-3-ethernet.wake-on-lan`, reactivates the connection so NM actually applies it) so users don't need to run `nmcli` by hand.
- **Wake side**: same dispatcher script via NM's `up` event (symlinked from pre-down.d/). `up` + the `/run/lgpowercontrol-sleep` flag = resume; flag absent = boot/replug → no-op. ON is detached via systemd-run so it doesn't block NM's dispatcher queue.
- **NIC-WoL exception (issue #12, 2026-07-17)**: when the PC's NIC has Wake-on-LAN enabled, NM *skips the device entirely* at sleep (`sleep: device eno1 has wake-on-lan, skipping`) — no pre-down, network stays up through suspend. Fix: systemd sleep hook `scripts/lgpowercontrol-sleep` (installed as `/usr/lib/systemd/system-sleep/lgpowercontrol`) that acts only when the dispatcher's sleep flag is absent. On these setups a sleep hook is NOT a race — there is no teardown. Hook runs after NM's dispatcher queue (logind inhibitor), so flag-gating is reliable. The hook must own resume too (`post` → detached ON, gated on its own `/run/lgpowercontrol-hook-sleep` flag): no `up` event fires (device never went down) and the monitor can freeze before observing DPMS-off, leaving no off→on transition at resume (verified on p600s 2026-07-17).
- **NIC-WoL became an active install-time choice (2026-07-29)**: `install.py` now asks (default Y, pros/cons listed) whether to enable Wake-on-LAN on the wired adapter, replacing the old opt-in README instruction — the earlier "default-enable NIC WoL" rejection was superseded by this explicit-choice middle ground. Only asked when exactly one wired NM device with an active connection exists (Wi-Fi-only gets a note, multi-NIC setups are skipped); already-`magic` connections skip the question, which keeps re-runs via `update.py` prompt-free. Runs after `authorize.py` since enabling reactivates the connection (brief network drop). A yes touches `NIC_WOL_MARKER` (`/opt/lgtvpc/.nic-wol-enabled`, preserved across reinstalls like the pairing DB); `uninstall.py` reverts the setting when the marker exists — but not on the `--quiet` reinstall path.
- **Immutable-OS fallback (2026-07-29)**: on read-only `/usr` (Bazzite etc.) the sleep hook can't be installed; `install.py` instead installs `scripts/sleep-listener.py` + `systemd/lgtvpc-sleep.service` (unit in `/etc/systemd/system`, which stays writable). The listener holds a logind sleep **delay inhibitor** (via a `systemd-inhibit ... sleep infinity` child; killing it releases the lock — avoids stdlib-impossible fd-passing) and watches `PrepareForSleep` via `busctl --system monitor` (verified: flushes per message even piped). The inhibitor is safe *here* despite the "delays the kernel, not NM" dead end: in the NIC-WoL case NM skips the device, so there's no teardown to race. Unlike the hook, the listener gets `PrepareForSleep` simultaneously with NM, so the dispatcher's `SLEEP_FLAG` may not be set yet — it waits up to 1 s for the flag (pre-down touches it within tens of ms) before acting; the wait plus `--retries 1` OFF must fit `InhibitDelayMaxSec` (5 s default). The listener must NOT replace the hook on normal distros: the hook's run-after-all-inhibitors ordering is a guarantee, the listener's grace wait is a heuristic.
- **`nmcli connection modify ... wake-on-lan magic` alone does not enable the NIC-WoL fix** — it only updates the saved connection profile; NetworkManager doesn't push the setting down to the card until the connection is reactivated (`nmcli connection down/up <name>`, or a reboot). Verified on p600s (2026-07-28): running `modify` then suspending ~8s later still hit the pre-down race (`activated -> deactivating`, `power_off: Errno 101`) because the live device still had WoL off; reactivating the connection first made NM skip the device at sleep (`activated -> unmanaged`) and the race-free sleep hook took over successfully. README now documents the reactivation step.
- **Bridge exception**: NM detaches bridge ports 1 ms into deactivation, before the pre-down window — no TV-off at suspend on bridged setups (documented in README). Wake still works via the DRM off→on watcher. The sleep hook fires there too but the network is already gone; it passes `connect_retries=1` so the failed attempt stays fast.
- networkd-only systems: TV-off at suspend deliberately unsupported (user decision).
- On resume, ON fires from both the DPMS watcher and the dispatcher; a flock in `turn_tv_on` deduplicates.
- **Wake-loop poll interval stays at 1 s.** The attempt count is really a time budget for network-up + TV wake: while the network is still down after resume, `get_power_state` fails instantly and each attempt costs only the sleep. 0.5 s was tried (2026-07-17) and halved the budget — a real wake barely fit (attempt 10/10, back when the budget was 10 attempts). Don't shorten the interval again. The attempt count itself has already been raised once: 10 attempts (~10 s) was seen to barely fit again (attempt 9/10, p600s 2026-07-28, sleep-hook path — ~5 s of that spent on the network alone after resume) and was bumped to 15 (~15 s).

## Notify feature (Plasma TV-off warning)

- Plasma's idle dim is invisible on D-Bus: it doesn't touch `org.kde.ScreenBrightness`, activates no KWin effect, emits no session-bus signals. The only observable is each output's "dimming" property via `kscreen-doctor -o` text output (**not** in the `-j` JSON).
- `lgpowercontrol-notify.sh` polls `kscreen-doctor -o` every 5 s for "dimming to" < 100%, arms a timer for (off − dim − OFF_WARNING_SECONDS), cancels on return to 100%. Notification via `busctl call org.freedesktop.Notifications` (no libnotify).
- Two earlier designs shipped and never fired (ScreenBrightness listener, kscreen-effect watcher). PowerDevil display names (display1/display3…) change across sessions — don't hardcode.
- Battery/LowBattery fallback timeouts in notify are unverified estimates.

## Python rewrite (2026-07-28)

- The project was rewritten from bash to Python end-to-end (attempt 2 — straight port, no scope creep). Renamed throughout: `lgpowercontrol` → `lgtvpc` (CLI, dispatcher `90-lgtvpc`, systemd units, `/opt/lgtvpc`), shared helper module `lgpc_common.py` → `lgtvpc_common.py`, its `OPT_DIR` constant → `INSTALL_DIR`. `uninstall.py` still tears down a pre-rename `lgpowercontrol` install (and even older `lgpc-*` artefacts) as a one-time migration — don't remove that path casually.
- `scripts/lgpc-wol.py` no longer exists as a separate script: WoL sending is inlined as `send_wol()` in `scripts/lgtvpc` (the CLI script), sharing `CONF`/logging with the rest of the tool.
- `conf_int()` in `lgtvpc_common.py` originally only accepted positive integers, silently treating a conf value of `"0"` the same as missing/invalid and falling back to the default — this broke the documented `OFF_WARNING_SECONDS="0"` and `UPDATE_CHECK_DAYS="0"` (both meant to disable the feature) and made the `<= 0: return` guards in `notify.py`/`update-check.py` dead code. Fixed (2026-07-28) with a `conf_int(..., allow_zero=True)` param, used by those two call sites; `NOTIFY_POLL_SECONDS` keeps the positive-only default since 0 would busy-loop.
- `notify.py`'s `TurnOffDisplayWhenIdle` check was originally read once at startup and, if disabled, exited `main()` permanently — enabling "Turn off screen" later required a service restart to resume warnings (unlike the equivalent `DimDisplayWhenIdle` check, which self-corrects since dim events simply stop firing). Fixed by moving the check into `Notifier.compute_timings()` (re-read on every dim, like the other PowerDevil settings) and gating `arm_timer()` on it instead.
- `install.py` uses relative paths (`lgtvpc.conf`, `./uninstall.py`, `scripts/...`) throughout `main()`, so it must be run with the repo root as cwd; fixed by `os.chdir(Path(__file__).resolve().parent)` right after `require_root()`.

Machine-specific notes (test machine details) live in `CLAUDE.local.md`, which is gitignored.
