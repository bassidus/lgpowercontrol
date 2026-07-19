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
- WoL is built and sent in-house: a stdlib-python snippet (run with the venv's interpreter) sends the magic packet on UDP port 9, both broadcast *and* routed unicast to `$LGTV_IP` — the external `wol` tool and the `WOL_L3` conf option were dropped 2026-07-19 since each copy is a harmless no-op in the other's setup (WebOS networked standby answers ARP, so routed unicast covers cross-VLAN, issue #12). Old confs defining `WOL_L3` are harmless.

## Suspend/resume architecture (hard-won, don't relitigate)

- **NM kills the network 17 ms after logind's PrepareForSleep** and does not wait for foreign delay inhibitors. Proven dead ends: sleep.target units (network already down), own logind delay inhibitor (delays the kernel, not NM), PowerDevil's aboutToSuspend (ms of margin), NM sleep config options (none exist). Guides/projects using sleep units (e.g. LG_Buddy and the Reddit HTPC guide built on it) work by racing NM's teardown — `NetworkManager-sleep.service` doesn't even exist as a unit.
- **What works: NM dispatcher `pre-down.d`** — runs blocking at sleep with network still up (plain-NIC setups), gated on logind's `PreparingForSleep` property. Shipped as `scripts/90-lgpowercontrol` (v2.3).
- **Wake side**: same dispatcher script via NM's `up` event (symlinked from pre-down.d/). `up` + the `/run/lgpowercontrol-sleep` flag = resume; flag absent = boot/replug → no-op. ON is detached via systemd-run so it doesn't block NM's dispatcher queue.
- **NIC-WoL exception (issue #12, 2026-07-17)**: when the PC's NIC has Wake-on-LAN enabled, NM *skips the device entirely* at sleep (`sleep: device eno1 has wake-on-lan, skipping`) — no pre-down, network stays up through suspend. Fix: systemd sleep hook `scripts/lgpowercontrol-sleep` (installed as `/usr/lib/systemd/system-sleep/lgpowercontrol`) that acts only when the dispatcher's sleep flag is absent. On these setups a sleep hook is NOT a race — there is no teardown. Hook runs after NM's dispatcher queue (logind inhibitor), so flag-gating is reliable. The hook must own resume too (`post` → detached ON, gated on its own `/run/lgpowercontrol-hook-sleep` flag): no `up` event fires (device never went down) and the monitor can freeze before observing DPMS-off, leaving no off→on transition at resume (verified on p600s 2026-07-17).
- **Bridge exception**: NM detaches bridge ports 1 ms into deactivation, before the pre-down window — no TV-off at suspend on bridged setups (documented in README). Wake still works via the DRM off→on watcher. The sleep hook fires there too but the network is already gone; it passes `connect_retries=1` so the failed attempt stays fast.
- networkd-only systems: TV-off at suspend deliberately unsupported (user decision).
- On resume, ON fires from both the DPMS watcher and the dispatcher; a flock in `turn_tv_on` deduplicates.
- **Wake-loop poll interval stays at 1 s.** The 10 attempts are a ~10 s time budget for network-up + TV wake: while the network is still down after resume, `get_power_state` fails instantly and each attempt costs only the sleep. 0.5 s was tried (2026-07-17) and halved the budget — a real wake barely fit (attempt 10/10). Don't shorten it again.

## Notify feature (Plasma TV-off warning)

- Plasma's idle dim is invisible on D-Bus: it doesn't touch `org.kde.ScreenBrightness`, activates no KWin effect, emits no session-bus signals. The only observable is each output's "dimming" property via `kscreen-doctor -o` text output (**not** in the `-j` JSON).
- `lgpowercontrol-notify.sh` polls `kscreen-doctor -o` every 5 s for "dimming to" < 100%, arms a timer for (off − dim − OFF_WARNING_SECONDS), cancels on return to 100%. Notification via `busctl call org.freedesktop.Notifications` (no libnotify).
- Two earlier designs shipped and never fired (ScreenBrightness listener, kscreen-effect watcher). PowerDevil display names (display1/display3…) change across sessions — don't hardcode.
- Battery/LowBattery fallback timeouts in notify are unverified estimates.

Machine-specific notes (test machine details) live in `CLAUDE.local.md`, which is gitignored.
