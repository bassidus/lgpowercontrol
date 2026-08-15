# LGPowerControl

## 1. Introduction

LGPowerControl links an LG TV's power state to your computer's power state. It is made for setups where the TV is used as a computer monitor. OLED owners get the most benefit, since it reduces the time the screen sits on with a static image and lowers the risk of burn-in.

It targets KDE Plasma on Wayland. Other desktop environments work too, apart from the on-screen warning before the TV turns off, which is Plasma-specific.

These distributions are tested before a release:

- CachyOS and EndeavourOS
- Fedora 44 and Bazzite
- openSUSE Tumbleweed
- Ubuntu 22.04 LTS and Linux Mint 22.3

Turning the TV off at suspend and on again at resume is confirmed on physical machines running CachyOS and Bazzite. The other distributions are tested in virtual machines, which cannot suspend and resume, so everything except that part is confirmed there. Distributions outside this list generally work as well; these are simply the ones actually tried.

## 2. How it works

The TV turns on at boot, when the computer wakes, and when the display wakes. It turns off at shutdown and at suspend. When the computer goes idle, the TV's screen turns off first. After ten minutes of that idle state, LGPowerControl turns the TV fully off instead, which lets it reach a faster standby mode (see [Wake-up speed](#5-limitations)).

If the TV is shared with another source, such as a game console or a work laptop on a second HDMI input, set `HDMI_INPUT` in the configuration file to the input this computer is connected to and `SHARED_TV` to `"1"`. The TV is then left alone whenever it is showing the other source: it is not turned off when this computer suspends or goes idle, and a TV that is already on when this computer wakes keeps whatever it is showing. A TV that was off is still switched to this computer, since turning it on was this computer's doing.

To keep the TV on regardless of what it is showing, set `POWER_OFF_AT_SUSPEND` or `POWER_OFF_AT_SHUTDOWN` to `"0"`. This suits a setup where the TV is switched over to another device after the computer is already asleep or shut down. Waking the computer still turns the TV back on, and the idle behaviour above is unaffected.

On KDE Plasma, a desktop notification appears shortly before the TV turns off from idling. This needs Plasma's automatic screen dimming to be enabled, under System Settings, Power Management. The warning time is set with `OFF_WARNING_SECONDS` in the configuration file.

The mechanics underneath are drawn out in [docs/architecture.html](docs/architecture.html): the wake loop, the guard that decides when the TV may be turned off, the timing race at suspend, and the idle escalation. It is a standalone page — open it in a browser from a clone, since GitHub shows HTML files as source. Reading it is not needed to use LGPowerControl; it is for anyone looking at the code.

## 3. Installation

The TV needs to be an LG WebOS model, for example a CX or C1 through C4 OLED. An internet connection is needed during installation, since the control library is downloaded during setup.

Before installing, turn the TV on and connect it to the network. Enable Wake-on-LAN on the TV. On CX models this setting lives under Connection, Mobile Connection Management, TV On with Mobile. On C1 through C4 it lives under General, Devices, External Devices, TV On With Mobile, Turn on via Wi-Fi. This setting is required even on a wired connection, despite its name. Giving the TV a static DHCP lease in your router is recommended. Enabling Always Ready, under General, is also recommended: it noticeably shortens wake-up time.

To install, clone the repository, set the TV's IP address in `lgpowercontrol.conf`, and run the installer as root.

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
nano lgpowercontrol.conf
sudo ./install.py
```

The installer first checks the machine for anything it would clash with. Leftovers from an older version of LGPowerControl are removed automatically. [LG_Buddy](https://github.com/Staphylococcus/LG_Buddy) controls the same TVs in the same way and cannot share a machine with LGPowerControl, so the installer stops and asks for it to be removed first. Running the installer with `--force` skips this check, but installing the two side by side is strongly discouraged.

The installer then sets everything up and starts a one-time pairing request. Accept it on the TV with the remote.

On a wired connection, the installer also offers to enable Wake-on-LAN on the computer's own network card. This makes turning the TV off at suspend more reliable, and is worth accepting. It can be changed later with `lgpowercontrol wol --enable` or `--disable`.

If the TV loses its pairing, for example after a factory reset, re-pair with `lgpowercontrol authorize`.

Only the installer itself needs root. It hands the configuration file and the pairing key to the user who ran it, so everything afterwards — controlling the TV, re-pairing, editing the configuration — works without `sudo`. The program files stay owned by root, since the system services run them. Changing the network card's Wake-on-LAN setting is the one exception: it edits a system-wide network connection, which some distributions ask for a password before allowing.

## 4. Configuration

All settings live in `/opt/lgpowercontrol/lgpowercontrol.conf` and are documented there. After editing, restart the affected services.

```bash
nano /opt/lgpowercontrol/lgpowercontrol.conf
sudo systemctl restart lgpowercontrol-monitor.service
systemctl --user restart lgpowercontrol-notify.service
```

Logging is off by default. If something is not working, set `LOGGING="1"` in the configuration file and restart the services above. Everything LGPowerControl does then goes to the system journal under one tag.

```bash
journalctl -t lgpowercontrol      # view the log
```

## 5. Limitations

Locking the screen alone does not turn the TV off. The TV reacts to the display actually going blank, not to the session being locked. To get the TV to turn off when you lock the computer, configure the desktop to blank the display on lock. On KDE Plasma this is under Power Management, Display and Brightness, Turn off screen, When locked, set to Immediately. On GNOME it is under Settings, Power, Screen Blank, set to the shortest delay. On any X11 desktop, a lock shortcut can be bound to run `xset dpms force off` together with the usual lock command.

On a bridged network setup, the TV cannot be turned off automatically at suspend. It has to be turned off manually, or left to turn off on its own no-signal timeout a few minutes later. Waking the TV at resume still works normally.

The same applies on a computer without NetworkManager, for example one set up to use systemd-networkd instead. Every desktop distribution ships NetworkManager by default, so this only affects systems where it was deliberately replaced. The installer says so when it finds none.

If the TV has been off for more than about ten minutes, waking it can take several seconds. Enabling Always Ready on the TV shortens this considerably. Waking over Wi-Fi adds a couple of seconds on top. This is the reason LGPowerControl turns the TV fully off after ten minutes of idling instead of leaving it merely screen-off: left alone, the TV drops into a deeper sleep state with a slower wake-up on its own, while a full turn-off lets Always Ready hold it in a faster standby. On TVs without Always Ready this makes no difference either way.

## 6. Troubleshooting

If the TV does not turn off when the computer suspends, enabling Wake-on-LAN on the computer's wired network adapter usually fixes it. The installer offers this during setup; if it was declined, it can be turned on later.

```bash
lgpowercontrol wol --enable
```

It can be turned off again with `lgpowercontrol wol --disable`. Enabling it also lets any machine on the network wake the computer itself with a matching magic packet. This only helps on a wired connection, since Wake-on-LAN is an Ethernet feature; on Wi-Fi there is no equivalent, and the only workaround is turning the TV off by hand before suspending.

If a problem is not covered here, set `LOGGING="1"` as described in [Configuration](#4-configuration) and reproduce it. The journal then says what LGPowerControl asked the TV to do and what came back, which is also what to attach to a bug report.

If the TV wakes with the computer but goes dark again a few seconds later, the usual cause on KDE Plasma is the "Lock after waking from sleep" setting, which is on by default on a fresh install. It blanks the screen shortly after resume through the lock screen, and LGPowerControl simply follows that. It can be disabled under System Settings, Security & Privacy, Screen Locking, or left on with a longer "Turn off screen when locked" delay under Power Management.

## 7. Updating and removal

To update, clone the repository again into a fresh directory, fill in `lgpowercontrol.conf` as at the first install, and run the installer. Settings change between releases, so a configuration file from an older version is never carried over.

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
nano lgpowercontrol.conf
sudo ./install.py
```

The installer reinstalls over the existing installation and carries the pairing key across, so the TV does not have to be paired again. Everything else is replaced, `/opt/lgpowercontrol/lgpowercontrol.conf` included — read the installed one first if you want your old values in front of you.

To remove LGPowerControl, run the installer with `--uninstall` from the cloned repository, cloning it again first if it is no longer around.

```bash
sudo ./install.py --uninstall
```

This removes every installed service along with `/opt/lgpowercontrol`.

## 8. About the project

An AI assistant helps refine the code and suggest solutions, with a human deciding what gets built and reviewing every change. Nothing lands untested: changes are checked on real hardware and in virtual machines across the supported distributions, and the codebase is kept deliberately minimal. If something still looks like AI slop, please open an issue.

It relies on [bscpylgtv](https://github.com/chros73/bscpylgtv) to talk to the TV, and took inspiration from [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion).
