# virtual-webos-tv

A test double for an LG webOS TV's SSAP control surface. It speaks the protocol faithfully
enough that [`bscpylgtv`](https://github.com/chros73/bscpylgtv) cannot tell the difference, so
[`lgpowercontrol`](https://github.com/bassidus/lgpowercontrol) can be exercised without a real TV
— including the failure branches a real TV will never produce on demand.

It is **not** a simulator of webOS. LG's own webOS TV Simulator emulates the app runtime, not the
remote-control surface; see `CLAUDE.md` section 7 for why that dead end is worth remembering.

## What works

Stages 1 and 2:

- TLS transport on `wss://127.0.0.1:3001`, and the three-message pairing handshake
- The command set `lgpowercontrol` uses: `getForegroundAppInfo`, `getPowerState`,
  `system/turnOff`, `turnOffScreen`, `turnOnScreen`, `switchInput`, `getExternalInputList`
- A power state machine over the five states observed on real hardware, with the
  `processing: "Screen On"` window and the observed 4 s / 5 s wake times
- The `-102` ambiguity that the wake loop is built around
- A Wake-on-LAN listener on UDP 9, and a TV that genuinely refuses TCP while in deep standby
- Fault injection covering every error branch in `bscpylgtv`'s `request()`
- Latency, jitter and loss knobs, calibrated against the times measured on real hardware

## Requirements

`lgpowercontrol` must be installed at `/opt/lgpowercontrol` — the server borrows its vendored
`websockets`, and that is also where the rigs find `bscpylgtv`. `openssl` is used once to
generate a self-signed certificate into `cert/`, which is gitignored.

## The three rigs

```
./check_guard_paths.py     # the OFF side: check_power_off_guard()
./check_wake_paths.py      # the ON side: the wake loop
sudo ./check_wake_paths.py # ...including the three Wake-on-LAN cases
./check_timing_paths.py    # a slow and a lossy TV
```

Or all of them, after the unit suite, from the repository root:

```
./tests/run_all.py         # exit 2 means something was skipped, not that everything passed
```

**What gets tested: the working tree, by default.** `--target installed` runs the copy under
`/opt` instead, which is the one to use when the install itself is in question — the wrappers,
the pinned interpreter, the `--target` lib directory. Either way each rig prints the version and
the exact file it imported, and refuses to run if that file is not the one asked for. That line
is worth reading: the very first run here went against a build that predated the feature under
test, and every case took the ordinary path while the table looked like it meant something.

Each case gets its own virtual TV, conf file and pairing database. The rigs run the real CLI in
a subprocess, so the assertions are about the real exit code, and then ask the TV's journal what
it was actually told to do and what state that left it in.

That second half is not decoration. Several cases exit 0 for opposite reasons — the guard letting
an off command through and the guard standing down both exit 0 — so the exit code alone cannot
tell a working guard from a broken one.

```
case                   exit  final state     WoL  result
---------------------  ----  --------------  ---  ------
already-awake          0     Active          1    ok
screen-off             0     Active          1    ok
screen-saver           0     Active          1    ok
active-standby-wol     0     Active          1    ok
deep-standby-offline   0     Active          1    ok
deep-standby-readable  0     Active          1    ok
never-wakes            1     Active Standby  0    ok
set-input-after-lag    0     Active          1    ok
shared-tv-keeps-input  0     Active          1    ok
off-lands-in-standby   0     Active Standby  0    ok
screen-off-command     0     Screen Off      0    ok
```

Two results are worth reading twice:

- `pairing-refused` in the guard rig exits **3**, not 0. The guard does fail open, but `OFF` then
  falls through to `tv_cmd("power_off")`, which meets the same unpairable TV and fails again.
  Fail-open is proved by the second pairing attempt, not by the exit code.
- `never-wakes` exits **1** after burning all 15 `WAKE_ATTEMPTS`. It is the only case that proves
  the loop ever stops, and the only slow one — about 16 s.

Without root, UDP port 9 cannot be bound and the three Wake-on-LAN cases are **skipped**; the rig
then exits **2**, not 0. A rig that reports success with a third of its cases unrun is exactly the
trap this project already has a scar from.

`--keep` leaves each case's conf, pairing database and TV journal on disk. `--only NAME` runs one.

## Latency and loss

`--calibrated` sets the times measured on the real TV: 57 ms for the handshake, 7.7 ms per
command. The handshake is where the time goes, which is why the OFF guard's extra session costs
about one more handshake rather than one more command.

The three latency knobs are separate because they land on opposite sides of the client's only
timeout:

| knob | inside `wait_for(connect(), timeout=2)`? | result when exceeded |
|---|---|---|
| `--latency-handshake` | yes | `TimeoutError`, a retry, finally rc 2 |
| `--latency-pairing` | **no** | hangs forever |
| `--latency-command` | **no** | hangs forever |
| `--loss` (drop a response) | **no** | hangs forever |
| `--close` (hang up instead) | — | recoverable, but see below |

`bscpylgtv` has exactly one `wait_for` in the connect path and it wraps `websockets.connect` and
nothing else. The pairing `recv()`s and `request()`'s future are outside it, and `ping_interval=
None` — which `lgpowercontrol` always passes — disables the one keepalive that would otherwise
notice a dead-but-open connection. So a TV that answers TLS and then goes quiet hangs the CLI
indefinitely. Three cases in `check_timing_paths.py` demonstrate that, and passing means they
did not finish.

`--jitter` and the two probabilities are driven by a seeded RNG (`--seed`, default 1), so a run
repeats exactly. Both dice are rolled before the delay, so changing a latency setting cannot
change which requests a given seed drops.

### One case found a real defect

`connection-dropped` used to exit 1 with a traceback. `asyncio.CancelledError` subclasses
`BaseException`, so `tv_cmd`'s `except Exception` catch-all never saw it and it escaped `main()`
— a TV that went away read as a program bug rather than the network event it is, and `monitor.py`
classifies on that exit code. `lgpowercontrol` now names it explicitly and reports rc 2.

The case therefore expects **rc 2 and no traceback**. If it fails with exit 1 and a
`CancelledError` traceback in the stderr dump, the build under test predates the fix — check the
version line at the top of the run, and under `--target installed` reinstall with
`sudo ./install.py`.

## Running it as a daemon

Leave a TV standing in one terminal and fire commands at it from another. It keeps its state and
its pairing key between commands, so it behaves like a television in the room rather than a
fixture that resets.

```
./virtual_webos_tv.py          # terminal 1: the TV
./tvsend STATUS                # terminal 2
./tvsend OFF --guard 1
./tvsend ON --hdmi 2
```

Every request **and every response** is shown:

```
  0.00s  virtual TV ready
         state=Active  app=com.webos.app.hdmi1  mac=02:00:00:00:00:01  offline in: Suspend
  0.00s  listening on wss://127.0.0.1:3001
  2.56s  conn 1    connected
  2.56s  conn 1  > register  (no key yet)
  2.56s  conn 1  < registered
  2.56s  conn 1  > getPowerState
  2.56s  conn 1  < ok  state=Active
  2.56s  conn 1    disconnected
  4.43s  control $ standby
  4.43s  state   = Active -> Active Standby  (control)
  6.93s  wol     * magic packet from control
  6.93s  state   = Active Standby [Screen On]  (wol-wake-start)
 10.95s  state   = Active Standby -> Active  (wol-wake-done)
```

Typing into the TV's terminal changes it, the way pressing a button on a real one would:

```
on | standby | suspend | screen-off | screensaver   set the power state
wol                    deliver a magic packet by hand (no root needed)
app <id>               what getForegroundAppInfo reports; 'hdmi2' expands
fail <endpoint>=<form> inject a fault; 'fail none' clears them
lag <seconds>          how long switchInput refuses after a wake
loss <p> | close <p>   drop responses / hang up, with probability p
state                  print what the TV looks like right now
help | quit
```

`wol` is worth knowing about: binding UDP 9 needs root, but *receiving* the packet does not, so
typing `wol` exercises the whole wake path as an ordinary user.

Use `tvsend` rather than calling `lgpowercontrol` directly. The installed CLI reads
`/opt/lgpowercontrol/lgpowercontrol.conf`, which holds the real TV's address — `lgpowercontrol
OFF` typed while testing turns off the actual television. `tvsend` goes through the same runner
the rigs use, which refuses any conf that is not loopback and any MAC that could belong to a
real device.

Starting flags still work, for a TV that begins somewhere specific:

```
./virtual_webos_tv.py --power-state "Active Standby"
./virtual_webos_tv.py --power-state Suspend --offline-states ""   # answers in deep standby
./virtual_webos_tv.py --error current_app=not-found
./virtual_webos_tv.py --refuse-pairing
./virtual_webos_tv.py --ignore-wol             # a TV that never comes back
./virtual_webos_tv.py --input-lag-seconds 3    # switchInput fails for 3s after a wake
```

`--error` takes an endpoint (`current_app`, `power_state`, `power_off`, `screen_off`, `screen_on`,
`set_input`, `get_inputs`) and a fault (`not-found`, `error`, `no-payload`, `return-false`,
`screen-102`). The first four are the four ways `bscpylgtv`'s `request()` can raise.

The readable view and the stdin control both switch themselves on when stderr and stdin are
terminals, so the rigs — whose streams are pipes — get JSON and no control channel without
passing any flags. `--journal` always writes JSON regardless. `--pretty`/`--no-pretty` and
`--control`/`--no-control` override the detection.

**The daemon is stateful on purpose, and the rigs are not.** Run `OFF` and the next `STATUS`
reports `Active Standby`. That is the point here, and it is exactly why each rig case gets its
own fresh server instead — a case must not depend on what the previous one did. Do not merge
the two models.

## The state machine

| from | event | to |
|---|---|---|
| `Active` / `Screen Off` / `Screen Saver` | `system/turnOff` | `Active Standby` |
| `Active` / `Screen Saver` | `turnOffScreen` | `Screen Off` |
| `Screen Off` / `Screen Saver` | `turnOnScreen` | `Active` |
| `Active` | `turnOnScreen` | **`-102`** — screen already on |
| `Active Standby` | `turnOnScreen` | **`-102`** — TV asleep |
| `Active Standby` / `Suspend` | magic packet | `processing: "Screen On"`, then `Active` |

The two `-102` rows are the ambiguity the wake loop exists to resolve: the same error means
"nothing to do" and "the TV is not awake yet", and only `getPowerState` tells them apart. `Suspend`
refuses TCP by default, so a TV in deep standby produces a real `ConnectionRefusedError` rather
than a faked one.

## Safety

- The server binds loopback by default, and the rigs write `LGTV_IP="127.0.0.1"` themselves and
  refuse to start the CLI if the conf says otherwise. Pointing a test at the real TV would turn it
  off mid-use.
- `send_wol()` always broadcasts to `255.255.255.255:9`, and that packet does leave the machine.
  The rigs therefore use `02:00:00:00:00:01`, a locally administered address that belongs to no
  device, and refuse to run if that constant is ever changed to a globally administered one — a
  real TV's MAC is globally administered by definition.
- The rigs redirect `ON_LOCK` and `TV_OFF_FLAG` out of `/run`. `lgpowercontrol-monitor` runs for
  real on this machine, and `ON` takes a non-blocking flock whose whole purpose is to make a
  second `ON` return 0 and do nothing — a test holding it would silently swallow a real wake.
- The rigs check that the installed `lgpowercontrol` actually carries the code under test and
  print its version. Version 4.0.1 has no guard at all, and a run against it looks like a test
  result.

## What a green run does and does not prove

It proves the guard's and the wake loop's own branches, against the real `bscpylgtv` error
contract read out of the installed library. That is worth having: `PyLGTVServiceNotFoundError`,
`PyLGTVPairException` and a `-102` refusal cannot be provoked from a real C3, and until now the
wake loop could only be exercised by suspending hardware and watching a television.

It does not clear the hardware. Every response form here encodes a *belief* about webOS, and a
wrong belief passes green while reality breaks — which feels like verification, and is worse than
not testing. `CLAUDE.md` section 6 has the details. In particular, latency emulation can never
answer the pre-down question: that race runs against NetworkManager flushing the interface, not
against the TV's response time.

Everything not derivable from `bscpylgtv` or from a hardware observation is marked `GUESSED` in
the code. The open questions those marks correspond to are listed in `CLAUDE.md` section 9 —
each one is answerable in a few minutes with a real TV, and worth more than any test here.
