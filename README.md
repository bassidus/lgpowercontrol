# LGPowerControl

## 1. Introduction

LGPowerControl links an LG TV's power state to your computer's power state. It is made for setups where the TV is used as a computer monitor. OLED owners get the most benefit, since it reduces the time the screen sits on with a static image and lowers the risk of burn-in.

It targets KDE Plasma on Wayland. Other desktop environments likely work too. It has been confirmed on CachyOS, EndeavourOS, Fedora, Bazzite and Ubuntu.

## 2. How it works

The TV turns on at boot, when the computer wakes, and when the display wakes. It turns off at shutdown and at suspend. When the computer goes idle, the TV's screen turns off first. After ten minutes of that idle state, LGPowerControl turns the TV fully off instead, which lets it reach a faster standby mode (see [Wake-up speed](#5-limitations)).

On KDE Plasma, a desktop notification appears shortly before the TV turns off from idling. This needs Plasma's automatic screen dimming to be enabled, under System Settings, Power Management. The warning time is set with `OFF_WARNING_SECONDS` in the configuration file.

## 3. Installation

The TV needs to be an LG WebOS model, for example a CX or C1 through C4 OLED. An internet connection is needed during installation, since the control library is downloaded during setup.

Before installing, turn the TV on and connect it to the network. Enable Wake-on-LAN on the TV. On CX models this setting lives under Connection, Mobile Connection Management, TV On with Mobile. On C1 through C4 it lives under General, Devices, External Devices, TV On With Mobile, Turn on via Wi-Fi. This setting is required even on a wired connection, despite its name. Giving the TV a static DHCP lease in your router is recommended. Enabling Always Ready, under General, is also recommended: it noticeably shortens wake-up time.

To install, clone the repository, set the TV's IP address in `lgtvpc.conf`, and run the installer as root.

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
nano lgtvpc.conf
sudo ./install.py
```

The installer sets everything up and starts a one-time pairing request. Accept it on the TV with the remote.

On a wired connection, the installer also offers to enable Wake-on-LAN on the computer's own network card. This makes turning the TV off at suspend more reliable, and is worth accepting. It can be changed later with `sudo /opt/lgtvpc/lgtvpc-wol.py --enable` or `--disable`.

If the TV loses its pairing, for example after a factory reset, re-pair with `sudo /opt/lgtvpc/authorize.py`.

## 4. Configuration

All settings live in `/opt/lgtvpc/lgtvpc.conf` and are documented there. After editing, restart the affected services.

```bash
sudo systemctl restart lgtvpc-monitor.service
systemctl --user restart lgtvpc-notify.service
```

Everything is logged to the system journal under one tag.

```bash
journalctl -t lgtvpc      # view the log
journalctl -t lgtvpc -f   # follow live
```

Logging can be turned off with `LOGGING="off"` in the configuration file.

## 5. Limitations

Locking the screen alone does not turn the TV off. The TV reacts to the display actually going blank, not to the session being locked. To get the TV to turn off when you lock the computer, configure the desktop to blank the display on lock. On KDE Plasma this is under Power Management, Display and Brightness, Turn off screen, When locked, set to Immediately. On GNOME it is under Settings, Power, Screen Blank, set to the shortest delay. On any X11 desktop, a lock shortcut can be bound to run `xset dpms force off` together with the usual lock command.

On a bridged network setup, the TV cannot be turned off automatically at suspend. It has to be turned off manually, or left to turn off on its own no-signal timeout a few minutes later. Waking the TV at resume still works normally.

If the TV has been off for more than about ten minutes, waking it can take several seconds. Enabling Always Ready on the TV shortens this considerably. Waking over Wi-Fi adds a couple of seconds on top. This is the reason LGPowerControl turns the TV fully off after ten minutes of idling instead of leaving it merely screen-off: left alone, the TV drops into a deeper sleep state with a slower wake-up on its own, while a full turn-off lets Always Ready hold it in a faster standby. On TVs without Always Ready this makes no difference either way.

## 6. Troubleshooting

If the TV does not turn off when the computer suspends, enabling Wake-on-LAN on the computer's wired network adapter usually fixes it. The installer offers this during setup; if it was declined, it can be turned on later.

```bash
sudo /opt/lgtvpc/lgtvpc-wol.py --enable
```

It can be turned off again with `sudo /opt/lgtvpc/lgtvpc-wol.py --disable`. Enabling it also lets any machine on the network wake the computer itself with a matching magic packet. This only helps on a wired connection, since Wake-on-LAN is an Ethernet feature; on Wi-Fi there is no equivalent, and the only workaround is turning the TV off by hand before suspending.

If the TV wakes with the computer but goes dark again a few seconds later, the usual cause on KDE Plasma is the "Lock after waking from sleep" setting, which is on by default on a fresh install. It blanks the screen shortly after resume through the lock screen, and LGPowerControl simply follows that. It can be disabled under System Settings, Security & Privacy, Screen Locking, or left on with a longer "Turn off screen when locked" delay under Power Management.

## 7. Updating and removal

```bash
sudo /opt/lgtvpc/update.py
```

This installs the latest release. Running it with `--dev` installs the latest development commit instead. LGPowerControl also checks for updates on its own once a week and shows a desktop reminder when one is available; nothing is ever installed automatically. `UPDATE_CHECK_DAYS` and `UPDATE_CHANNEL` in the configuration file tune or disable this check.

To remove LGPowerControl, run the uninstaller from the cloned repository, cloning it again first if it is no longer around.

```bash
sudo ./uninstall.py
```

This removes every installed service along with `/opt/lgtvpc`.

## 8. About the project

An AI assistant helps refine the code and suggest solutions, with a human deciding what gets built and reviewing every change. Nothing lands untested: changes are checked on real hardware and in virtual machines across the supported distributions, and the codebase is kept deliberately minimal. If something still looks like AI slop, please open an issue.

The project is looking for people interested in helping maintain it long-term: testing on other distributions and desktops, triaging issues, or contributing code. See [CONTRIBUTING.md](CONTRIBUTING.md) to help out. For how the software works internally, see [ARCHITECTURE.md](ARCHITECTURE.md).

It relies on [bscpylgtv](https://github.com/chros73/bscpylgtv) to talk to the TV, and took inspiration from [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion).
