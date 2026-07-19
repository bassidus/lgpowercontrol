# LGPowerControl

Automatically turns an LG TV on and off with your computer's power state. Made for setups where an LG TV is used as a computer monitor — especially useful for OLED users looking to reduce burn-in risk.

Primarily made for **KDE Plasma on Wayland**, but should work with other desktop environments too — X11 or Wayland — on Arch, Debian/Ubuntu and Fedora-based distributions.

## How it works

The TV follows your computer's power state:

* **Turns on** at boot, when the computer wakes, and when the display wakes
* **Turns off** at shutdown and suspend
* When the display goes to sleep, the TV screen turns off, followed by a full power off after 10 minutes

The full power off is deliberate: left with just the screen off, the TV soon drops itself into a deep standby that is slow to wake, on an internal timer that cannot be stopped over the network. A full power off instead lands it in **Always Ready** standby (when enabled), which wakes in a few seconds — see [Wake-up can take several seconds](#wake-up-can-take-several-seconds).

On KDE Plasma, LGPowerControl can also show a notification shortly before the TV turns off — see `OFF_WARNING_SECONDS` in the config file. Requires **Dim automatically** to be enabled in **System Settings → Power Management**.

## Requirements

* **systemd** and **Python 3** (preinstalled on virtually every distribution)
* An **LG WebOS TV** (for example CX or C1–C4 OLED models)
* An **internet connection during installation** — the LG control library is downloaded during setup

## Installation

### 1. Prepare the TV

1. Turn on the TV and connect it to your network.

2. Enable **Wake-on-LAN**. This is required even when using wired Ethernet.

   **CX:**

   `Settings → All Settings → Connection → Mobile Connection Management → TV On with Mobile`

   **C1–C4:**

   `All Settings → General → Devices → External Devices → TV On With Mobile → Turn on via Wi-Fi`

3. **Recommended:** Assign the TV a static DHCP lease in your router.

4. **Recommended:** Enable **Always Ready**:

   `Settings → General → Always Ready`

   On an OLED42C35LA, this reduces wake-up time from around 10 seconds to approximately 3–4 seconds. Other models may behave differently.

### 2. Run the installer

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
nano lgpowercontrol.conf   # set your TV's IP (MAC is auto-detected)
sudo ./install.sh
```

The installer configures everything and initiates a one-time pairing request on the TV — **accept it with the remote**.

If the TV loses its pairing (for example after a factory reset), re-pair with `sudo /opt/lgpowercontrol/authorize.sh`.

## Configuration

All settings are documented in `/opt/lgpowercontrol/lgpowercontrol.conf`. After editing, restart the services:

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

### Screen lock does not turn off the TV

The TV responds to **display sleep**, not screen locking. If you want the TV to turn off when the computer is locked, configure your desktop to blank the display on lock:

* **KDE Plasma:**
  `Power Management → Display and Brightness → Turn off screen → When locked: Immediately`
* **GNOME:**
  `Settings → Power → Screen Blank → shortest delay`
* **X11 (any desktop):**
  Bind your lock shortcut to `xset dpms force off && loginctl lock-session`

### Turning off the TV during suspend

Normally, the TV is turned off through NetworkManager's pre-down event. If that event does not occur — for example when the computer's own network adapter has Wake-on-LAN enabled and NetworkManager leaves the network untouched during sleep — a bundled systemd sleep hook is used instead.

Bridged networks are a known limitation: there the network is gone before either mechanism can run, and the TV's own no-signal timeout turns it off a few minutes later. Waking the TV when the computer resumes works regardless.

### Wake-up can take several seconds

If the TV has been off for more than approximately 10 minutes, waking it can take several seconds. Enabling **Always Ready** significantly reduces this delay — see [Prepare the TV](#1-prepare-the-tv). Wake-up over Wi-Fi can take a few additional seconds; LGPowerControl retries the power-on request until the TV responds.

This is a limitation of the TV itself, not LGPowerControl.

## Updating

```bash
sudo /opt/lgpowercontrol/update.sh
```

Offers to install the latest GitHub release (`--dev` installs the latest dev-branch commit instead). Your configuration and TV pairing are preserved during updates.

LGPowerControl also checks for new versions once a week and shows a desktop notification when an update is available, repeating as a reminder until you update — **nothing is installed automatically**. See `UPDATE_CHECK_DAYS` and `UPDATE_CHANNEL` in the config file to tune or disable this.

## Uninstallation

From the cloned repository — the same directory you ran the installer from (clone it again if it's gone):

```bash
sudo ./uninstall.sh
```

Removes all services and `/opt/lgpowercontrol`.

## AI transparency

The original script was entirely handwritten, without any AI involvement. Later in the project's development, an AI assistant (Claude) has helped refine the code and suggest solutions, with a human deciding what to build and reviewing every change. Nothing lands untested: changes are verified on real hardware and in virtual machines across the supported distributions, and the codebase is deliberately kept minimal.

If you spot something that looks like AI slop anyway, please open an issue.

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) — inspiration (Windows)
* [LGBuddy](https://github.com/Faceless3882/LG_Buddy) — inspiration (Linux)
