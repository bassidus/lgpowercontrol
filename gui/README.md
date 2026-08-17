# LGPowerControl Settings

A small Qt 6 window over `/opt/lgpowercontrol/lgpowercontrol.conf`: sliders, checkboxes and three
text fields instead of an editor and the memory of what each key means.

It is a separate program in every sense. It links nothing from `src/`, `install.py` knows nothing
about it, and it treats the conf as what it is - a text file whose comments are worth keeping. The
only thing it borrows from the rest of the repo is the *meaning* of each key, which it reads the
way `src/lgpowercontrol` reads it rather than the way the file looks:

| Setting | What the file says | What the code does with it |
|---|---|---|
| HDMI input | `HDMI_INPUT=""` | empty, non-numeric or `0` all mean "never switch input" |
| Another device shares this TV | `SHARED_TV="0"` | only the exact string `1` enables it, and it needs an HDMI input |
| Turn the TV off when … | `POWER_OFF_AT_SUSPEND="1"` | only an explicit `0` disables it; a typo leaves it on |
| Check every | `NOTIFY_POLL_SECONDS="5"` | a missing or `0` value falls back to 5 s, the same as the shipped one |

The window shows the second column. That is the whole reason it exists.

The two idle-warning settings are greyed out where the warning cannot happen. `notify.py` exits at
startup without `kscreen-doctor` and `kreadconfig6`, so those decide whether it can work at all -
but on a machine with Plasma and GNOME both installed they are on `PATH` in the GNOME session too,
where nothing dims through KScreen. The session is therefore checked as well, and only when
`XDG_CURRENT_DESKTOP` says nothing at all - started from a tty - are the binaries trusted alone.
The values stay visible and are written back untouched, so a conf edited from GNOME still works
when Plasma reads it.

The address and the MAC address open behind a padlock: they are set once and never touched again,
and they are the two fields where a stray keystroke leaves lgpowercontrol talking to nothing. Click
the padlock to edit one. *Save* stays greyed out until something actually differs from the file -
change a value and change it back and it greys out again. *Restore* reads the file in again,
dropping whatever has been changed but not saved, and *Defaults* fills the form with the values the
shipped template carries. Neither writes anything until *Save* is pressed.

## Build

Needs CMake and the Qt 6 Widgets development package. Qt 6 itself is already installed on any KDE
Plasma desktop; on GNOME the runtime comes with it as a dependency.

    Arch/CachyOS   sudo pacman -S --needed cmake qt6-base
    Debian/Ubuntu  sudo apt install cmake qt6-base-dev libgl-dev
    Fedora         sudo dnf install cmake qt6-qtbase-devel
    openSUSE       sudo zypper install cmake qt6-base-devel

`libgl-dev` is on the Debian line because `qt6-base-dev` does not pull it in: measured on Ubuntu
22.04, where CMake otherwise stops at "Qt6Gui could not be found because dependency WrapOpenGL
could not be found", which does not name the missing package.

On an image-based system such as Bazzite there is no compiler and `/usr` is read-only, so nothing
here can be built. `install.sh` installs the copy in `prebuilt/` instead - see below.

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
./build/lgpowercontrol-gui
```

## Install

```bash
./install.sh              # builds, then installs to ~/.local - no root
./install.sh --uninstall
```

That adds `lgpowercontrol-gui` and a menu entry called "LGPowerControl Settings". It does not touch
LGPowerControl itself: `./install.py --uninstall` in the repo root is still what removes that.

### Where it cannot be built

`install.sh` builds from source when it can and installs `prebuilt/lgpowercontrol-gui` when it
cannot. The choice is made by running the CMake configure step and seeing whether it works - not by
asking which distribution this is - so every image-based variant is covered without naming any of
them, and so is an ordinary machine that happens to be missing the Qt development package.

Three things are checked before that copy is trusted, because it is the one part of this that was
built somewhere else:

| Check | Why |
|---|---|
| `uname -m` is `x86_64` | it is one architecture, and a wrong one should say so rather than be copied |
| `sha256sum -c prebuilt/SOURCE.sha256` | a binary older than the sources beside it would behave like last month's code while looking current |
| `prebuilt/lgpowercontrol-gui --check` | version symbols are resolved when the binary loads, so this exits non-zero on a machine whose Qt is older than the build |

Any of them failing is an error naming what to do about it, never a silent fall-through.

To rebuild the shipped copy after changing the sources, and to commit both files it writes:

```bash
./prebuilt/build.sh          # needs podman or docker
```

It builds in the container described by `prebuilt/Containerfile`, which pins Ubuntu 22.04 - Qt 6.2.4
and glibc 2.35, the oldest of both that this program supports, and the same Qt floor
`CMakeLists.txt` declares. That direction is the only one that works: measured on Bazzite 44, whose
host Qt is 6.10.3, a binary from a Fedora 44 toolbox (Qt 6.11.1) refuses to start with
`libQt6Core.so.6: version 'Qt_6.11' not found`, while the container's binary starts and still gets
Breeze. The shipped copy asks for no more than `Qt_6.2` and `GLIBC_2.34`, which `objdump -p` will
confirm.

Nothing is written into the work tree by that script: the sources go into the container read-only
and the binary comes back on stdout, which also means the result does not depend on whether rootless
podman or docker ran it.

**No sudo, and that is the point.** The conf this window edits is handed to one user by
`install.py` - `0644`, owned by whoever ran the installer - so a system-wide copy would give every
other local user a window that can only read. `PREFIX=/usr/local ./install.sh` still works and asks
for sudo, but only because that destination needs it.

`$PREFIX/bin` has to be on `PATH`. The installer says so if it is not, and it matters for more than
convenience - see the note about the app id below.

## What it will not do

* **Create the conf.** If `/opt/lgpowercontrol/lgpowercontrol.conf` is missing it says so and exits.
  A file written here would have no services reading it and no pairing key beside it.
* **Add or remove keys.** Only values are rewritten, in place, with every comment and the trailing
  legend on each line left alone. A key missing from the file is reported, never appended.
* **Edit a conf it does not own.** If the installer ran from a root login, `SUDO_USER` was unset and
  the file stayed root-owned; the window then fills in but is read-only, and says why.
* **Ask for a password it does not need.** See below.

## What has to be restarted, and what does not

Saving applies the change, so there is no restart button and nothing to remember afterwards:

| Changed | Held by | What saving does |
|---|---|---|
| address, MAC, HDMI input, shared TV, the two power-off boxes | nobody | nothing to do - `cli.py` re-reads the conf in a fresh process on every invocation, including the ones the monitor spawns |
| warn ahead by, check every | `lgpowercontrol-notify.service` | restarts it; it runs as you, so nothing is asked |
| logging | both long-running services, via `Logger()` | restarts the user service, and the monitor through `pkexec`, which asks for authorisation |

So a password is asked for one setting - logging - and never for an ordinary change, because the
monitor is the only service running as root that holds anything from this file.

A unit that is not running is skipped rather than started: it reads the file on its own when it
next starts. A restart that worked is not announced; only a failure reaches the status line, with
the command to finish the job by hand.

The `boot`, `shutdown` and `sleep` units are never touched. They are oneshots, so "restarting" one
would *run* it, which means turning the TV on or off.

## Looks

No palette, font or stylesheet is set anywhere in the code. That is deliberate: Qt then uses Breeze
under Plasma, and Fusion plus the freedesktop appearance portal (light/dark and the system font)
under GNOME. Hardcoding any of it would look intentional on one desktop and broken on the other.
Confirmed on Ubuntu 22.04: `QApplication::style()` there is `fusion`, with the desktop's own Yaru
padlock in the two locked fields.

The padlock is the one icon the window cannot do without, so it is drawn from the palette's text
colour when the icon theme has none - which is what happens when Qt cannot tell what desktop it is
on and falls back to `hicolor`. Removing the padlock instead would leave the fields it protects
quietly editable.

The program names its `.desktop` file - which is what gives the window its icon in the task manager
- only once that file is installed *and* the running binary is the one it points at. `xdg-desktop-portal`
checks both, and a build-tree copy that claims the name anyway makes it warn on every start:

    qt.qpa.services: Failed to register with host portal ... App info not found for 'lgpowercontrol-gui'

So a copy run straight from `build/` is anonymous and quiet, and an installed one is not.
