# LGTV Btw

Heavily inspired by [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) and [LGBuddy](https://github.com/Faceless3882/LG_Buddy) but is tailored to be easy to install on Arch-based systems.

This project automates the power control of your LG TV using Wake-on-LAN and [bscpylgtv](https://github.com/chros73/bscpylgtv). It integrates with systemd to power **on** your TV at boot and **off** at shutdown. There's also optional integration with KDE lock/unlock events.

This script has only been tested on [EndeavourOS](https://endeavouros.com) but should in theory work on all Arch-based systems.

---

## Requirements

- Arch-based Linux system (e.g. Arch, EndeavourOS, CachyOS)
- Power ON the TV and ensure it's connected to your local area network via Wi-Fi or cable.
- Ensure that the TV can be woken via the network. For the CX line of displays this is accomplished by navigating to Settings (cog button on remote)->All Settings->Connection->Mobile Connection Management->TV On with Mobile, and then enable 'Turn On via Wi-Fi'. For C1, C2, C3 and C4 it's All Settings->General->Devices->External Devices->TV On With Mobile->Turn on via Wi-Fi. NOTE! This step is needed regardless of using WiFi or a cable.
- Open the administrative interface of your router, and set a static DHCP lease for your TV, i.e. to ensure that your TV always have the same IP-address on your LAN.

---

## Installation

1. **Clone this repo**:
```bash
git clone https://github.com/bassidus/lgtv-btw.git
cd lgtv-btw
chmod +x install.sh
```

2. **Edit `config.ini` before continuing**

This file must contain your TV’s IP and optional MAC address:

```ini
LGTV_IP=192.168.x.x
LGTV_MAC=AA:BB:CC:DD:EE:FF
```
You can find the MAC address by running `arp -a 192.168.x.x` or by logging in to your router and find it there.

3. **Run the installer script with sudo**:

```bash
sudo ./install.sh
```

The script will:

* Install dependencies
* Set up systemd services
* Optionally set up KDE Lock/Unlock Integration

---

## KDE Lock/Unlock Integration (Optional)

If you're using KDE, you can choose to install a listener script that turns the TV off when you lock the screen and on when you unlock it.

This only works if you don't require a password when unlocking after inactivity. Otherwise, the screen stays off and you’ll have to enter your password blindly before the TV powers on.

---

## Uninstallation

Run the included `uninstall.sh` with:

```bash
chmod +x uninstall.sh
sudo ./uninstall.sh
```

---

## Notes

* Only tested on **EndeavourOS** with an LG OLED42C35LA TV.
* Other Arch-based distros and LG TV's **may work**, but are not guaranteed.
* Make sure your TV supports and has Wake-on-LAN enabled.

---

## License

MIT — feel free to use, modify, or contribute.
