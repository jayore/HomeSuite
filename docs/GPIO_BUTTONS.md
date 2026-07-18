# Additional GPIO Buttons

Home Suite can map auxiliary Raspberry Pi GPIO buttons to ordinary command
phrases. These controls are separate from push-to-talk: pressing one executes a
configured command but does not open or close microphone capture.

The management console's **Physical controls** page is the recommended setup
path. Its command-button editor presents one row per physical button with its
button ID, BCM pin, and commands for single press, double press, and long press.
The same settings can still be maintained directly in `local_prefs.py`.

## Basic Configuration

Enable command buttons and map each button number to a BCM GPIO pin:

```python
PHYSICAL_BUTTONS_ENABLED = True
PHYSICAL_BUTTON_ACTIVE_LOW = True
PHYSICAL_BUTTON_PULL_UP = True

PHYSICAL_BUTTON_PINS = {
    1: 2,
    2: 3,
}
```

The common wiring pattern is GPIO to momentary button to ground. With that
wiring, keep both `PHYSICAL_BUTTON_ACTIVE_LOW` and
`PHYSICAL_BUTTON_PULL_UP` enabled. BCM numbers are not physical header-pin
numbers.

Every configured GPIO pin must be unique. Do not reuse the pin assigned to
`PTT_GPIO_PIN` when both PTT and command buttons are enabled.

## Map Actions

`PHYSICAL_BUTTON_ACTIONS` maps each button number to gesture names and Home
Suite command phrases:

```python
PHYSICAL_BUTTON_ACTIONS = {
    1: {
        "press": "turn on the office light",
        "double_press": "set the office light to blue",
        "long_press": "turn off the office light",
    },
    2: {
        "press": "toggle play pause",
    },
}
```

Supported gestures are `press`, `double_press`, and `long_press`. An action can
be one command string, a list of command strings, or an advanced action object
with `command` or `commands`. Commands enter the same deterministic routing
pipeline used by text, voice, and companion clients.

In the console, enter one command per line when one gesture should run several
commands in sequence. Enable **Repeat while held** on a long-press action for
controls such as volume up or volume down. The runtime uses the global hold
repeat interval and maximum below unless an existing advanced action object
overrides them.

Button IDs link the pin and action dictionaries and are also used by optional
applet button-mode layouts such as the Apple TV remote. They do not correspond
to physical header positions. Keep an existing ID stable once another mode
refers to it. Removing a row removes that button from the normal pin and action
dictionaries. A button may have a pin and no normal actions while its command
mapping is still being planned.

## Runtime Requirements

Additional buttons currently use the local `pigpio` daemon:

```python
PHYSICAL_BUTTON_BACKEND = "pigpio"
PHYSICAL_BUTTON_PIGPIO_HOST = "127.0.0.1"
PHYSICAL_BUTTON_PIGPIO_PORT = 8888
```

The subsystem remains inactive when `PHYSICAL_BUTTONS_ENABLED` is false. When
enabled, `pigpiod` must be installed, running, and reachable from Home Suite.

## Advanced Timing

The defaults in `app_config.py` control debounce, transition settling,
double-press timing, long-press timing, and optional hold repetition:

```python
PHYSICAL_BUTTON_DEBOUNCE_MS = 40
PHYSICAL_BUTTON_SETTLE_MS = 25
PHYSICAL_BUTTON_DOUBLE_PRESS_WINDOW_MS = 350
PHYSICAL_BUTTON_LONG_PRESS_MS = 800
PHYSICAL_BUTTON_HOLD_REPEAT_INTERVAL_MS = 350
PHYSICAL_BUTTON_HOLD_REPEAT_MAX_REPEATS = 30
```

Keep these defaults unless logs show contact bounce or a specific control needs
different gesture timing. These advanced settings can remain in
`local_prefs.py` until they receive dedicated console controls.

## Apply And Test

Reviewing and saving a button mapping creates a backup and updates
`local_prefs.py`, but the console does not restart the voice service. Restart
`homesuite.service` when ready, then test every configured gesture. The editor
rejects duplicate pins, a pin shared with enabled PTT, unsupported gesture
names, and actions without executable command text.
