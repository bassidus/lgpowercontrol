## LGPowerControl

Heavily inspired by [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) and [LGBuddy](https://github.com/Faceless3882/LG_Buddy), this script is designed for easy installation on various Linux distributions, including **Debian-based** (e.g., Ubuntu, Mint), **Fedora-based**, and **Arch-based** (e.g., EndeavourOS, Manjaro) systems.

It’s intended for setups where an LG TV is used as a computer monitor. Unlike regular monitors, TVs don’t respond naturally to the computer’s power state changes. This script bridges that gap by automatically turning the TV **on at boot** and **off at shutdown**.

It also includes optional support for powering the TV based on **GNOME**, **KDE**, or **Cinnamon** screen lock/unlock events. All background actions are logged to the system journal using `logger`.

Especially useful for OLED users looking to reduce the risk of burn-in.

---

## Requirements

* **Linux System:** Compatible with most modern distributions using `systemd`.
* **LG TV with WebOS:** (e.g., CX, C1, C2, C3, and C4 OLED models).
* **Network Tools:** * `iproute2` (for the `ip` command).
* `wakeonlan` (Debian/Arch) or `net-tools` (for `ether-wake` on Fedora).


* **Python 3:** Including `python3-venv` (specifically required on Debian-based systems).
* **User Privileges:** The script must be run as a **regular user** (not root/sudo), though it will prompt for sudo access when installing system services.

---

## Installation

### 1. Prepare the TV

1. **Power ON** the TV and connect it to your network (Wi-Fi or Ethernet).
2. **Enable Wake-on-LAN:**
* **CX models:** Settings → All Settings → Connection → Mobile Connection Management → **TV On with Mobile**.
* **C1-C4 models:** All Settings → General → Devices → External Devices → TV On With Mobile → **Turn on via Wi-Fi**.
* *Note: This must be enabled even if you use a wired Ethernet connection.*


3. **Static IP:** It is highly recommended to set a static DHCP lease for your TV in your router settings.

### 2. Run the Installer

```bash
# Clone the repository
git clone https://github.com/bassidus/lgpowercontrol.git
cd lgpowercontrol

# Run the installer with your TV's IP address
./install.sh <TV_IP_ADDRESS>

```

### What the script does:

* **Dependency Check:** Automatically detects your package manager (`apt`, `dnf`, or `pacman`) to provide installation hints.
* **IP & MAC Validation:** Pings the TV and retrieves the MAC address automatically using the ARP table (`ip neigh`).
* **Virtual Environment:** Installs `bscpylgtv` into a dedicated local environment at `~/.local/lgpowercontrol/bscpylgtv`.
* **HDMI Input Selection:** Prompts you to choose an HDMI port (1-5) so the TV switches to the correct input automatically when powered on.
* **Systemd Integration:** Installs `lgpowercontrol-boot.service` and `lgpowercontrol-shutdown.service` to handle power states.
* **TV Authorization:** Triggers a one-time pairing request on your TV screen. **You must click "Accept" on the TV remote.**

---

## DBus Screen Lock Integration (Optional)

The installer can set up a background listener that monitors screen lock/unlock events.

* **Auto-detection:** Supports **GNOME**, **KDE Plasma**, and **Cinnamon**.
* **Behavior:** Powers the TV off when you lock the screen and on when you unlock it.
* **Fedora/ether-wake Support:** If using `ether-wake` (common on Fedora), the script offers to create a `sudoers` rule in `/etc/sudoers.d/` so the TV can be woken up without requiring a password prompt during the process.

> [!IMPORTANT]
> If your lock screen requires a password, the TV will stay off until you have finished typing your password (blindly) and pressed Enter.

---

## Uninstallation

To remove all files, services, and configurations:

```bash
./uninstall.sh

```

This safely removes the virtual environment, systemd services, the DBus listener, and any created `sudoers` rules.

---

## Notes

* **Logs:** View all activity (power events, WoL commands, errors) by running:
`journalctl -t lgpowercontrol`
* **Testing:** Verified on **EndeavourOS** and **Fedora 42** with an **LG OLED42C35LA** and **KDE Plasma 6.4**.
* **Root Warning:** Do **not** run the `install.sh` script with `sudo`. The script is designed to handle elevated permissions only when necessary (e.g., for systemd services).
