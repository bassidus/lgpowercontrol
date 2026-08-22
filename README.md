# LGPowerControl

Automatically controls an LG TV's power state based on your computer's power and display state. Designed for setups where an LG TV is used as a computer monitor, especially OLED displays.

Primarily developed for **KDE Plasma on Wayland**, but works on other desktops too. The warning notification is KDE-specific.

## How it works

The TV turns on when the computer boots, wakes or the display wakes. It turns off when the computer sits idle for a period of time or when it shuts down or suspends.

Idling happens in two steps. When the desktop blanks the display, the TV's screen is turned off while the TV itself stays on, so the picture comes straight back. Ten minutes later the TV is turned off properly, which is what puts it into **Always Ready** and keeps the next wake-up fast.

Everything is configured in `lgpowercontrol.conf`, which is edited before installing and lives in `/opt/lgpowercontrol/` afterwards:

| Setting | Shipped as | What it does |
| --- | --- | --- |
| `LGTV_IP` | empty | The TV's address, for example `"192.168.1.100"`. Required: the installer stops until it is set and the TV answers on it. |
| `LGTV_MAC` | empty | Filled in by the installer, read from the ARP table while the TV is on. It is where the Wake-on-LAN packet is sent, so fill it in by hand only if detection fails. |
| `HDMI_INPUT` | empty | The input this computer is connected to, for example `"2"`. The TV is switched to it whenever this computer turns it on. Empty means the input is never switched. |
| `SHARED_TV` | `"0"` | `"1"` when another device shares the TV. Needs `HDMI_INPUT`. |
| `POWER_OFF_AT_SUSPEND` | `"1"` | Turn the TV off when this computer suspends. |
| `POWER_OFF_AT_SHUTDOWN` | `"1"` | Turn the TV off when this computer shuts down. |
| `OFF_WARNING_SECONDS` | `"120"` | How far ahead of the TV turning off from idling the warning notification is shown. `0` turns the warning off. KDE Plasma only. |
| `NOTIFY_POLL_SECONDS` | `"5"` | How often that service checks whether the screen has dimmed. A lower value lands the warning closer to the configured time and uses a little more CPU. |
| `LOGGING` | `"0"` | Whether the services write what they do to the journal. `lgpowercontrol log --enable` sets it and restarts the services for you. |

With `SHARED_TV="1"`, LGPowerControl leaves the TV alone while another input is active: it is only turned off while it is showing `HDMI_INPUT`, and a TV that is already on keeps its picture when this computer wakes. A TV that was off is still turned on and switched to this computer, since turning it on was this computer's doing.

To prevent the TV from turning off at suspend or shutdown, set:

```ini
POWER_OFF_AT_SUSPEND="0"
POWER_OFF_AT_SHUTDOWN="0"
```

Both only hold back the automatic events. A hand-typed `lgpowercontrol off` always goes through, and neither of them affects the idle timeout: keeping the TV off a static image is what this program exists for.

On KDE Plasma, a notification is shown `OFF_WARNING_SECONDS` before the TV is turned off due to inactivity. This requires automatic screen dimming to be enabled in **System Settings → Power Management**, since the dim is what starts the countdown. A warning set further ahead than the gap between dimming and screen-off arrives as soon as the screen dims.

## Install / Update / Uninstall

The TV must be an **LG webOS model** with Wake-on-LAN support, such as the CX or C1–C4 OLED series. Newer models **might** work, but they have not been tested.

Before installing:

* Connect the TV to your network and turn it on.
* Enable **TV On With Mobile / Wake-on-LAN** on the TV. Despite the name, this is required on a wired connection too.
* A static DHCP lease is recommended.
* **Always Ready** is recommended for faster wake-up.

Clone the repository, configure the TV's IP address, then run the installer:

```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
nano lgpowercontrol.conf
sudo ./install.py # --update / --uninstall
```

## Commands

Everything the services do can also be done by hand:

| Command | What it does |
| --- | --- |
| `lgpowercontrol on` | Turns the TV on and switches to `HDMI_INPUT`. |
| `lgpowercontrol off` | Turns the TV off. |
| `lgpowercontrol screen_off` | Turns the screen off but leaves the TV on. |
| `lgpowercontrol status` | Prints the TV's power state, for example `state=Active`. |
| `lgpowercontrol authorize` | Pairs with the TV. Accept the dialog that appears on the screen. |
| `lgpowercontrol wol --status` | Shows Wake-on-LAN on this computer's wired adapter. |
| `lgpowercontrol wol --enable` | Enables it, which makes turning the TV off at suspend more reliable. |
| `lgpowercontrol wol --disable` | Disables it again. |
| `lgpowercontrol wol --interface IFACE` | Added to any of the three above to pick the adapter when there is more than one. |
| `lgpowercontrol log [N]` | Shows the last N log lines (default 50). Add `-f` to keep watching. |
| `lgpowercontrol log --enable` | Turns logging on. `--disable` turns it off, `--status` says which it is. |
| `sudo lgpowercontrol update` | Updates to the latest release. |
| `sudo lgpowercontrol uninstall` | Removes the installation, its services and the TV pairing. |

The four TV commands take `--retries N` for the number of connect attempts (default 3). They exit **0** on success, **1** on error, **2** when the TV is unreachable and **3** when it is not paired.

`POWER_OFF_AT_SUSPEND` and `POWER_OFF_AT_SHUTDOWN` only hold back the automatic events; a hand-typed `off` always goes through. `SHARED_TV` applies to it as well, so the TV is still left alone while another input is active.

## Configuration

Configuration is stored in:

```text
/opt/lgpowercontrol/lgpowercontrol.conf
```

Every setting is listed under [How it works](#how-it-works) and documented in the file itself.

Most settings apply immediately. `OFF_WARNING_SECONDS` and `NOTIFY_POLL_SECONDS` are kept in memory by a running service, which needs to be restarted:

```bash
systemctl --user restart lgpowercontrol-notify.service
```

`LOGGING` is kept in memory the same way, but `lgpowercontrol log --enable` and `--disable` restart the services for you.

## Limitations

**Screen locking alone does not turn the TV off.** The display must actually be blanked. Configure your desktop to turn the display off when locking if this behaviour is desired.

On KDE Plasma:

**System Settings → Power Management → Display and Brightness → Turn off screen → When locked → Immediately**

Turning the TV off at suspend requires a working **NetworkManager connection**. On a bridged network setup it cannot be done reliably, and without NetworkManager, on a system using systemd-networkd for example, it is unavailable by design. Waking the TV at resume works normally in both cases.

Wake-up can take several seconds if the TV has been fully off for a while. **Always Ready** significantly reduces this delay. Wi-Fi adds some additional latency.

## Troubleshooting

If the TV does not turn off when the computer suspends, enable Wake-on-LAN on the computer's wired network adapter:

```bash
lgpowercontrol wol --enable
```

Note that enabling it also lets any machine on your network wake this computer with a matching magic packet. You can disable it again with:

```bash
lgpowercontrol wol --disable
```

If the TV wakes with the computer but turns off again shortly afterwards, check whether KDE Plasma is locking the screen after resume. The lock screen can blank the display, which LGPowerControl correctly interprets as an idle display.

For other problems, turn logging on:

```bash
sudo lgpowercontrol log --enable
```

That sets `LOGGING="1"` in the configuration file and restarts the running services so they pick it up. Without `sudo` it still sets the value, but restarting the background service needs root, and it says so rather than asking. Reproduce the issue, then read the log:

```bash
lgpowercontrol log        # the last 50 lines; log 200 for more, log -f to follow
```

The lines go to the system journal under the tag `lgpowercontrol`, so `journalctl -t lgpowercontrol` reaches the same messages with all of journalctl's own options available.

## About

LGPowerControl uses [bscpylgtv](https://github.com/chros73/bscpylgtv) to communicate with the TV and was inspired by [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion). The mechanics underneath are [documented separately](https://lgpowercontrol.ath.cx), for anyone looking at the code.

Tested on real hardware with CachyOS and Bazzite.

EndeavourOS, Fedora, openSUSE Tumbleweed, Ubuntu 22.04 LTS and Linux Mint 22.3 have only been tested in virtual machines.

The project is developed with AI assistance, with all changes reviewed and tested by a human on real hardware and supported distributions.
