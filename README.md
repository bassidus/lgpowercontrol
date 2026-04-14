## LGPowerControl

Designed for setups where an LG TV is used as a computer monitor. Unlike regular monitors, TVs don't respond naturally to the computer's power state. This script bridges that gap by automatically turning the TV **on at boot** and **off at shutdown**, and blanking/unblanking the screen when the computer display sleeps or wakes.

The screen monitor reads DPMS state directly from the kernel DRM subsystem rather than relying on a separate idle timer. This means it respects fullscreen applications, compositor-level sleep inhibitors (e.g. KDE's "block sleep"), and other system power management mechanisms without interfering with them.

Compatible with **Debian-based** (Ubuntu, Mint), **Fedora-based**, and **Arch-based** (EndeavourOS, Manjaro) systems.

Especially useful for OLED users looking to reduce burn-in risk.

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
./install.sh 192.168.X.X
```

The IP address is optional — the installer will prompt for it if omitted. Root access is requested automatically via `sudo`.

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
| `BOOT_SHUTDOWN_MODE` | `power`, `screen` | `power` | `power`: WoL on at boot, power off at shutdown. `screen`: screen on/off only (TV stays in standby) |
| `MONITOR_MODE` | `power`, `screen` | `power` | `power`: full power off/on when display sleeps/wakes. `screen`: screen off/on only |

---

## Screen State Monitor

Installed automatically as `lgpowercontrol-monitor.service`.

* **System service:** Runs independently of which user is logged in. Works in multi-user setups.
* **DE-agnostic:** Works with GNOME, KDE Plasma, Cinnamon, and others on both X11 and Wayland.
* **Detection:** Polls DPMS state from the kernel DRM subsystem (`/sys/class/drm/`) every 2 seconds. Falls back to logind `IdleHint` when DRM sysfs is unavailable.

---

## Limitations

### What This Project Does NOT Do

* **Sleep / Suspend / Hibernate:** This project does not reliably support Linux sleep, suspend, or hibernate modes. This is a deliberate choice — I don't use these features myself and have prioritized boot/shutdown and screen sleep/wake functionality instead. If you need sleep/suspend/hibernate support, contributions and pull requests are welcome.

---

## Uninstallation

```bash
./uninstall.sh
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