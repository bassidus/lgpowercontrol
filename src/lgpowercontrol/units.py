from pathlib import Path

SYSTEM = Path("/etc/systemd/system")
USER = Path("/etc/systemd/user")


def build_units(bin_dir: Path) -> dict[str, tuple[Path, str]]:
    return {
        "lgpowercontrol-boot.service": (SYSTEM, f"""[Unit]
Description=Power on TV at boot after network is up
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=LGPC_SOURCE=boot
ExecStart={bin_dir}/lgpowercontrol ON

[Install]
WantedBy=multi-user.target
"""),
        "lgpowercontrol-shutdown.service": (SYSTEM, f"""[Unit]
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
WantedBy=poweroff.target halt.target"""),
        "lgpowercontrol-monitor.service": (SYSTEM, f"""[Unit]
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
"""),
        "lgpowercontrol-sleep.service": (SYSTEM, f"""[Unit]
Description=LGPowerControl suspend/resume listener (immutable-OS fallback)

[Service]
Type=simple
ExecStart={bin_dir}/lgpowercontrol-sleep-listener
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""),
        "lgpowercontrol-notify.service": (USER, f"""[Unit]
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
"""),
    }
