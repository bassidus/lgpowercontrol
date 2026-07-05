# LGPowerControl
> **Note:** This branch targets Arch Linux and Arch-based distributions only.

Automatically turns your LG TV on and off with your computer — on boot, shutdown, and when the display sleeps or wakes.

## Installation

1. Clone the repository:
```bash
   git clone -b arch https://github.com/bassidus/lgpowercontrol.git
   cd lgpowercontrol
```

2. Edit the configuration file and set your TV's IP and MAC address:
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