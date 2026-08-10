# Coherence checks on the installer's tables. Nothing here installs anything: uninstall() is the
# one function called, with every path it touches redirected into a scratch directory and every
# child process recorded instead of run, so the suite cannot disturb a real installation even if
# it is run as root.
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import install
from lgpowercontrol import units

SYSTEM_UNIT_KEYS = ("boot", "shutdown", "monitor", "sleep")


class UnitTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.units = units.build_units(Path("/opt/lgpowercontrol/bin"))

    def test_every_unit_is_named_after_its_key(self) -> None:
        for key, unit in self.units.items():
            with self.subTest(key=key):
                self.assertEqual(unit.path.name, f"lgpowercontrol-{key}.service")

    def test_the_notify_unit_is_the_only_user_unit(self) -> None:
        for key, unit in self.units.items():
            with self.subTest(key=key):
                expected = units.USER_UNIT_DIR if key == "notify" else units.SYSTEM_UNIT_DIR
                self.assertEqual(unit.path.parent, expected)

    def test_the_bin_directory_is_substituted_everywhere(self) -> None:
        for key, unit in self.units.items():
            with self.subTest(key=key):
                self.assertNotIn("{", unit.text)
                self.assertIn("/opt/lgpowercontrol/bin/", unit.text)

    # An ExecStart naming a wrapper the installer does not generate is a unit that fails at boot,
    # which is exactly the kind of drift a hand-maintained pair of tables produces.
    def test_every_execstart_names_a_generated_wrapper(self) -> None:
        for key, unit in self.units.items():
            with self.subTest(key=key):
                command = next(line for line in unit.text.splitlines() if line.startswith("ExecStart="))
                binary = command.removeprefix("ExecStart=").split()[0]
                self.assertIn(Path(binary).name, install.ENTRY_POINTS)

    # Conflicts=reboot.target is what keeps the TV on across a reboot, and DefaultDependencies=no
    # plus the Before= list is what gets this run while the network is still up. Both are easy to
    # lose in a reformat and neither fails loudly.
    def test_the_shutdown_unit_keeps_the_tv_on_across_a_reboot(self) -> None:
        text = self.units["shutdown"].text
        self.assertIn("Conflicts=reboot.target", text)
        self.assertIn("DefaultDependencies=no", text)
        self.assertIn("Before=poweroff.target halt.target shutdown.target", text)

    def test_the_boot_unit_waits_for_the_network(self) -> None:
        text = self.units["boot"].text
        self.assertIn("After=network-online.target", text)
        self.assertIn("Wants=network-online.target", text)

    # The source tag is what names the entry point in the journal, and what the POWER_OFF_AT_*
    # gating reads - the shutdown unit's OFF is only gateable because it carries one.
    def test_the_off_events_carry_their_source_tag(self) -> None:
        self.assertIn("Environment=LGPC_SOURCE=shutdown", self.units["shutdown"].text)
        self.assertIn("Environment=LGPC_SOURCE=boot", self.units["boot"].text)


class EntryPointTest(unittest.TestCase):
    # A rename on either side leaves a wrapper importing something that no longer exists, and it
    # only shows up at the next boot, suspend or resume.
    def test_every_wrapper_target_exists(self) -> None:
        for name, (module_name, func) in install.ENTRY_POINTS.items():
            with self.subTest(name=name):
                module = __import__(f"lgpowercontrol.{module_name}", fromlist=[func])
                self.assertTrue(callable(getattr(module, func)))

    def test_every_generated_wrapper_is_valid_python(self) -> None:
        for name, (module_name, func) in install.ENTRY_POINTS.items():
            with self.subTest(name=name):
                text = install.WRAPPER.format(
                    lib_dir=install.LIB_DIR, module=module_name, func=func
                )
                compile(text, name, "exec")

    # sys.exit() around the call, not a bare call: cli.main() returns the exit code its callers
    # read, and discarding it made every command look like a success. That broke authorize(),
    # which branches on STATUS's rc to tell "unpaired" from "unreachable".
    def test_the_wrapper_propagates_the_exit_code(self) -> None:
        self.assertIn("sys.exit({func}())", install.WRAPPER)

    # /usr/bin/python3 over 'env python3' so the interpreter cannot be picked from an inherited
    # PATH, and the library path is baked in rather than set as Environment= in the units:
    # NetworkManager and systemd-sleep exec the dispatcher and the hook with none of our
    # environment.
    def test_the_wrapper_pins_the_interpreter_and_the_library_path(self) -> None:
        self.assertTrue(install.WRAPPER.startswith("#!/usr/bin/python3"))
        self.assertIn('sys.path.insert(0, "{lib_dir}")', install.WRAPPER)

    def test_no_version_stamped_path_is_baked_in(self) -> None:
        # The acceptance criterion from dropping the venv: nothing under /opt may carry a Python
        # version, or a distribution upgrade leaves the install unable to start.
        self.assertNotIn("python3.", str(install.LIB_DIR))


class LinkTest(unittest.TestCase):
    # One list drives both install() and uninstall(), so the two cannot drift apart.
    def test_every_link_is_torn_down(self) -> None:
        for _, link in install.LINKS:
            self.assertIn(link, install.TEARDOWN_PATHS)

    def test_the_dispatcher_shim_is_torn_down_too(self) -> None:
        self.assertIn(install.DISPATCHER_SHIM, install.TEARDOWN_PATHS)

    # Load-bearing on every SELinux distro: dispatcher.d/ must hold a real file, because SELinux
    # labels an exec by the target and only a file labelled NetworkManager_dispatcher_script_t
    # transitions into the permissive domain. A symlink into /opt leaves the script confined,
    # where logind, /run and systemd-run are all denied - silently, since those denials are
    # dontaudit'ed. Do not "simplify" this into a symlink or a semanage call with bin_t.
    def test_the_dispatcher_entry_is_a_real_file_not_a_link(self) -> None:
        self.assertNotIn(install.DISPATCHER_SHIM, [link for _, link in install.LINKS])
        self.assertTrue(install.DISPATCHER_SHIM_TEXT.startswith("#!/bin/sh"))
        self.assertIn("lgpowercontrol-nm-dispatcher", install.DISPATCHER_SHIM_TEXT)

    # pre-down.d/ keeps a relative symlink to the shim, which inherits the right label through it.
    def test_the_pre_down_link_stays_relative(self) -> None:
        target, link = install.PREDOWN_LINK
        self.assertEqual(target, "../90-lgpowercontrol")
        self.assertEqual(link.name, install.DISPATCHER_SHIM.name)


class UninstallTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.commands: list[list[str]] = []

    def _record(self, cmd, **kwargs):
        self.commands.append([str(part) for part in cmd])
        return mock.Mock(returncode=0)

    def run_uninstall(self, teardown_paths=None):
        scratch_units = {
            key: units.Unit(self.tmp / unit.path.name, unit.text)
            for key, unit in install.UNITS.items()
        }
        with (
            mock.patch.object(install, "require_root"),
            mock.patch.object(install, "INSTALL_DIR", self.tmp / "opt"),
            mock.patch.object(install, "UNITS", scratch_units),
            mock.patch.object(install, "TEARDOWN_PATHS",
                              [self.tmp / "link"] if teardown_paths is None else teardown_paths),
            mock.patch.object(install.shutil, "rmtree") as rmtree,
            mock.patch.object(install.os, "environ", {}),
            mock.patch.object(install.subprocess, "run", side_effect=self._record),
            mock.patch("builtins.print"),
        ):
            install.uninstall()
        return rmtree

    def systemctl_disables(self) -> list[list[str]]:
        return [cmd for cmd in self.commands if cmd[:2] == ["systemctl", "disable"]]

    # One call per unit, never one call listing all four. systemd-sleep's listener unit only
    # exists on immutable /usr, and `systemctl disable --now A B missing` fails *atomically* -
    # rc 1, nothing disabled, nothing stopped, with the error hidden by stderr=DEVNULL. That left
    # dangling target.wants symlinks and a monitor service still running the pre-update code on
    # every ordinary distro. Shipped as a bug once; this is the tripwire.
    def test_each_unit_is_disabled_in_its_own_call(self) -> None:
        self.run_uninstall()
        for command in self.systemctl_disables():
            with self.subTest(command=command):
                self.assertEqual(len(command), 4, "one unit per call, or a missing one takes the rest down")

    def test_every_system_unit_is_disabled(self) -> None:
        self.run_uninstall()
        disabled = {command[-1] for command in self.systemctl_disables()}
        self.assertEqual(disabled, {f"lgpowercontrol-{key}.service" for key in SYSTEM_UNIT_KEYS})

    def test_the_user_unit_is_disabled_globally(self) -> None:
        self.run_uninstall()
        self.assertIn(
            ["systemctl", "--global", "disable", "lgpowercontrol-notify.service"], self.commands
        )

    def test_the_install_directory_is_removed(self) -> None:
        rmtree = self.run_uninstall()
        self.assertEqual(rmtree.call_args.args[0], self.tmp / "opt")

    def test_systemd_is_reloaded_afterwards(self) -> None:
        self.run_uninstall()
        self.assertEqual(self.commands[-1], ["systemctl", "daemon-reload"])

    # Read-only /usr (Bazzite): unlinking the sleep hook raises EROFS, and it raises it even when
    # the file was never there, so missing_ok= does not cover it. Unhandled, it took down both the
    # uninstall and the install that calls this first - the installer died before writing anything.
    def test_a_read_only_filesystem_does_not_take_the_uninstall_down(self) -> None:
        class ReadOnlyPath:  # the sleep hook under /usr on an ostree system
            def unlink(self, missing_ok: bool = False):
                raise OSError(30, "Read-only file system")

        self.run_uninstall(teardown_paths=[ReadOnlyPath()])
        self.assertEqual(self.commands[-1], ["systemctl", "daemon-reload"])


class DisabledUnitListTest(unittest.TestCase):
    # The enable/disable lists in install.py are hand-written on purpose rather than derived from
    # UNITS - not every unit is enabled the same way, and the sleep listener is conditional. That
    # makes them exactly the kind of list that drifts, so it is checked here instead.
    def test_the_unit_table_holds_the_four_system_units_and_the_user_one(self) -> None:
        self.assertEqual(set(install.UNITS), set(SYSTEM_UNIT_KEYS) | {"notify"})


if __name__ == "__main__":
    unittest.main()
