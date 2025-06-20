# LG TV Power Control for Arch Linux

This project provides a script and systemd services to automatically power on and off an LG TV using Wake-on-LAN and the `bscpylgtv` tool on an Arch Linux system. The TV is powered on at system boot (after the network is up) and powered off during system shutdown or halt.

## Features
- Automatically powers on the LG TV at system boot using Wake-on-LAN.
- Powers off the LG TV during system shutdown or halt.
- Validates the TV's IP and MAC addresses.
- Installs dependencies (`wakeonlan`, `python-pip`, and optionally `net-tools`).
- Sets up a Python virtual environment for `bscpylgtv`.
- Supports non-interactive mode for automated setups.
- Provides a help menu with usage instructions.

## Requirements
- An Arch Linux-based system with `pacman` package manager.
- Root privileges (run the script with `sudo`).
- An LG TV that supports Wake-on-LAN and is compatible with the `bscpylgtv` tool.
- The following files in the same directory as the script:
  - `config.env`: Contains the TV's IP and MAC addresses.
  - `lgtv-power-on-at-boot.service`: Systemd service for powering on the TV.
  - `lgtv-power-off-at-shutdown.service`: Systemd service for powering off the TV.

## Installation
1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/lgtv-power-control.git
   cd lgtv-power-control
   ```

2. **Edit `config.env`**:
   Update `config.env` with your LG TV's IP and MAC addresses:
   ```env
   LGTV_IP="192.168.1.142"
   LGTV_MAC="20:28:bc:e6:3d:f6"
   ```
   If you don't know the MAC address, you can leave it blank (`LGTV_MAC=""`), and the script will attempt to retrieve it using `arp` (requires `net-tools`).

3. **Run the installation script**:
   ```bash
   sudo ./install.sh
   ```
   - If dependencies (`wakeonlan`, `python-pip`, or `net-tools`) are missing, you will be prompted to install them.
   - If `LGTV_IP` or `LGTV_MAC` is not set in `config.env`, you will be prompted to enter them.
   - The script validates the IP and MAC addresses and sets up systemd services.

4. **View help**:
   To see usage instructions:
   ```bash
   sudo ./install.sh --help
   ```

## Files
- `install.sh`: The main installation script that sets up dependencies, validates configurations, and enables systemd services.
- `config.env`: Configuration file for the TV's IP and MAC addresses.
- `lgtv-power-on-at-boot.service`: Systemd service to power on the TV at boot.
- `lgtv-power-off-at-shutdown.service`: Systemd service to power off the TV at shutdown.

## Notes
- Ensure your LG TV is configured to support Wake-on-LAN and is compatible with the `bscpylgtv` tool.
- The script performs a ping test to validate the TV's IP address. If the TV is off or blocks ICMP, a warning is displayed, but the script continues.
- If `net-tools` is not installed and the MAC address is not provided, you will be prompted to install `net-tools` or manually enter the MAC address.
- The `bscpylgtv` tool is installed in a Python virtual environment under `~/.local/lgtv_control`.

## Troubleshooting
- **Ping test fails**: If the TV is off or blocks ICMP, the script will warn but continue. Ensure the IP address is correct.
- **MAC address retrieval fails**: If `arp` cannot retrieve the MAC address, you will be prompted to enter it manually. Verify the TV is on the network.
- **Dependency installation fails**: Ensure you have an internet connection and that `pacman` repositories are accessible.
- **Systemd services not working**: Check the service status with:
  ```bash
  systemctl status lgtv-power-on-at-boot.service
  systemctl status lgtv-power-off-at-shutdown.service
  ```

## Contributing
Feel free to open issues or submit pull requests for improvements or bug fixes.

## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.