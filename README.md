## LGPowerControl

Automatically controls LG TV power when using your TV as a computer monitor. Turns the TV on at boot and off at shutdown. Optionally powers the TV on/off when locking/unlocking your screen (GNOME, KDE, Cinnamon).

## Requirements

* Linux (Debian, Ubuntu, Fedora, Arch, or similar)
* LG TV with WebOS and Wake-on-LAN enabled
* Python 3
* `wakeonlan` or `ether-wake` package

## Installation

1. Enable Wake-on-LAN on your TV:
   - Settings → Connection → Mobile Connection Management → TV On with Mobile → Turn On via Wi-Fi

2. Install:
   ```bash
   git clone https://github.com/bassidus/lgpowercontrol.git
   cd lgpowercontrol
   ./install.sh <TV_IP_ADDRESS>
   ```

3. Accept the authorization prompt on your TV when requested.

The installer sets up systemd services and optionally configures screen lock integration.

## Uninstallation

```bash
./uninstall.sh
```

## License

MIT
