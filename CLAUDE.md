# CLAUDE.md

Project notes for lgpowercontrol: accumulated findings and working rules. Verified against an LG OLED42C35LA on CachyOS, KDE Plasma/Wayland, wired and WiFi. Machine-specific details live in `CLAUDE.local.md`, which is gitignored.

## 1. Working rules

Propose first, act after approval. "Can X be improved?" asks for an assessment, not implementation. Never edit code or commit without an explicit go-ahead; committing needs its own approval even after a change was already approved.

No new dependencies without permission. The project stays deliberately minimal. Never add a third-party program or package without asking first, and offer a zero-dependency alternative when one exists. swayidle and libnotify were rejected on these grounds; kscreen-doctor is fine since it ships with Plasma.

Keep it simple over covering edge cases. Offline install and installer-side Wake-on-LAN were considered and rejected, since pairing needs the TV on anyway. Don't re-propose either without a real pain point.

## 2. Repository and releases

`main` is what users clone; `dev` is the working branch. A release bumps `VERSION` on dev, fast-forwards main, and tags it. Older project history lives in the `legacy-main` tag. The multi-distro rewrite deliberately dropped the old wizard UI, `common.sh`, and dependency tracking.

## 3. TV power states

`get_power_state` reports `Active` when on with the screen on, `Screen Off` after `turn_screen_off`, `Active Standby` after `power_off` on TVs with Always Ready, and `Suspend` for deep standby. While waking, the response also carries a `processing` field.

`processing` means mid-transition. A plain standby state means the Wake-on-LAN packet was lost and needs resending; unknown states get the same safe treatment. While `processing` is present, the state value itself can't be trusted to say which standby the TV woke from, only a plain state can. Once a packet lands, waking takes about 4 seconds from Always Ready, about 5 from deep standby, about 10 without Always Ready.

`turn_screen_on`'s `-102` error is ambiguous: it fires both when still asleep and when the screen is already on. Treat it as success only once `get_power_state` has separately proven the TV awake.

Keep-alive polling is impossible: deep standby is driven by an internal timer, around 13 minutes after screen-off, that ignores incoming connections. Don't re-propose it. Instead, the monitor escalates a screen-off to a full `power_off` after 10 minutes, landing the TV in Always Ready standby for a fast wake; Always Ready only engages on `power_off`, never on screen-off alone. This is also why there are no mode settings: commands are hardcoded to ON, OFF, and SCREEN_OFF. Older config files defining the old mode keys are harmless.

## 4. Wake-on-LAN

The magic packet must be broadcast on the TV's own subnet: unicast needs an ARP reply a sleeping TV won't reliably give, so it's silently dropped even though sending it reports success. Over WiFi, packets sent right after resume can be lost while the link settles even after the network reports itself ready, so the wake loop keeps resending until the TV's own state proves a packet arrived.

The packet is built and sent in-house, on UDP port 9, both as a broadcast and as a routed unicast to the TV's IP. Each copy is a harmless no-op in the other's setup; the routed copy covers a TV on a different subnet or VLAN, since WebOS answers ARP in standby.

## 5. Suspend and resume

NetworkManager tears its connections down about 17 ms after logind's PrepareForSleep signal, and won't wait for a foreign delay inhibitor. Proven dead ends, don't retry: sleep-target units (network already gone by the time they run), a delay inhibitor held by this project (only delays the kernel, not NetworkManager), PowerDevil's aboutToSuspend hook (milliseconds of margin), and NetworkManager config options (no such setting exists).

What works: a NetworkManager dispatcher script in `pre-down.d`, run synchronously at sleep while the network is still up, gated on logind's PreparingForSleep property. Known caveat: pre-down blocks the device-state transition but not NetworkManager's parallel DHCP-cancel and IP-flush, which can finish first and make the following `power_off` fail with the link already down. This only happens when the TV is still on at suspend (mostly manual suspend), and is a real but infrequent race. Fix: enable Wake-on-LAN on the computer's own network card, which makes NetworkManager skip deactivating that device at sleep entirely, sidestepping the race.

On resume, the same dispatcher script reacts to NetworkManager's `up` event: sleep flag set means resume, so the TV turns back on, detached so a long retry sequence doesn't block other dispatcher scripts; flag absent means boot or cable replug, so nothing happens.

When the computer's NIC has its own Wake-on-LAN enabled, NetworkManager skips deactivating it at sleep entirely, so the dispatcher never runs and the network stays up through suspend. A systemd-sleep hook covers this: it acts only when the dispatcher's sleep flag is absent, which is safe here since there's no teardown to race. Since no `up` event exists on this path, and the display watcher can be too slow to notice, this hook also turns the TV back on at resume, tracked with its own flag.

Enabling NIC Wake-on-LAN is now an explicit install-time choice, offered (default yes) whenever exactly one wired device with an active connection exists; Wi-Fi-only gets a note, multi-NIC is skipped. Accepting it is recorded in a marker file, preserved across reinstalls, so uninstalling can revert it later; not consulted on a quiet reinstall.

Editing the saved NetworkManager connection profile alone does not enable this fix: NetworkManager only pushes the Wake-on-LAN setting to the card once the connection is reactivated, not merely edited.

On a read-only root filesystem, the sleep hook can't be installed, so a listener service goes in a writable location instead. It holds a logind sleep delay inhibitor via a detached child process, releasing it by killing that child, and watches for the sleep signal directly. Holding an inhibitor is safe here specifically because NetworkManager already skips the device at sleep in this setup, so there's no teardown left to race. Since the listener gets the sleep signal at the same time as NetworkManager rather than after it, it waits briefly for the dispatcher's sleep flag before acting, and that wait must fit inside the delay inhibitor's timeout. This listener must never replace the sleep hook on an ordinary distribution: the hook's after-every-inhibitor ordering is a guarantee, the listener's brief wait is only a heuristic.

A bridged network is a known exception: NetworkManager detaches a bridge port almost immediately at suspend, before the pre-down window opens, so the TV can't be turned off automatically at suspend there. Resume still works normally. A system running only systemd-networkd, with no dispatcher available, doesn't support TV-off at suspend at all, by design.

On resume, the TV can be told to turn on from both the display watcher and the dispatcher at once; a file lock inside the wake command deduplicates that.

The display watcher's suspend gate checks only whether the system is preparing to sleep, not a separate dispatcher-only flag. Checking both together used to let the watcher's own screen-off slip in just before the sleep path's turn-off, on setups using the hook or listener. Checking only the system's sleep state is safe because logind reports it before the display itself turns off in response to the same signal.

The wake loop's poll interval must stay at one second: the real limit is the total time budget for the network to come back and the TV to wake, not the interval. A shorter interval was tried once and effectively halved that budget, causing a real wake to barely fit. The attempt count has already been raised once, from ten to fifteen, for the same reason.

A TV that appears to turn off again right after waking is not a bug here. KDE Plasma's default "lock after waking from sleep" setting blanks the display itself a few seconds after resume; the display watcher correctly follows that, and everything corrects itself on the next input.

## 6. The idle warning notification

Plasma's own idle dimming is invisible on D-Bus: no brightness-interface signal, no compositor effect, no session-bus event. The only observable is a per-output "dimming" property in one command's plain-text output, not its structured JSON output.

The notification service polls that command for a dimming percentage under 100, arms a timer for the configured warning window before the screen would turn off, and cancels it if the display returns to full brightness first. The notification is sent with a direct D-Bus call, no notification library involved.

Two earlier designs never fired: a brightness-interface listener and a compositor-effect watcher. Plasma's per-output display names change across sessions and must never be hardcoded. The battery and low-battery fallback timeouts this feature reads are unverified estimates.

## 7. The Python rewrite

The project was rewritten from bash to Python end to end, as a straight port with no added scope. Everything was renamed to match: the CLI, the dispatcher script, the systemd units, the install directory, and the shared helper module.

The uninstaller still tears down installs from before this rename, and from even older naming schemes, as a one-time migration; don't remove that path casually. It also tears down the `lgtvpc`-named install from §8 below, on the same principle. All of it lives in `legacy_migration.py`, kept apart from the installer and uninstaller so the two of them describe only the current install, and so the migration code can be deleted as one unit once old installs have died out.

A config-parsing helper originally treated a configured value of zero the same as a missing one, silently falling back to its default, which broke the documented way to disable two features by setting them to zero. It gained an explicit opt-in for allowing zero, used only by those two settings.

The idle warning service originally checked once, at startup, whether the underlying Plasma setting was enabled, and exited permanently if not, so re-enabling it later needed a manual restart. It now re-reads the setting every time the display dims and gates on it at the point of actually arming the warning.

Migrating from an older bash-based install is seamless: a transitional install script carries the old configuration values into the new format and hands off to the Python installer, which itself falls back to an older pairing-database location when the new one has none, so a working pairing survives without re-pairing. The transitional shim can be removed once old installs have died out. The installer must be run from the repository root, since it uses paths relative to it; it changes into its own directory to guarantee that.

## 8. The `lgtvpc` naming, tried and reverted

The Python rewrite in §7 also renamed the project's internal identifiers from `lgpowercontrol` to `lgtvpc` (install directory, package name, commands, systemd units, the lot). That shipped as v3.0. It was reverted back to `lgpowercontrol` shortly after, at Basse's explicit call - he didn't like the shorter name. Don't re-propose `lgtvpc` or any other shortening of the project name; this was tried and rejected, not just never considered.

Because v3.0 had already shipped, the revert needed a real migration path, not just a rename. It lives in `legacy_migration.py` alongside the older migrations from §7: a `lgtvpc.conf` next to the installer or at `/opt/lgtvpc/lgtvpc.conf` has its settings carried over when the local conf's `LGTV_IP` is empty, and `/opt/lgtvpc`'s pairing key and NIC-WoL marker are used as fallbacks. `uninstall.py`'s `remove_installation(prefix, opt_dir)` helper (see §7) stays in the uninstaller and is passed into the migration module, which calls it for a leftover `/opt/lgtvpc` v3.0 install - the reverse of what it did during v3.0's own lifetime.

The same pass fixed a pre-existing, unrelated bug found while doing this: the boot/shutdown systemd units set `LGPC_SOURCE`, but the code only ever read `LGTVPC_SRC`, so boot/shutdown invocations silently logged as generic `cli` instead of `boot`/`shutdown`. Settled on `LGPC_SOURCE` project-wide since it already matched the reverted naming.
