## LGPowerControl

Automatically turns an LG TV on and off based on your computer's power state. Designed for setups where an LG TV is used as a monitor.

| Event | TV behaviour |
|---|---|
| **System boot** | TV turns on |
| **System shutdown / halt** | TV turns off |
| **Display sleeps** (idle timer, manual blank, etc.) | TV turns off |
| **Display wakes** (mouse/keyboard activity, etc.) | TV turns on |
| **Screen lock** | No change |
| **Suspend / hibernate** | Not supported |

Compatible with **Debian-based** (Ubuntu, Mint), **Fedora-based**, and **Arch-based** (EndeavourOS, Manjaro) systems. Works on both X11 and Wayland.

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
sudo ./install.sh 192.168.X.X # IP address for your LG TV
```

The IP address is optional — the installer will prompt for it if omitted. The script must be run as root or with `sudo`.

The installer will:

* Install any missing dependencies
* Retrieve the TV's MAC address from the ARP table
* Prompt for an HDMI port (1–5) to switch to on power-on
* Prompt for power mode (see [Configuration](#configuration))
* Install and enable three systemd services for boot, shutdown, and display sleep/wake
* Trigger a one-time pairing request on the TV — **accept it with the remote**

---

## Configuration

All settings live in `/opt/lgpowercontrol/lgpowercontrol.conf`.

After editing, restart the monitor service to apply changes:

```bash
sudo systemctl restart lgpowercontrol-monitor.service
```

Boot and shutdown services read the config each time they run — no restart needed for those.

### Hardware

| Variable | Description |
|---|---|
| `LGTV_IP` | TV IP address |
| `LGTV_MAC` | TV MAC address |
| `WOL_CMD` | Wake-on-LAN command |
| `HDMI_INPUT` | HDMI port to switch to on power-on (empty to skip) |

### Behavior

- **`power` mode:** Fully powers the TV off and on. Maximum energy savings; takes a few seconds to turn on.
- **`screen` mode:** Turns the screen off without fully powering down. Wakes instantly, but uses slightly more power while idle.

| Variable | Default | Description |
|---|---|---|
| `BOOT_SHUTDOWN_MODE` | `power` | Mode used at boot and shutdown |
| `POWER_MODE` | `screen` | Mode used when the display sleeps or wakes due to inactivity |

### Logging

All events are logged — power on/off, state transitions, TV command output, and errors.

```bash
journalctl -t lgpowercontrol
journalctl -t lgpowercontrol -f   # follow live
```

---

## Limitations

* **Screen lock** does not turn off the TV. The TV turns off only when the display actually sleeps. To link them, configure your desktop to blank the display when locking:
  * **GNOME:** Settings → Power → Screen Blank → set shortest delay
  * **KDE Plasma:** System Settings → Power Management → Display and Brightness → Turn off screen → set **"When locked"** to "Immediately"
  * **Cinnamon:** System Settings → Power Management → "Turn off the screen when inactive for" → set a short delay
  * **X11 (any desktop):** bind your lock shortcut to `xset dpms force off && loginctl lock-session`

* **Suspend / Hibernate** is not supported. The network goes down before the TV command can connect.

---

## Uninstallation

```bash
sudo ./uninstall.sh
```

Stops and removes all systemd services and `/opt/lgpowercontrol`.

---

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) — inspiration (Windows)
* [LGBuddy](https://github.com/Faceless3882/LG_Buddy) — inspiration (Linux)
