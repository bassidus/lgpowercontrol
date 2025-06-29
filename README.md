# LGPowerControl (Formerly LGTVBtw)

Heavily inspired by [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) and [LGBuddy](https://github.com/Faceless3882/LG_Buddy), this script is designed for easy installation on various Linux distributions, including Debian-based (e.g., Debian, Ubuntu, Linux Mint), Fedora-based (e.g., Fedora, CentOS), and Arch-based (e.g., Arch, EndeavourOS, CachyOS, Manjaro) systems.

It’s intended for setups where an LG TV is used as a computer monitor. Unlike regular monitors, TVs don’t respond to the computer’s power state changes.

This script works around that by automatically turning the TV **on** at boot and **off** at shutdown. It also includes optional support for powering the TV based on KDE’s screen lock/unlock events.

Especially useful for OLED users looking to reduce the risk of burn-in.

It relies on `wakeonlan` (or `ether-wake` for Fedora-based systems) and [bscpylgtv](https://github.com/chros73/bscpylgtv), and integrates with `systemd` for seamless startup/shutdown behavior.

It should work on most Arch-based, Debian-based and Fedora-based systems.

---

## Requirements

- Linux system (e.g., Debian, Ubuntu, Linux Mint, Fedora, CentOS, Arch, EndeavourOS, CachyOS, Manjaro)
- LG TV with WebOS (e.g., CX, C1, C2, C3, and C4 OLED models, but likely compatible with most models from around 2020 onward)
- Python 3 installed
- `wakeonlan` (Debian-based, Arch-based) or `ether-wake` (Fedora-based) package

---

## Installation

Power ON the TV and ensure it's connected to your local area network via Wi-Fi or cable.

Ensure that the TV can be woken via the network.  

For the CX line of displays, navigate to Settings (cog button on remote) → All Settings → Connection → Mobile Connection Management → TV On with Mobile, and enable 'Turn On via Wi-Fi'.  

For C1, C2, C3, and C4, it’s All Settings → General → Devices → External Devices → TV On With Mobile → Turn on via Wi-Fi.

> **NOTE!** This step is required regardless of using Wi-Fi or a cable.

> **TIP:** Open your router’s administrative interface and set a static DHCP lease for your TV to ensure it always has the same IP address on your LAN.

1. **Clone this repo**:
```bash
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol
```

2. **Run the installer script**:
```bash
./install.sh <TV_IP_ADDRESS>
# Example:
# ./install.sh 192.168.1.100
```

The script will:
* Validate the provided TV IP address
* Check for required dependencies (`iproute2`, `python3`, and `wakeonlan` or `ether-wake`)
* Verify network connectivity to the TV
* Retrieve the TV’s MAC address automatically using `ip neigh`
* Install `bscpylgtv` in a virtual environment.
* Set up `systemd` services for TV power-on at boot and power-off at shutdown.
* Optionally prompt to set up KDE lock/unlock integration (if applicable)

If the MAC address cannot be retrieved, run `ip neigh show <TV_IP_ADDRESS>` manually and see if you can figure it out.

---

## KDE Lock/Unlock Integration (Optional)

If you're using KDE Plasma (available on most distributions), the installer can set up a listener script that turns the TV off when you lock the screen and on when you unlock it. During installation, you’ll be prompted to enable this feature.

**Note:** This only works if you don’t require a password when unlocking after inactivity. Otherwise, the screen stays off, and you’ll need to enter your password blindly before the TV powers on. 
 
For Fedora-based systems using `ether-wake`, you may also be prompted to configure a `sudoers` rule to allow `ether-wake` to run without a password.

> **Note:** This feature is only for KDE Plasma. Contributions for other desktop environments are welcome!

---

## Uninstallation

Run the included `uninstall.sh` script:

```bash
./uninstall.sh
```

This will remove the `systemd` services, virtual environment, and optional KDE integration files and sudoers rule.

---

## Notes

* Tested on **EndeavourOS** and **Fedora 42** with an **LG OLED42C35LA TV** and **KDE Plasma 6.4**.
* Should work on Debian-based (Debian, Ubuntu, Linux Mint), Fedora-based (Fedora, CentOS), and Arch-based (Arch, EndeavourOS, CachyOS, Manjaro) distributions with proper dependency installation.
* Other LG TVs with WebOS and Wake-on-LAN support **may work**, but compatibility is not guaranteed.
* Ensure your TV has Wake-on-LAN enabled in its settings.

---

## License

MIT — feel free to use, modify, or contribute.