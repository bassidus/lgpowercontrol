# LGPowerControl
> **Note:** This branch supports Arch, Debian/Ubuntu and Fedora-based distributions.

Automatically turns your LG TV on and off with your computer — on boot, shutdown, and when the display sleeps or wakes.

On KDE Plasma it can also show a desktop notification shortly before the TV turns off (see `OFF_WARNING_SECONDS` in the config file). The warning is timed from Plasma's "Dim automatically" event, so that setting must be enabled in System Settings → Energy Saving.

## Installation

1. Clone the repository:
```bash
   git clone -b minimal https://github.com/bassidus/lgpowercontrol.git
   cd lgpowercontrol
```

2. Edit the configuration file and set your TV's IP address (the MAC address is auto-detected if left empty):
```bash
   nano lgpowercontrol.conf
```

3. Run the installer:
```bash
   sudo ./install.sh
```

## Uninstallation

```bash
sudo ./uninstall.sh
```
