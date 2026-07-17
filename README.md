# LGPowerControl

Automatically turns an LG TV on and off with your computer's power state. Made for setups where an LG TV is used as a monitor — especially useful for OLED users looking to reduce burn-in risk.

Made primarily for KDE Plasma on Wayland, but should work on other desktops too — X11 or Wayland — on Arch, Debian/Ubuntu and Fedora-based distributions.

The TV follows your computer: it turns on at boot, wake and display wake, and turns off at shutdown and suspend. When the display goes to sleep the TV screen turns off, followed by a full power off after 10 minutes.

On KDE Plasma it can also show a notification shortly before the TV turns off — see `OFF_WARNING_SECONDS` in the config file. Requires "Dim automatically" in System Settings → Power Management.

## Requirements

* **Linux** with `systemd`
* **LG TV with WebOS** (e.g., CX, C1–C4 OLED)
* **Internet connection during install** — missing dependencies are installed automatically

## Installation

### 1. Prepare the TV

1. **Power on** the TV and connect it to your network.
2. **Enable Wake-on-LAN** (required even on wired Ethernet):
   * **CX:** Settings → All Settings → Connection → Mobile Connection Management → **TV On with Mobile**
   * **C1–C4:** All Settings → General → Devices → External Devices → **TV On With Mobile** → Turn on via Wi-Fi
3. **Recommended:** Give the TV a static DHCP lease in your router.
4. **Recommended:** Enable **Always Ready** (Settings → General → Always Ready) — the TV then wakes from standby in ~3–4 seconds instead of ~10. Verified on an OLED42C35LA; other models may differ.

### 2. Run the installer

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
nano lgpowercontrol.conf   # set your TV's IP (MAC is auto-detected)
sudo ./install.sh
```

The installer sets everything up and triggers a one-time pairing request on the TV — **accept it with the remote**.

If the TV ever forgets the pairing (e.g. after a factory reset), re-pair with `sudo /opt/lgpowercontrol/authorize.sh`.

## Configuration

All settings are documented in `/opt/lgpowercontrol/lgpowercontrol.conf`. If the TV is on a different subnet/VLAN than the computer, set `WOL_L3="yes"` so the wake packet is routed instead of broadcast. After editing, restart the services:

```bash
sudo systemctl restart lgpowercontrol-monitor.service
systemctl --user restart lgpowercontrol-notify.service
```

## Logging

```bash
journalctl -t lgpowercontrol      # view the log
journalctl -t lgpowercontrol -f   # follow live
```

Disable with `LOGGING="no"` in the config file.

## Limitations

* **Screen lock** doesn't turn off the TV — only display sleep does. To link them, make your desktop blank the display on lock:
  * **KDE Plasma:** Power Management → Display and Brightness → Turn off screen → **"When locked": Immediately**
  * **GNOME:** Settings → Power → Screen Blank → shortest delay
  * **X11 (any desktop):** bind your lock shortcut to `xset dpms force off && loginctl lock-session`

* **TV-off at suspend** normally runs in NetworkManager's pre-down window. When that event never fires — for example if the computer's own network card has Wake-on-LAN enabled, which makes NM leave the network untouched at sleep — a bundled systemd sleep hook takes over. Bridged networks remain a known gap: there the network is gone before either mechanism runs, and the TV's own no-signal timeout turns it off a few minutes later. Waking at resume works regardless.

* **Waking from standby takes several seconds** when the TV has been off for more than around 10 minutes. This can be sped up considerably with **Always Ready** enabled (see [Prepare the TV](#1-prepare-the-tv)). Wake-up over Wi-Fi can add a few more seconds — the power-on is retried until the TV responds. This is a limitation of the TV itself, not the script.

## Updating

```bash
sudo /opt/lgpowercontrol/update.sh
```

Offers to install the latest GitHub release. Settings and TV pairing survive the update.

## Uninstallation

```bash
sudo ./uninstall.sh
```

Removes all services and `/opt/lgpowercontrol`.

## AI transparency

The original script was entirely handwritten, without any AI involvement. Later in the project's life an AI assistant (Claude) has helped refine the code and suggest solutions, with a human deciding what to build and reviewing every change. Nothing lands untested: changes are verified on real hardware and in VMs across the supported distributions, and the codebase is deliberately kept minimal. If you spot something that looks like AI slop anyway, please open an issue.

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) — inspiration (Windows)
* [LGBuddy](https://github.com/Faceless3882/LG_Buddy) — inspiration (Linux)
