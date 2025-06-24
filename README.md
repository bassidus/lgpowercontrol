# LGTVBtw

Heavily inspired by [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) and [LGBuddy](https://github.com/Faceless3882/LG_Buddy), this script is designed for easy installation on Arch-based systems.

It’s intended for setups where an LG TV is used as a computer monitor. Unlike regular monitors, TVs don’t respond to the computer’s power state changes.

This script works around that by automatically turning the TV **on** at boot and **off** at shutdown. It also includes optional support for powering the TV based on KDE’s screen lock/unlock events.

Especially useful for OLED users looking to reduce the risk of burn-in.

It relies on `wakeonlan` and [bscpylgtv](https://github.com/chros73/bscpylgtv), and integrates with `systemd` for seamless startup/shutdown behavior.

Tested on [EndeavourOS](https://endeavouros.com) with **KDE Plasma 6.4**, but should work on most Arch-based systems.

---

## Requirements

- Arch-based Linux system (e.g. Arch, EndeavourOS, CachyOS)
- LG TV with WebOS eg. CX, C1, C2, C3 and C4 OLED models but probably all recent models from around 2020 or so

---

## Installation
Power ON the TV and ensure it's connected to your local area network via Wi-Fi or cable.

Ensure that the TV can be woken via the network. For the CX line of displays this is accomplished by navigating to Settings (cog button on remote)->All Settings->Connection->Mobile Connection Management->TV On with Mobile, and then enable 'Turn On via Wi-Fi'. For C1, C2, C3 and C4 it's All Settings->General->Devices->External Devices->TV On With Mobile->Turn on via Wi-Fi. 

> NOTE! This step is needed regardless of using WiFi or a cable.

> TIP: Open the administrative interface of your router, and set a static DHCP lease for your TV, i.e. to ensure that your TV always have the same IP-address on your LAN.

1. **Clone this repo**:
```bash
git clone https://github.com/bassidus/lgtv-btw.git
cd lgtv-btw
```
2. **Edit `config` before continuing**
This file must contain your TV’s IP and optional MAC address:
```ini
LGTV_IP="192.168.x.x"
LGTV_MAC="AA:BB:CC:DD:EE:FF"
```
You can find the MAC address by running `arp -a 192.168.x.x` or by logging in to your router and find it there. If you don't know the MAC address, you can leave it blank (LGTV_MAC="") and the install script will attempt to retrieve it automatically, provided `net-tools` is installed. You may be prompted to install `net-tools` during execution.

3. **Run the installer script with sudo**:
```bash
sudo ./install
```
The script will:
* Install dependencies
* Set up systemd services
* Optionally set up KDE Lock/Unlock Integration
---

## KDE Lock/Unlock Integration (Optional)

If you're using KDE, you can choose to install a listener script that turns the TV off when you lock the screen and on when you unlock it.

This only works if you don't require a password when unlocking after inactivity. Otherwise, the screen stays off and you’ll have to enter your password blindly before the TV powers on.

> This **may** also work on GNOME but hasn't been tested yet.
---

## Uninstallation

Run the included `uninstall` with sudo:

```bash
sudo ./uninstall
```

---

## Notes

* Only tested on **EndeavourOS** with an **LG OLED42C35LA TV**.
* Other Arch-based distros and LG TV's **may work**, but are not guaranteed.
* Make sure your TV supports and has Wake-on-LAN enabled.

---

## License

MIT — feel free to use, modify, or contribute.
