## LGPowerControl

This script is designed for easy installation on various Linux distributions, including **Debian-based** (e.g., Ubuntu, Mint), **Fedora-based**, and **Arch-based** (e.g., EndeavourOS, Manjaro) systems.

It's intended for setups where an LG TV is used as a computer monitor. Unlike regular monitors, TVs don't respond naturally to the computer's power state changes. This script bridges that gap by automatically turning the TV **on at boot** and **off at shutdown**, and blanking/unblanking the TV screen when the computer display sleeps or wakes.

All behaviour is configurable via a single config file at `/opt/lgpowercontrol/lgpowercontrol.conf`.

Especially useful for OLED users looking to reduce the risk of burn-in.

---

## Requirements

* **Linux System:** Compatible with most modern distributions using `systemd`.
* **LG TV with WebOS:** (e.g., CX, C1, C2, C3, and C4 OLED models).
* **Network Tools:** `iproute2` (for the `ip` command), and `wakeonlan` (Debian/Arch) or `net-tools` (for `ether-wake` on Fedora).
* **Python 3:** Including `python3-venv` (specifically required on Debian-based systems).

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

# Run the installer (optionally pass TV IP as argument)
./install.sh [TV_IP_ADDRESS]
```

If you omit the IP address, the installer will prompt you for it.

### What the script does:

* **Dependency Check:** Automatically detects your package manager (`apt`, `dnf`, or `pacman`) to provide installation hints.
* **IP & MAC Validation:** Pings the TV and retrieves the MAC address automatically using the ARP table (`ip neigh`).
* **System-wide Installation:** Installs `bscpylgtv` into a dedicated virtual environment at `/opt/lgpowercontrol/bscpylgtv`, accessible to all users and system services.
* **HDMI Input Selection:** Prompts you to choose an HDMI port (1–5) so the TV switches to the correct input automatically when powered on.
* **Power Mode Selection:** Lets you choose between full power on/off or screen on/off for both boot/shutdown and screen sleep/wake behavior.
* **Systemd Integration:** Installs `lgpowercontrol-boot.service`, `lgpowercontrol-shutdown.service`, and `lgpowercontrol-monitor.service` to handle power states at boot, shutdown, and screen sleep/wake.
* **Config File:** Creates `/opt/lgpowercontrol/lgpowercontrol.conf` with hardware values and configurable behavior settings.
* **TV Authorization:** Triggers a one-time pairing request on your TV screen. **You must click "Accept" on the TV remote.**

---

## Configuration

All settings are in `/opt/lgpowercontrol/lgpowercontrol.conf`. After editing, restart the monitor service to apply changes:

```bash
sudo systemctl restart lgpowercontrol-monitor.service
```

Boot and shutdown services read the config each time they run — no restart needed.

### Hardware (updated automatically on reinstall)

| Variable | Description |
|---|---|
| `LGTV_IP` | TV IP address |
| `LGTV_MAC` | TV MAC address |
| `WOL_CMD` | Wake-on-LAN command |
| `HDMI_INPUT` | HDMI port to switch to on power-on (empty to skip) |

### Behavior

| Variable | Options | Default | Description |
|---|---|---|---|
| `BOOT_SHUTDOWN_MODE` | `power`, `screen` | `power` | `power`: WoL on at boot, power off at shutdown. `screen`: turn screen on/off (TV stays in standby) |
| `MONITOR_MODE` | `power`, `screen` | `power` | `power`: full power off/on when display sleeps/wakes. `screen`: turn TV screen off/on instead |

---

## Screen State Monitor

The screen state monitor is installed automatically as a systemd service (`lgpowercontrol-monitor.service`).

* **System service:** Runs as a systemd system service (`lgpowercontrol-monitor.service`), independently of which user is logged in. Works correctly in multi-user setups.
* **DE-agnostic:** Works with **GNOME**, **KDE Plasma**, **Cinnamon**, and any other systemd-based desktop, on both **X11** and **Wayland**.
* **Detection method:** Polls DPMS state directly from the kernel DRM subsystem (`/sys/class/drm/`) every 2 seconds. This works reliably on Wayland where KDE's *Screen Energy Saving* bypasses logind and D-Bus entirely. When DRM sysfs is unavailable, it falls back to logind's `IdleHint` across all active graphical sessions.
* **Behavior:** By default, powers the TV off/on when the display blanks or wakes. This can be changed to `turn_screen_off` / `turn_screen_on` via the config file.
* **No D-Bus dependency:** The previous version used a D-Bus session monitor, which was prone to firing multiple signals during lock→dim→screen-off cycles, causing the TV to unexpectedly turn back on. The current approach reads hardware state directly, avoiding that race condition entirely.

---

## Uninstallation

To remove all files, services, and configurations:

```bash
./uninstall.sh
```

This safely stops and removes all systemd services (including any legacy services from older versions), and the `/opt/lgpowercontrol` directory.

---

## Notes

* **Logs:** View all activity (power events, WoL commands, errors) by running:
  `journalctl -t lgpowercontrol`
* **Testing:** Verified on **EndeavourOS** and **Fedora 43** with an **LG OLED42C35LA** and **KDE Plasma 6.6.3**.

---

## Credits

* [bscpylgtv](https://github.com/chros73/bscpylgtv) — the Python library used to communicate with LG WebOS TVs.
* [LGTVCompanion](https://github.com/JPersson77/LGTVCompanion) — inspiration (Windows).
* [LGBuddy](https://github.com/Faceless3882/LG_Buddy) — inspiration (Linux).
