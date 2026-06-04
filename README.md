# LGPowerControl

Automatically turns your LG TV on and off with your computer — on boot, shutdown, and when the display sleeps or wakes.

> **Note:** This branch targets Arch Linux and Arch-based distributions only.

## Installation

1. Clone the repository:
```bash
   git clone -b simple https://github.com/bassidus/lgpowercontrol.git
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

The installer will set up a Python virtual environment, install dependencies, register systemd services, and walk you through authorizing the connection to your TV.