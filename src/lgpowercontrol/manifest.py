# The registration manifest sent when pairing with the TV, replacing the one shipped in
# bscpylgtv's manifest.py.
#
# Every SSAP client has inherited the same manifest since 2014: appId "com.lge.test" plus a
# `signatures` block carrying LG's old test-signing certificate, and a `signed` block whose
# permissions the TV grants on that signature alone, without asking the user. webOS 26 (firmware
# 11.2.0) blacklists that certificate: the TV answers the registration with
#
#     {"type": "error", "id": "register_0",
#      "error": "403 Pairing rejected: blacklisted certificate detected"}
#
# in well under a tenth of a second, before it draws the pairing dialog - so no dialog ever
# appears, and the rejection comes before the TV even looks at a stored client key. An
# installation that paired years ago breaks the moment the TV takes the update. See
# https://github.com/bassidus/lgpowercontrol/issues/16 and, for the protocol captures,
# https://github.com/home-assistant/core/issues/172703.
#
# The way out is to send no signature at all. An unsigned manifest is accepted on every firmware,
# webOS 26 included: the TV shows the dialog, the user accepts, and it grants exactly the
# permissions listed in the clear below. Home Assistant took this route in aiowebostv 0.9.2 across
# its whole install base and existing keys kept working - the key is honoured again as soon as the
# registration carrying it is not rejected outright.
#
# What is lost is the tier the signature bought: permissions that were declared only inside the
# `signed` block are not granted, no matter where they are declared instead (measured on a G5 in
# the issue above - moving them into the list below does not bring them back). They are kept here
# anyway, matching aiowebostv, so that a future firmware that grants them has nothing to object
# to. None of them matter to us: this package asks the TV for its power state, its foreground app,
# the screen on and off, an HDMI input and a power off, and each of those is covered by
# READ_POWER_STATE, READ_APP_STATUS, CONTROL_TV_SCREEN, CONTROL_INPUT_TV and CONTROL_POWER, all of
# which are granted by the dialog. The one thing Home Assistant lost with this change,
# getCurrentSWInformation, we never call - tv_cmd() passes states=None precisely so that the
# client fetches nothing of its own accord.
#
# deviceName is left at bscpylgtv's value on purpose, although the TV shows it in the pairing
# dialog and under Settings -> Connections -> External Devices. The only difference between this
# manifest and the one it replaces is meant to be the missing signature, so that a TV that still
# refuses to pair has been told one thing less. Renaming it is a cosmetic change to make once this
# is known to work against real hardware, not in the same commit.
MANIFEST = {
    "appVersion": "1.1",
    "manifestVersion": 1,
    "deviceName": "bscpylgtv",
    "permissions": [
        "APP_TO_APP",
        "CLOSE",
        "CONTROL_AUDIO",
        "CONTROL_DISPLAY",
        "CONTROL_INPUT_JOYSTICK",
        "CONTROL_INPUT_MEDIA_PLAYBACK",
        "CONTROL_INPUT_MEDIA_RECORDING",
        "CONTROL_INPUT_TEXT",
        "CONTROL_INPUT_TV",
        "CONTROL_MOUSE_AND_KEYBOARD",
        "CONTROL_POWER",
        "CONTROL_TV_SCREEN",
        "LAUNCH",
        "LAUNCH_WEBAPP",
        "READ_APP_STATUS",
        "READ_COUNTRY_INFO",
        "READ_CURRENT_CHANNEL",
        "READ_INPUT_DEVICE_LIST",
        "READ_INSTALLED_APPS",
        "READ_LGE_SDX",
        "READ_LGE_TV_INPUT_EVENTS",
        "READ_NETWORK_STATE",
        "READ_NOTIFICATIONS",
        "READ_POWER_STATE",
        "READ_RUNNING_APPS",
        "READ_SETTINGS",
        "READ_TV_CHANNEL_LIST",
        "READ_TV_CURRENT_TIME",
        "READ_UPDATE_INFO",
        "SEARCH",
        "TEST_OPEN",
        "TEST_PROTECTED",
        "TEST_SECURE",
        "UPDATE_FROM_REMOTE_APP",
        "WRITE_NOTIFICATION_ALERT",
        "WRITE_NOTIFICATION_TOAST",
        "WRITE_SETTINGS",
    ],
}
