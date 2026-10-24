"""mFruit App SDK: one input controller and one look for every mFruit OS app.

The source lives here, inside mFruit OS, which uses it itself. Apps carry a
copy named ``mfruit_sdk`` (``scripts/sdk-sync.sh <app dir>``), so they run
with or without mFruit OS installed. Every module uses relative imports only,
which is what lets the same files work under either package name.

    keys       USB / Bluetooth keyboards from /dev/input (no Pillow needed)
    gestures   one button -> tap, 2x, 3x, 4x, hold
    input      InputController: button + keyboard -> the mFruit OS actions
    status     WiFi level and battery for the status bar
    ui         theme, fonts, status bar, footer hints, lists, toast, RGB565
    radio      the shared LoRa radio: settings, Device ID, keys, contacts
               (keyring/crypto need the cryptography package)
    background ask mFruit OS to keep the app running after the user leaves
               it, optionally with the screen held at full brightness

See APP_DEVELOPMENT.md ("mFruit App SDK") and docs/APP_RULES.md.
"""

SDK_VERSION = "1.5.0"
