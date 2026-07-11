# LGPowerControl

Automatically turns an LG TV on and off based on your computer's power state. Designed for setups where an LG TV is used as a monitor — especially useful for OLED users looking to reduce burn-in risk.

Supports Arch, Debian/Ubuntu and Fedora-based distributions, on both X11 and Wayland.

| Event | TV behaviour |
|---|---|
| **System boot** | TV turns on |
| **System shutdown / halt** | TV turns off |
| **Display sleeps** (idle timer, manual blank, etc.) | TV turns off |
| **Display wakes** (mouse/keyboard activity, etc.) | TV turns on |
| **Screen lock** | No change (see [Limitations](#limitations)) |
| **Suspend / hibernate** | Not supported |

On KDE Plasma it can also show a desktop notification shortly before the TV turns off (see `OFF_WARNING_SECONDS` in the config file). The warning is timed from Plasma's "Dim automatically" event, so that setting must be enabled in System Settings → Power Management → Display and Brightness.

## Requirements

* **Linux** with `systemd`
* **LG TV with WebOS** (e.g., CX, C1–C4 OLED)
* **Internet connection during install** — missing dependencies (Python 3, wakeonlan, …) are installed automatically

## Installation

### 1. Prepare the TV

1. **Power on** the TV and connect it to your network.
2. **Enable Wake-on-LAN** (required even on wired Ethernet):
   * **CX:** Settings → All Settings → Connection → Mobile Connection Management → **TV On with Mobile**
   * **C1–C4:** All Settings → General → Devices → External Devices → **TV On With Mobile** → Turn on via Wi-Fi
3. **Recommended:** Set a static DHCP lease for the TV in your router so its IP doesn't change.

### 2. Run the installer

1. Clone the repository:
```bash
   git clone https://github.com/bassidus/lgpowercontrol.git
   cd lgpowercontrol
```

2. Edit the configuration file and set your TV's IP address (the MAC address is auto-detected if left empty):
```bash
   nano lgpowercontrol.conf
```

3. Run the installer:
```bash
   sudo ./install.sh
```

The installer installs any missing dependencies, sets up the systemd services, and triggers a one-time pairing request on the TV — **accept it with the remote**.

## Configuration

All settings are documented in `lgpowercontrol.conf`, installed to `/opt/lgpowercontrol/lgpowercontrol.conf`. After editing, restart the services to apply changes:

```bash
sudo systemctl restart lgpowercontrol-monitor.service
systemctl --user restart lgpowercontrol-notify.service
```

The notify service (the TV-off warning) only works on KDE Plasma — on other desktops it exits silently and can be ignored.

## Logging

```bash
journalctl -t lgpowercontrol
journalctl -t lgpowercontrol -f   # follow live
```

## Limitations

* **Screen lock** does not turn off the TV — only actual display sleep does. To link them, configure your desktop to blank the display when locking:
  * **KDE Plasma:** System Settings → Power Management → Display and Brightness → Turn off screen → set **"When locked"** to "Immediately"
  * **GNOME:** Settings → Power → Screen Blank → set shortest delay
  * **X11 (any desktop):** bind your lock shortcut to `xset dpms force off && loginctl lock-session`
* **Suspend / hibernate** is not supported. The network goes down before the TV command can connect.

## Uninstallation

```bash
sudo ./uninstall.sh
```

Stops and removes all systemd services and `/opt/lgpowercontrol`.

## AI transparency

Most of the code in this project is written with the help of an AI assistant (Claude), with a human deciding what to build and reviewing every change. Nothing lands untested: changes are verified on real hardware and in VMs across the supported distributions, and the codebase is deliberately kept minimal — if a line or comment doesn't earn its place, it goes. If you spot something that looks like AI slop anyway, please open an issue.

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) — inspiration (Windows)
* [LGBuddy](https://github.com/Faceless3882/LG_Buddy) — inspiration (Linux)
