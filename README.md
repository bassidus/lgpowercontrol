# LGPowerControl

Automatically turns an LG TV on and off with your computer's power state. Made for setups where an LG TV is used as a computer monitor — especially useful for OLED users looking to reduce burn-in risk.

Primarily made for **KDE Plasma on Wayland**, but should work with other desktop environments too — X11 or Wayland — on Arch, Debian/Ubuntu and Fedora-based distributions.

## How it works

The TV follows your computer's power state:

* **Turns on** at boot, when the computer wakes, and when the display wakes
* **Turns off** at shutdown and suspend
* When the display goes to sleep, the TV screen turns off, followed by a full power off after 10 minutes

The full power off is deliberate: left with just the screen off, the TV soon drops into a deep standby that is slow to wake — powered off it stays quick to wake instead (with **Always Ready** enabled, see below).

On KDE Plasma, LGPowerControl can also show a notification shortly before the TV turns off — see `OFF_WARNING_SECONDS` in the config file. Requires **Dim automatically** to be enabled in **System Settings → Power Management**.

Curious how it all works under the hood? See [ARCHITECTURE.md](ARCHITECTURE.md).

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
nano lgtvpc.conf   # set your TV's IP (MAC is auto-detected)
sudo ./install.py
```

The installer configures everything and initiates a one-time pairing request on the TV — **accept it with the remote**.

On wired computers, the installer also offers to enable Wake-on-LAN on the computer's network card — recommended, as it makes turning the TV off at suspend fully reliable (the pros and cons are listed when it asks). Change your mind anytime with `sudo /opt/lgtvpc/lgtvpc-wol.py --enable` or `--disable`.

If the TV loses its pairing (for example after a factory reset), re-pair with `sudo /opt/lgtvpc/authorize.py`.

## Configuration

All settings are documented in `/opt/lgtvpc/lgtvpc.conf`. After editing, restart the services:

```bash
sudo systemctl restart lgtvpc-monitor.service
systemctl --user restart lgtvpc-notify.service
```

## Logging

```bash
journalctl -t lgtvpc      # view the log
journalctl -t lgtvpc -f   # follow live
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

### Bridged network setups can't turn off the TV at suspend

On bridged network setups, the TV cannot be turned off at suspend at all — its own no-signal timeout turns it off a few minutes later. Waking the TV when the computer resumes works regardless.

### Wake-up can take several seconds

If the TV has been off for more than approximately 10 minutes, waking it can take several seconds. Enabling **Always Ready** significantly reduces this delay — see [Prepare the TV](#1-prepare-the-tv). Wake-up over Wi-Fi can take a few additional seconds; LGPowerControl retries the power-on request until the TV responds.

This is a limitation of the TV itself, not LGPowerControl.

## Troubleshooting

### TV doesn't turn off at suspend / "Network is unreachable" in the log

If the TV is still on when the computer suspends (typically a manual suspend), turning it off can occasionally fail — the log then shows `power_off: OSError: [Errno 101] Network is unreachable` and the TV stays on until its own no-signal timeout. To avoid it, let the TV turn off before suspending manually, or enable Wake-on-LAN on the computer's wired adapter, which makes turning off the TV at suspend fully reliable (the installer offers this; if you declined, enable it anytime):

```bash
sudo /opt/lgtvpc/lgtvpc-wol.py --enable
```

It auto-detects your wired network device; pass `--interface eno1` (or similar) if you have more than one. To undo it: `sudo /opt/lgtvpc/lgtvpc-wol.py --disable`.

Note: this also lets any machine on your network wake the computer with a magic packet.

This only works if the computer itself is on a wired connection — Wake-on-LAN is an Ethernet feature, and `lgtvpc-wol.py` will tell you so if it can't find a wired network device. If the computer connects over Wi-Fi, there's no equivalent fix; the workarounds above (let the TV turn off before suspending manually, or accept the occasional miss) are the only options.

### TV turns off right after waking from sleep

On KDE Plasma, if the TV wakes up with the computer but goes dark again a few seconds later, the likely cause is **Lock after waking from sleep** (enabled by default on a fresh installation): the lock screen turns the display off shortly after resume, and LGPowerControl follows the display. Disable it under `System Settings → Security & Privacy → Screen Locking`, or keep locking but set **Turn off screen when locked** to a longer delay under `Power Management`.

### Immutable distros (Bazzite, Silverblue, …)

On distros where `/usr` is read-only, the installer can't place its suspend hook there. It installs a small background service (`lgtvpc-sleep.service`) that does the same job — everything works the same, this note just explains the extra service.

## Updating

```bash
sudo /opt/lgtvpc/update.py
```

Offers to install the latest GitHub release (`--dev` installs the latest dev-branch commit instead). Your configuration and TV pairing are preserved during updates.

LGPowerControl also checks for new versions once a week and shows a desktop notification when an update is available, repeating as a reminder until you update — **nothing is installed automatically**. See `UPDATE_CHECK_DAYS` and `UPDATE_CHANNEL` in the config file to tune or disable this.

## Uninstallation

From the cloned repository — the same directory you ran the installer from (clone it again if it's gone):

```bash
sudo ./uninstall.py
```

Removes all services and `/opt/lgtvpc`.

## AI transparency

The original script was entirely handwritten, without any AI involvement. Later in the project's development, an AI assistant (Claude) has helped refine the code and suggest solutions, with a human deciding what to build and reviewing every change. Nothing lands untested: changes are verified on real hardware and in virtual machines across the supported distributions, and the codebase is deliberately kept minimal.

If you spot something that looks like AI slop anyway, please open an issue.

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) — inspiration (Windows)
* [LGBuddy](https://github.com/Faceless3882/LG_Buddy) — inspiration (Linux)
