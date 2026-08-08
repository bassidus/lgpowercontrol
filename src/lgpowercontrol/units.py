from pathlib import Path
from typing import NamedTuple

# Unit text lives here rather than as files in the repo, so one table drives both writing them
# at install and deleting them at uninstall - the two can't drift apart.
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
USER_UNIT_DIR = Path("/etc/systemd/user")

BOOT_UNIT = """\
[Unit]
Description=Power on TV at boot after network is up
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=LGPC_SOURCE=boot
ExecStart={bin_dir}/lgpowercontrol ON

[Install]
WantedBy=multi-user.target
"""

# Conflicts=reboot.target is what keeps the TV on across a reboot; DefaultDependencies=no plus
# the Before= list is what gets this run while the network is still up.
SHUTDOWN_UNIT = """\
[Unit]
Description=Power off TV at shutdown (not reboot)
DefaultDependencies=no
After=network.target network-online.target
Before=poweroff.target halt.target shutdown.target
Conflicts=reboot.target

[Service]
Type=oneshot
Environment=LGPC_SOURCE=shutdown
ExecStart={bin_dir}/lgpowercontrol OFF
TimeoutStartSec=15

[Install]
WantedBy=poweroff.target halt.target
"""

MONITOR_UNIT = """\
[Unit]
Description=LGPowerControl DPMS state monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={bin_dir}/lgpowercontrol-monitor
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

SLEEP_UNIT = """\
[Unit]
Description=LGPowerControl suspend/resume listener (immutable-OS fallback)

[Service]
Type=simple
ExecStart={bin_dir}/lgpowercontrol-sleep-listener
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

NOTIFY_UNIT = """\
[Unit]
Description=LGPowerControl TV off warning notification
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart={bin_dir}/lgpowercontrol-notify
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""

class Unit(NamedTuple):
    path: Path  # full path to the unit file
    text: str   # file contents with bin_dir filled in

# short key -> (unit dir, template); the filename is derived as lgpowercontrol-<key>.service
UNIT_TEMPLATES = {
    "boot":     (SYSTEM_UNIT_DIR, BOOT_UNIT),
    "shutdown": (SYSTEM_UNIT_DIR, SHUTDOWN_UNIT),
    "monitor":  (SYSTEM_UNIT_DIR, MONITOR_UNIT),
    "sleep":    (SYSTEM_UNIT_DIR, SLEEP_UNIT),
    "notify":   (USER_UNIT_DIR,   NOTIFY_UNIT),
}

def build_units(bin_dir: Path) -> dict[str, Unit]:
    return {
        key: Unit(unit_dir / f"lgpowercontrol-{key}.service", template.format(bin_dir=bin_dir))
        for key, (unit_dir, template) in UNIT_TEMPLATES.items()
    }
