Sure! Here's a `README.md` tailored for GitHub:

````markdown
# LG TV Auto Power Script

This project automates the power control of your LG TV using Wake-on-LAN and [bscpylgtv](https://github.com/chros73/bscpylgtv). It integrates with systemd to power **on** your TV at boot and **off** at shutdown. There's also optional integration with KDE lock/unlock events.

> This script has only been tested on **EndeavourOS** and **CachyOS** (both Arch-based).

---

## Features

- Automatically powers **on** your LG TV at system boot
- Powers **off** the TV when shutting down
- Optional: reacts to KDE lock/unlock to turn the TV off/on
- Dependency checks and minimal setup interaction
- Uses a Python virtual environment for bscpylgtv

---

## Requirements

- Arch-based Linux system (e.g. EndeavourOS, CachyOS)
- LG TV on the same network
- Wake-on-LAN support enabled on the TV

---

## Installation

1. **Clone this repo**:
   ```bash
   git clone https://github.com/bassidus/arch-lgtv-power-control.git
   cd arch-lgtv-power-control
   chmod +x install.sh
````

2. **Edit `config.ini` before continuing**
   This file must contain your TV’s IP and MAC address:

   ```ini
   LGTV_IP=192.168.x.x
   LGTV_MAC=AA:BB:CC:DD:EE:FF
   ```

3. **Run the installer script**:

   ```bash
   sudo ./install.sh
   ```

   The script will:

   * Validate your config
   * Install dependencies
   * Set up systemd services
   * Optionally set up KDE Lock/Unlock Integration

---

## KDE Lock/Unlock Integration (Optional)

If you're using KDE, you can choose to install a listener script that turns the TV off when you lock the screen and on when you unlock it.

This is offered during the install process.

---

## Uninstallation

To remove the setup:

```bash
sudo systemctl disable lgtv-power-on-at-boot.service
sudo systemctl disable lgtv-power-off-at-shutdown.service
sudo rm /etc/systemd/system/lgtv-power-*.service
sudo rm -rf ~/.local/lgtv_control
```

And optionally remove the KDE autostart script if installed:

```bash
rm ~/.config/autostart/listen-for-lock-unlock-events.desktop
```

---

## Notes

* Only tested on **EndeavourOS** and **CachyOS**.
* Other Arch-based distros **may work**, but are not guaranteed.
* Make sure your TV supports and has Wake-on-LAN enabled.

---

## License

MIT — feel free to use, modify, or contribute.
