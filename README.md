## LGPowerControl

Designed for setups where an LG TV is used as a computer monitor. Unlike regular monitors, TVs don't respond naturally to the computer's power state. This script bridges that gap by automatically turning the TV **on at boot** and **off at shutdown**, and blanking/unblanking the screen when the computer display sleeps or wakes.

The screen monitor reads DPMS state directly from the kernel DRM subsystem rather than relying on a separate idle timer. This means it respects fullscreen applications, compositor-level sleep inhibitors (e.g. KDE's "block sleep"), and other system power management mechanisms without interfering with them.

### What triggers TV control

| Event | TV behaviour |
|---|---|
| **System boot** | TV turns on |
| **System shutdown / halt** | TV turns off |
| **Display sleeps** (DPMS off — idle timer, manual `xset dpms force off`, etc.) | TV turns off |
| **Display wakes** (DPMS on — mouse/keyboard activity, etc.) | TV turns on |
| **Screen lock** | No change — display stays active (DPMS stays on) |
| **Suspend / hibernate** | Not supported — see [Limitations](#limitations) |

Compatible with **Debian-based** (Ubuntu, Mint), **Fedora-based**, and **Arch-based** (EndeavourOS, Manjaro) systems.

Especially useful for OLED users looking to reduce burn-in risk.

---

## How It Works

### TV communication: WebSocket + Wake-on-LAN

The script talks to the TV over your local network using **[bscpylgtv](https://github.com/chros73/bscpylgtv)**, a Python library that connects to the TV's built-in WebOS WebSocket server (port 3000). Commands like `power_off`, `turn_screen_on`, `turn_screen_off`, and `set_input` are sent as JSON messages over this connection.

The problem: when the TV is fully off, its network stack is also off — the WebSocket is unreachable. That's where **Wake-on-LAN** comes in. A magic packet (a broadcast UDP frame containing the TV's MAC address repeated 16 times) is sent to the LAN. The TV's NIC has a dedicated low-power circuit that listens for this even when the TV is off, and wakes it on receipt. Once the TV's WebSocket is up, subsequent commands (like `set_input`) go over bscpylgtv normally.

### Screen state detection: DRM sysfs

The monitor polls `/sys/class/drm/card*/card*-*/` every 2 seconds. This is the Linux kernel's **Direct Rendering Manager** subsystem, which owns the display hardware directly. Each connected output (e.g. `card1-HDMI-A-1`) exposes two files:

- `status` — whether a display is physically connected (`connected` / `disconnected`)
- `dpms` — the current DPMS power state (`On`, `Off`, `Standby`, `Suspend`)

The monitor iterates all DRM connectors, finds any connected one, and returns `on` if its `dpms` reads `On`, or `off` otherwise.

Why DRM sysfs over userspace alternatives:

| Method | Problem |
|---|---|
| `xset q` / `xrandr` | X11-only; breaks under Wayland |
| `wlopm`, `swayidle` | Compositor-specific |
| logind `IdleHint` | Only set after inactivity timeout; `xset dpms force off` doesn't trigger it |
| **DRM sysfs** | Kernel-level, session-agnostic, works on X11 and Wayland |

Because DPMS state in DRM is written by the compositor or X server whenever it blanks the display — whether from idle timeout, `xset dpms force off`, or a power management event — reading it at the kernel level captures all these cases uniformly. A fallback to logind `IdleHint` is used only if no DRM sysfs entries are found at all (e.g. some VM setups).

**Why screen lock doesn't trigger TV-off:** A screen locker keeps the display active — DPMS stays `On` — it just renders a lock screen on top. The DRM state is unchanged, so the monitor sees no transition and does nothing.

### Systemd integration

Three services handle the lifecycle:

| Service | Type | Trigger |
|---|---|---|
| `lgpowercontrol-boot.service` | `oneshot` | After `network-online.target` — network is required to reach the TV |
| `lgpowercontrol-shutdown.service` | `oneshot` | `Before=poweroff.target halt.target`, `Conflicts=reboot.target` — runs late in shutdown, skipped on reboot |
| `lgpowercontrol-monitor.service` | `simple` (persistent) | `After=network-online.target`, `Restart=on-failure` |

The shutdown service sets `DefaultDependencies=no` to opt out of the normal dependency graph, allowing it to run late enough in the shutdown sequence to reliably reach the TV before the network is torn down.

### Power mode vs screen mode

The `power` / `screen` mode distinction controls what "off" means:

- **`power` mode:** OFF sends `power_off` via bscpylgtv (full power cut). ON sends a WoL magic packet. Suitable for a daily-driver setup.
- **`screen` mode:** OFF sends `turn_screen_off` (TV stays in standby — panel off, but OS and network remain active). ON sends `turn_screen_on`. Faster to wake, but keeps the TV's internals running and is only meaningful if the TV supports the WebOS screen-off state.

---

## Requirements

* **Linux** with `systemd`
* **LG TV with WebOS** (e.g., CX, C1–C4 OLED)
* `iproute2`, and `wakeonlan` (Debian/Arch) or `net-tools` for `ether-wake` (Fedora)
* **Python 3** with `python3-venv` (required on Debian-based systems)

---

## Installation

### 1. Prepare the TV

1. **Power on** the TV and connect it to your network.
2. **Enable Wake-on-LAN:**
   * **CX:** Settings → All Settings → Connection → Mobile Connection Management → **TV On with Mobile**
   * **C1–C4:** All Settings → General → Devices → External Devices → **TV On With Mobile** → Turn on via Wi-Fi
   * *Required even when using a wired Ethernet connection.*
3. **Recommended:** Set a static DHCP lease for the TV in your router so its IP doesn't change.

### 2. Run the Installer

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
sudo ./install.sh 192.168.X.X # IP address for your LG TV
```

The IP address is optional — the installer will prompt for it if omitted. The script must be run as root or with `sudo`.

### What the installer does

* Detects your package manager (`apt`, `dnf`, `pacman`) and offers to install any missing dependencies automatically
* Pings the TV and retrieves its MAC address from the ARP table
* Prompts for an HDMI port (1–5) so the TV switches to the right input on power-on
* Prompts for power mode (see [Configuration](#configuration))
* Installs `bscpylgtv` into a dedicated Python venv at `/opt/lgpowercontrol/bscpylgtv`
* Installs and enables three systemd services for boot, shutdown, and screen sleep/wake
* Writes `/opt/lgpowercontrol/lgpowercontrol.conf` with all settings filled in
* Triggers a one-time pairing request on the TV — **accept it with the remote**

---

## Configuration

All settings live in `/opt/lgpowercontrol/lgpowercontrol.conf`, written by the installer.

After editing, restart the monitor service to apply changes:

```bash
sudo systemctl restart lgpowercontrol-monitor.service
```

Boot and shutdown services read the config each time they run — no restart needed for those.

### Hardware (refreshed automatically on reinstall)

| Variable | Description |
|---|---|
| `LGTV_IP` | TV IP address |
| `LGTV_MAC` | TV MAC address |
| `WOL_CMD` | Wake-on-LAN command |
| `HDMI_INPUT` | HDMI port to switch to on power-on (empty to skip) |

### Behavior

| Variable | Options | Default | Description |
|---|---|---|---|
| `BOOT_SHUTDOWN_MODE` | `power`, `screen` | `screen` | `power`: WoL on at boot, power off at shutdown. `screen`: screen on/off only (TV stays in standby) |
| `MONITOR_MODE` | `power`, `screen` | `screen` | `power`: full power off/on when display sleeps/wakes. `screen`: screen off/on only |

### Logging

All events are logged — power on/off, state transitions, TV command output, and errors.

View logs with:

```bash
journalctl -t lgpowercontrol
journalctl -t lgpowercontrol -f   # follow live
```

---

## Screen State Monitor

Installed automatically as `lgpowercontrol-monitor.service`.

* **System service:** Runs independently of which user is logged in. Works in multi-user setups.
* **DE-agnostic:** Works with GNOME, KDE Plasma, Cinnamon, and others on both X11 and Wayland.
* **Detection:** Polls DPMS state from the kernel DRM subsystem (`/sys/class/drm/`) every 2 seconds. Falls back to logind `IdleHint` when DRM sysfs is unavailable.

---

## Limitations

### What This Project Does NOT Do

* **Screen lock:** Locking the screen (via keyboard shortcut, GNOME/KDE lock screen, `loginctl lock-session`, etc.) does **not** turn off the TV. The monitor watches the DPMS power state reported by the kernel — locking the screen leaves the display active (DPMS stays on). The TV will only turn off when the display actually sleeps, which is controlled by your desktop's display sleep / screen blanking timer.

**To make the TV turn off when you lock:** configure your desktop so the display blanks shortly before or when the screen locks. How to do this in common desktops:

  * **GNOME:** Use **Settings → Power → Screen Blank** to set the shortest available delay, and keep **Settings → Privacy & Security → Screen Lock → Automatic Screen Lock** enabled so the session locks after inactivity.
  * **KDE Plasma:** System Settings → Power Management → Display and Brightness → **Turn off screen** → set **"When locked"** to "Immediately". This powers off the display right after locking.
  * **Cinnamon:** System Settings → Power Management → **"Turn off the screen when inactive for"** → set a short delay. The display powers down on this timer regardless of whether the screen is locked.
  * **Any desktop, X11 only:** customize your lock keyboard shortcut to run a small script that forces the display off immediately:
    ```bash
    xset dpms force off && loginctl lock-session
    ```
    The monitor will detect the DPMS change within 2 seconds and send the TV-off command.

* **Sleep / Suspend / Hibernate:** Not supported. When the system suspends, the network goes down before the TV control command can connect, so the TV stays on. This is a known limitation with no current fix planned — contributions are welcome.

---

## Uninstallation

```bash
sudo ./uninstall.sh
```

Stops and removes all systemd services (including any legacy services from older versions) and `/opt/lgpowercontrol`.

---

## Notes

* **Logs:** `journalctl -t lgpowercontrol`
* **Tested on:** Linux Mint 22.3 Cinnamon, Ubuntu 25.10, Fedora 43, EndeavourOS and CachyOS (April 2026) with an LG OLED42C35LA

---

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) — inspiration (Windows)
* [LGBuddy](https://github.com/Faceless3882/LG_Buddy) — inspiration (Linux)