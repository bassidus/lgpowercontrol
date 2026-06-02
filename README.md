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

Especially useful for OLED users looking to reduce burn-in risk.

---

## Requirements

* **Arch-based** distro (EndeavourOS, CachyOS, etc.)
* **LG TV with WebOS** (e.g., CX, C1–C4 OLED)
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
git clone -b simple https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
sudo ./install.sh 192.168.X.X
```

The TV must be **on and reachable** when running the installer — it resolves the MAC address from the ARP table.

The installer will:

* Install missing dependencies via `pacman`
* Resolve the TV's MAC address
* Create `/opt/lgpowercontrol/lgpowercontrol.conf` with default settings
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
| `WOL_CMD` | Wake-on-LAN command (array) |
| `HDMI_INPUT` | HDMI port to switch to on power-on (e.g. `HDMI_1`; empty to skip) |

### Behavior

- **`power` mode:** Fully powers the TV off and on. Maximum energy savings; takes a few seconds to turn on.
- **`screen` mode:** Turns the screen off without fully powering down. Wakes instantly, but uses slightly more power while idle.

| Variable | Default | Description |
|---|---|---|
| `BOOT_SHUTDOWN_MODE` | `power` | Mode used at boot and shutdown |
| `POWER_MODE` | `screen` | Mode used when the display sleeps or wakes due to inactivity |

### Logging

All events are logged — power on/off, DRM state transitions, and TV command output.

```bash
journalctl -t lgpowercontrol
journalctl -t lgpowercontrol -f   # follow live
```

---

## Limitations

* **Screen lock** does not turn off the TV. The TV turns off only when the display actually sleeps. To link them, configure your desktop to blank the display when locking:
  * **GNOME:** Settings → Power → Screen Blank → set shortest delay
  * **KDE Plasma:** System Settings → Power Management → Display and Brightness → Turn off screen → set **"When locked"** to "Immediately"

* **Suspend / Hibernate** is not supported. The network goes down before the TV command can connect.

---

## Uninstallation

```bash
sudo ./uninstall.sh
```

Stops, disables, and removes all systemd services and `/opt/lgpowercontrol`.

---

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs