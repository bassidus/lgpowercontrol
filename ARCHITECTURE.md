# LGPowerControl: Architecture

Internal documentation for developers. It explains how the pieces fit together and why they are built the way they are. For user-facing instructions, see [README.md](README.md).

## 1. Overview

The project mirrors the computer's display power state onto an LG WebOS TV used as a monitor. At boot, the TV turns on. When the display goes idle, the TV's screen turns off, and after ten minutes of that it turns fully off instead. When activity returns, the TV turns back on. At suspend, the TV turns fully off; at resume, it turns back on. At shutdown, unless it is a reboot, the TV turns off. Shortly before an idle screen-off, a desktop warning notification appears on Plasma.

Everything is Python and systemd. The TV is controlled with [bscpylgtv](https://github.com/chros73/bscpylgtv), installed in its own virtual environment, which speaks the WebOS WebSocket API directly. There are no other dependencies: the installer, the self-updater, and every daemon use only the standard library plus bscpylgtv itself.

The git repository is not the runtime. `install.py` builds a private virtual environment under `/opt/lgpowercontrol/bscpylgtv` and `pip install`s the whole `lgpowercontrol` package into it, alongside a couple of plain files copied into `/opt/lgpowercontrol` itself; editing a module in the repository has no effect until it is reinstalled. Reinstalling is idempotent and preserves both the configuration file and the TV's pairing key.

The Python source lives under `src/lgpowercontrol/` and installs as an ordinary pip package (`pyproject.toml` at the repo root), generating one console-script executable per command into `/opt/lgpowercontrol/bscpylgtv/bin/`: the core command `lgpowercontrol`, the DPMS watcher daemon `lgpowercontrol-monitor`, the Plasma warning daemon `lgpowercontrol-notify`, the pairing helper `lgpowercontrol-authorize`, the self-updater `lgpowercontrol-update` and its periodic companion `lgpowercontrol-update-check`, the Wake-on-LAN toggle helper `lgpowercontrol-wol`, the immutable-OS fallback daemon `lgpowercontrol-sleep-listener`, and two that are never invoked by name directly but exist so the installer has a stable executable to symlink into place: the systemd-sleep hook `lgpowercontrol-sleep-hook` and the NetworkManager dispatcher `lgpowercontrol-nm-dispatcher`. `/opt/lgpowercontrol` itself additionally holds the configuration file `lgpowercontrol.conf`, a `VERSION` file, and the TV's pairing key.

The four commands meant to be typed by hand - `lgpowercontrol`, `lgpowercontrol-wol`, `lgpowercontrol-authorize`, `lgpowercontrol-update` - are also symlinked into `/usr/local/bin`, so they work without the full venv path. Everything else only ever runs by its full path, invoked by systemd, NetworkManager, or another part of this project.

Three systemd units run at the system level: a oneshot that turns the TV on at boot, a oneshot that turns it off at shutdown (but not reboot), and the DPMS watcher service. Two more run per user session: the warning daemon, and a timer that periodically checks for updates. A NetworkManager dispatcher script handles both the suspend and the resume transition, and a systemd-sleep script acts as its fallback on setups where the dispatcher script cannot run.

## 2. The shared module

`lgpowercontrol.common` holds everything that would otherwise be duplicated across every module: path constants, the configuration file parser, a small logger that writes tagged lines to the system journal and can be turned off entirely, a helper that checks whether the system is currently preparing to sleep, the desktop notification helpers, a helper for launching a command detached from its parent, and a helper that requires the calling script to be run as root.

The two hooks that must live at fixed paths outside `/opt/lgpowercontrol` (the NetworkManager dispatcher and the systemd-sleep hook, see §5) are just symlinks the installer creates, pointing at the real pip-generated executables in `/opt/lgpowercontrol/bscpylgtv/bin/`. Since they're reached through the venv's own executable, not run in place, they need no special import handling - a plain `from lgpowercontrol.common import ...` resolves like it does everywhere else in the package.

`install.py` and `uninstall.py` are the only pieces of this project that stay outside the package and run on the system's Python, since they exist to build (or tear down) the venv in the first place. Because `lgpowercontrol.common` only imports the standard library, they can import it directly from the repository's `src/` tree before any install exists.

`legacy_migration.py` sits beside them, outside the package for the same reason. It holds every migration path for installs made under an older naming scheme: carrying their configuration, pairing key and Wake-on-LAN choice forward, and tearing their files down. Nothing in it serves a current install, so it is meant to be deleted whole, along with its handful of call sites, once those installs have died out.

## 3. The core command

`lgpowercontrol` is the single entry point for every TV command. Every service, the dispatcher script, and a user at the command line all go through it. Like every other command in this project except the installer and uninstaller, it runs in the project's own virtual environment, where `bscpylgtv` is installed as a normal dependency of the `lgpowercontrol` package.

```
lgpowercontrol [--retries N] ON | OFF | SCREEN_OFF | STATUS
```

The `--retries` option sets how many times a command tries to connect to the TV, and defaults to three. The suspend fallback hook passes one, so a dead network cannot hold up suspend. The wake loop inside `ON` always uses one for its own internal checks regardless of this flag, since the loop already retries once a second on its own.

`ON` broadcasts a Wake-on-LAN packet, then polls the TV's power state once a second for up to fifteen seconds, resending the packet while the TV still looks asleep. Once the TV reports itself awake, it turns the screen on, and if an HDMI input is configured, switches to it, retrying for up to fifteen seconds more in case the TV is still booting.

`OFF` turns the TV fully off and marks that it did so, so a following suspend does not try to turn it off again.

`SCREEN_OFF` turns just the screen off, leaving the TV's software running.

`STATUS` prints the TV's current power state and exits with a code that tells the caller why a command failed rather than just that it did. A zero means success, one means the TV refused the command or something in this program went wrong internally, two means the TV could not be reached at all, and three means the TV is not paired or refused pairing. The pairing helper relies on this distinction directly: it only deletes a saved pairing key on exit code three, never on a merely unreachable TV.

Every exception the TV control library can raise is classified in exactly one place, so a bug in this program can never be mistaken for a network problem: a pairing failure becomes exit code three, anything shaped like a connection problem becomes exit code two, and everything else becomes exit code one and gets logged as an internal error. The one exception is the TV's own screen-on-related error, described below, which needs special handling because it is inherently ambiguous.

Every invocation carries an identifier for whoever triggered it, boot, shutdown, the DPMS watcher, the network dispatcher, resume, the suspend fallback hook, or the command line by default, and every journal line it writes carries that same identifier.

Wake-on-LAN packets are built directly with the standard networking library, with no external tool involved. Each wake sends the packet twice, once as a broadcast and once routed directly to the TV's address, so that setups where the TV sits on a different subnet are covered as well as ordinary ones.

The lock file used to prevent two `ON` calls from running at once is created so that only its owner can read or write it, since a lock file anyone could hold open would let any local user block every future TV wake-up.

### Why waking the TV is a loop, not a single command

A Wake-on-LAN packet can simply be lost. On the TV's own network segment it has to be sent as a broadcast, since a directed packet needs the TV to answer an address lookup it will not reliably answer while asleep, and the send itself still reports success even when the packet never arrived. Over Wi-Fi, packets sent right after the computer resumes can be lost while the wireless link finishes settling, even once the network otherwise reports itself ready. The loop keeps resending the packet, once a second, until the TV's own reported state proves that one has actually arrived.

The TV's screen-on command also returns an error that means two different things: that the TV is still asleep, or that the screen was already on. The only way to tell which is true is to check the TV's power state directly, so that error only counts as success once the power state has already confirmed the TV is awake.

Each second, the loop reads the TV's power state and acts on it. An awake state gets the screen-on command. A state that is mid-transition is left alone. A standby state, or a failure to connect at all, gets another Wake-on-LAN packet. Any state this project does not recognize also falls into that last, safe branch. Once a packet does land, waking typically takes a few seconds from a fast standby mode, a little longer from a deeper one, and longest on TVs that lack a fast standby mode at all. While the state is mid-transition, its exact value cannot be trusted to say which kind of standby the TV was woken from; only a plain, non-transitional state can be relied on for that.

### Avoiding duplicate wake-ups

At resume, the TV can be told to turn on twice at once, once by the network dispatcher and once by the display watcher noticing the screen come back. `ON` takes a non-blocking file lock before doing anything; whichever call loses that race exits immediately as if it had succeeded. Turning the TV off does not need this, since only two simultaneous wake-ups can actually collide.

## 4. The display watcher

`lgpowercontrol-monitor` is a root daemon that checks the display's power state once a second, read directly from the kernel rather than through the desktop session, so it works the same regardless of which compositor or session is running. It considers the display on if any connected output is on, off if every connected output is off, and otherwise leaves the current state alone.

When the display turns on, it runs the wake command. When it turns off, it runs the screen-off command, unless a suspend is already in progress, in which case the suspend path is already handling the TV and the network may already be gone.

After ten continuous minutes of the display staying off, the watcher escalates once and turns the TV fully off instead. This exists because the TV drops into a deep, slow-to-wake standby on its own after a fixed amount of time with the screen merely off, and a full turn-off instead lets it settle into a much faster standby where the TV supports one. On TVs without a fast standby mode, this escalation makes no difference either way. It only fires once per period of the display being off, and is skipped entirely while a suspend is in progress.

Because this behavior is fixed and always the right one for the situation, none of it is configurable.

## 5. Suspend and resume

A NetworkManager dispatcher script is what makes both directions of this work. NetworkManager tears its own network connections down within milliseconds of the system beginning to suspend, so anything that still needs the network at suspend time has to run, and finish, before that happens. NetworkManager dispatcher scripts that hook the pre-down stage run exactly that way: synchronously, with the connection still up, and NetworkManager waits for them before continuing.

On the way down, the script only acts once it has confirmed the system is genuinely preparing to sleep, so an ordinary disconnect is ignored. It runs once for each network interface, and only the first of those runs does anything; it marks that a suspend is underway. If the TV has already been turned off by the display watcher's escalation, it skips turning the TV off again, since doing so against an already-off TV would just wait out a connection timeout and delay suspend for no reason. Otherwise, it turns the TV off itself, synchronously, before the network actually drops.

On the way back up, the same script checks whether the earlier mark is still there. If it is, this is a resume, and the mark is cleared; if not, this is an ordinary boot or a cable being replugged, and nothing happens. On a genuine resume it turns the TV back on, but detached from the dispatcher process itself, since dispatcher scripts run one after another and the wake sequence can take up to a minute; running it inline would stall every other dispatcher script behind it.

Some network cards have their own Wake-on-LAN feature, and when the computer's card has that feature turned on, NetworkManager skips deactivating it entirely at suspend, which means the dispatcher script never runs at all. A separate systemd-sleep script exists to cover exactly this case. It runs after the dispatcher has already had its chance, so if a suspend was already marked as handled, it does nothing. If not, it turns the TV off itself, using only a single connection attempt so that setups where the network really has gone away anyway, such as a bridged network, fail fast rather than stalling. Since no dispatcher resume event exists on this kind of setup either, and the display watcher can be too slow to notice, this same script is also responsible for turning the TV back on at resume, tracked with its own separate marker.

On a bridged network setup, the network is torn down for suspend before either of these mechanisms gets a chance to run, so the TV cannot be turned off automatically at suspend at all; resume still works normally either way. On a system using only systemd-networkd, with no NetworkManager dispatcher available, turning the TV off at suspend is not supported, though boot, shutdown, and ordinary idle screen-off are unaffected.

## 6. Flag files

A handful of files under `/run` track state across these transitions, and all of them disappear on their own at reboot.

One marks that a suspend is currently being handled by the dispatcher script; it is set going down and cleared coming back up, and while it is set the display watcher will not react to the screen going dark, since that is an expected part of suspending rather than something to act on. If this marker is ever left behind, for example because the network never came back after a resume, the display watcher notices by checking the system's sleep state directly and clears it itself.

Another marks that the TV has already been turned off, set whenever the TV is turned off and cleared whenever it is turned back on; it exists purely so the suspend path can skip a redundant turn-off.

A third exists only for the systemd-sleep fallback script, tracking whether that particular suspend was its responsibility rather than the dispatcher's, since that is also what tells it to turn the TV back on at resume.

The last is a simple lock, held only for the length of a single wake-up call, used to keep two simultaneous wake-ups from racing each other.

## 7. The warning notification

`lgpowercontrol-notify` is a Plasma-only convenience: a desktop notification a configurable number of seconds before the screen, and therefore the TV, is about to turn off from being idle. It runs as a per-session service, since it needs access to the session's desktop bus. It exits immediately if the warning is turned off in configuration, or if the Plasma tools it depends on are not present at all.

Plasma's own automatic dimming does not announce itself anywhere convenient; the only place it shows up at all is as a line of plain text output from one particular command, not in that same command's structured output. So the service polls that command periodically, watching for that line to appear.

Once dimming starts, it reads how long Plasma is configured to wait before actually turning the screen off, for whichever power profile is active, and sets a timer for that time minus the configured warning window. When the timer fires, it double-checks that dimming is still happening before actually notifying, so that a suspend which starts before the screen would have turned off does not produce a stale warning afterward.

If the user comes back before the screen turns off, the pending timer is cancelled and any notification already on screen is withdrawn. Notifications themselves are sent with a direct desktop bus call, without any extra libraries involved.

If Plasma's "turn off screen" setting is disabled entirely, the service exits, since a warning for something that can never happen would be misleading. If only automatic dimming is disabled, it keeps running but can never actually fire, since dimming is what triggers it in the first place.

## 8. Installing, updating, and pairing

`install.py` needs the TV to already be turned on, since it has to reach it directly and complete a pairing prompt on screen. It begins by quietly removing any previous installation, which also cleans up naming left over from older versions, while carrying the existing pairing key across that reset so re-pairing is not needed. It builds its own virtual environment for bscpylgtv, and on distributions that need a separate package for virtual environments to work at all, installs that one package; everywhere else this step does nothing. On a wired connection, it also offers to enable Wake-on-LAN on the network card itself, since that makes turning the TV off at suspend noticeably more reliable.

`lgpowercontrol-authorize` repeatedly asks the TV for its status, which both triggers the pairing prompt and validates whatever key already exists. It only deletes a saved key once the TV has explicitly refused pairing, never when the TV was simply unreachable at the time.

`lgpowercontrol-update` fetches the newest release from GitHub, or the newest development commit when asked for it directly, keeps the existing configuration file in place, and reruns the installer over the freshly downloaded code. Every step of this, from talking to GitHub's API to unpacking the download, uses only the standard library.

`lgpowercontrol-update-check` is the periodic, timer-triggered version of that same check, surfaced as a desktop notification rather than an interactive prompt, and it never installs anything on its own.

## 9. Troubleshooting

Every part of the system logs to the system journal under a single tag, and every line also carries a short label saying which part produced it, so following or searching the journal in one place shows the whole picture.

The core command can be run directly to probe things by hand: running it with the wake command walks through the entire wake-up sequence, and running it with the status command gives a quick read of the TV's state along with a clear exit code. The display state the watcher relies on can be read from the same place it reads it from, and the current flag files under `/run` show what the suspend and resume logic currently believes.

If the TV does not wake after resuming over Wi-Fi, this is almost always a Wake-on-LAN packet lost while the wireless link settles, and the log should show it being resent; if it eventually gives up, the link took longer to settle than the retry budget allows.

If the TV reports that it woke up but the screen stays dark, this points at the screen-on command's ambiguous error being misread, and is worth checking against the TV's current firmware behavior.

If suspending itself takes noticeably long, the likely cause is the turn-off command running against a TV that was already off, or one that could not be reached during the pre-suspend step.

If the TV never turns off at suspend at all, check whether the network is bridged or systemd-networkd-only, both of which are not supported for this, or whether the dispatcher script is actually installed.

If the display watcher never reacts to anything, the display most likely is not exposing its power state where the watcher expects to find it.

If the warning notification never appears, either automatic dimming is turned off in Plasma's power settings, or the tool the notification service depends on has stopped reporting dimming the way it expects.

If pairing fails after the TV has been factory reset, rerun the pairing helper, and use the status command's exit code to tell whether this is really a pairing problem or just an unreachable TV.

If the TV sits on a different subnet and never wakes, confirm it still answers a basic network probe while asleep, and that the Wake-on-LAN port is not being filtered between the two networks.

## 10. Development

The stable branch is what users install by default; a separate working branch is where changes happen first. A release bumps the version, fast-forwards the stable branch to match, and tags it.

The usual development cycle is editing the code directly, reinstalling it, which preserves both configuration and pairing, and watching the journal live for the result.

The project deliberately avoids adding any dependency beyond the one WebOS library it needs, prefers staying simple over covering every possible edge case, and treats the suspend and resume design described above as settled: the alternatives it does not use were tried and did not work. Continuous integration compiles and lints every script on every change.
