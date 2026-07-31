# LGPowerControl
Automatically turns an LG TV on and off with your computer's power state. Made for setups where an LG TV is used as a computer monitor, especially useful for OLED users looking to reduce burn-in risk.

Primarily made for **KDE Plasma on Wayland**, but should probably work with other desktop environments too. Confirmed to work with CachyOS, EndeavourOS, Fedora 44, Bazzite and Ubuntu.

## How it works
The TV follows your computer's power state:

* **Turns on** at boot, when the computer wakes, and when the display wakes
* **Turns off** at shutdown and suspend
* **Screen off** at inactivity (controlled by display sleep in System Settings), escalating to a full [turn off after 10 minutes](#wake-up-can-take-several-seconds)

## Notifications
On KDE Plasma, LGPowerControl shows a notification shortly before the TV turns off (see `OFF_WARNING_SECONDS` in the config file). This requires **Dim automatically** to be enabled under **System Settings → Power Management**.

## Requirements
* An **LG WebOS TV** (for example CX or C1–C4 OLED models)
* An **internet connection during installation** — the LG control library is downloaded during setup

## Installation
### 1. Prepare the TV
* Turn on the TV and connect it to your network.
* Enable **Wake-on-LAN**. Even if it says `Turn on via Wi-Fi`, this is also required when using wired Ethernet.
* **CX:** `Settings → All Settings → Connection → Mobile Connection Management → TV On with Mobile`
* **C1–C4:** `All Settings → General → Devices → External Devices → TV On With Mobile → Turn on via Wi-Fi`
* **Recommended:** Assign the TV a static DHCP lease in your router.
* **Recommended:** Enable **Always Ready**: `Settings → General → Always Ready`   On an OLED42C35LA, this reduces wake-up time from around 10 seconds to approximately 3–4 seconds. Other models may behave differently.

### 2. Run the installer

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
nano lgtvpc.conf   # set your TV's IP
sudo ./install.py  
```

The installer configures everything and initiates a one-time pairing request on the TV, be ready to accept it with the remote.

On wired network, the installer also offers to enable Wake-on-LAN on the computer's network card, this is recommended to enable as it makes turning the TV off at suspend more reliable. You can change this setting anytime with `sudo /opt/lgtvpc/lgtvpc-wol.py --enable` or `--disable`.

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

On bridged network setups, the TV cannot be turned off automatically at suspend at all. It needs to be turned off manually or wait until its own no-signal timeout turns it off a few minutes later. Waking the TV when the computer resumes works regardless.

### Wake-up can take several seconds

If the TV has been off for more than approximately 10 minutes, waking it can take several seconds. Enabling **Always Ready** significantly reduces this delay, see [Prepare the TV](#1-prepare-the-tv). Wake-up over Wi-Fi can take a few additional seconds.

This is also why LGPowerControl turns the TV fully off after 10 minutes of screen-off: left merely screen-off, the TV soon drops into a deep sleep state on its own with the slowest wake-up, while a full turn off lets Always Ready park it in a faster standby. On TVs without Always Ready this makes no difference to the wake-up time.

## Troubleshooting

### TV doesn't turn off at suspend / "Network is unreachable" in the log

If the TV doesn't turn off when the computer suspends, try to enable Wake-on-LAN on the computer's wired adapter. This makes turning off the TV at suspend more reliable (the installer offers this; if you declined, enable it anytime):

```bash
sudo /opt/lgtvpc/lgtvpc-wol.py --enable
```

To undo it: `sudo /opt/lgtvpc/lgtvpc-wol.py --disable`.

Note: this also lets any machine on your network wake the computer with a magic packet.

This only works if the computer itself is on a wired connection, Wake-on-LAN is an Ethernet feature. If the computer connects over Wi-Fi, there's no equivalent fix; the only known workaround is to let the TV turn off before suspending manually.

### TV turns off right after waking from sleep

On KDE Plasma, if the TV wakes up with the computer but goes dark again a few seconds later, the likely cause is **Lock after waking from sleep** (enabled by default on a fresh installation): the lock screen turns the display off shortly after resume, and LGPowerControl follows the display. Disable it under `System Settings → Security & Privacy → Screen Locking`, or keep locking but set **Turn off screen when locked** to a longer delay under `Power Management`.

## Updating

```bash
sudo /opt/lgtvpc/update.py
```

Offers to install the latest GitHub release (`--dev` installs the latest dev-branch commit instead). Your configuration and TV pairing are preserved during updates.

LGPowerControl also checks for new versions once a week and shows a desktop notification when an update is available, repeating as a reminder until you update — **nothing is installed automatically**. See `UPDATE_CHECK_DAYS` and `UPDATE_CHANNEL` in the config file to tune or disable this.

## Uninstallation

From the cloned repository, the same directory you ran the installer from (clone it again if it's gone):

```bash
sudo ./uninstall.py
```

Removes all services and `/opt/lgtvpc`.

## AI transparency

The original script was entirely handwritten, without any AI involvement. Later in the project's development, an AI assistant (Claude) has helped refine the code and suggest solutions, with a human deciding what to build and reviewing every change. Nothing lands untested: changes are verified on real hardware and in virtual machines across the supported distributions, and the codebase is deliberately kept minimal.

If you spot something that looks like AI slop anyway, please open an issue.

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — Python library for communicating with LG WebOS TVs
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) for inspiration

Curious how it all works under the hood? See [ARCHITECTURE.md](ARCHITECTURE.md).
