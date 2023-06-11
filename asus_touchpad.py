#!/usr/bin/env python3

import argparse
import asyncio
import importlib
import logging
import math
import os
import re
import subprocess
import sys
from typing import Any, Callable, Optional

import evdev
from evdev import KeyEvent, ecodes
import pyudev

# Setup logging
# LOG=DEBUG sudo -E ./asus-touchpad-numpad-driver  # all messages
# LOG=ERROR sudo -E ./asus-touchpad-numpad-driver  # only error messages
logging.basicConfig()
log = logging.getLogger('Pad')
log.setLevel(os.environ.get('LOG', 'INFO'))


# Parse command line arguments

parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--model", type=str, choices=['gx701', 'm433ia', 'ux433fa', 'ux581l'], help='Model number corresponding to the layout of the numpad', default='m433ia')
parser.add_argument('--percentage-key', type=int, help='Key code for the "5" key (percent symbol on qwerty layouts)', default=ecodes.KEY_5)
parser.add_argument('--numpad-delay', type=float, help='delay (in seconds) for the numpad to be (de)activated when pressing the numlock button', default=0.4)
parser.add_argument('--custom-key-delay', type=float, help='delay (in seconds) for the custom key to be activated when pressing the custom action button', default=0.4)

args = parser.parse_args()

model = args.model
percentage_key = args.percentage_key
numpad_delay = args.numpad_delay
custom_action_delay = args.custom_key_delay

# Setup other parameters

model_layout = importlib.import_module('numpad_layouts.' + model)

# KEY_5: 6, KEY_APOSTROPHE: 40
# Special key names can be found in /usr/share/X11/xkb/keycodes/evdev
custom_key = ecodes.KEY_PLAYPAUSE


# Figure out devices from devices file

touchpad: Optional[evdev.InputDevice] = None
keyboard: Optional[evdev.InputDevice] = None
device_id: Optional[str] = None

udev_context = pyudev.Context()

for path in evdev.list_devices():
    device = evdev.InputDevice(path)

    # Look for the touchpad
    if touchpad is None and ('ASUE' in device.name or 'ELAN' in device.name) and 'Touchpad' in device.name:
        log.debug('Detected touchpad from %s', str(device))

        sys_path = pyudev.Devices.from_device_file(udev_context, device.path).sys_path
        device_id = re.sub(r".*i2c-(\d+)/.*$", r'\1', sys_path)
        log.debug('Set touchpad device id %s from %s', device_id, sys_path)

        touchpad = device
        log.debug('Set touchpad from %s', device.path)

    # Look for the keyboard (numlock) # AT Translated Set OR Asus Keyboard
    if keyboard is None and ('AT Translated Set 2 keyboard' in device.name or 'Asus Keyboard' in device.name):
        log.debug('Detected keyboard from %s', str(device))

        keyboard = device
        log.debug('Set keyboard from %s', device.path)

    if touchpad is not None and keyboard is not None:
        break

if touchpad is None:
    log.error("Can't find touchpad")
    sys.exit(1)

if not device_id.isnumeric():
    log.error("Can't find device id")
    sys.exit(1)

if keyboard is None:
    log.error("Can't find keyboard")
    sys.exit(1)
    

# Retrieve touchpad dimensions

x_axis = touchpad.absinfo(ecodes.ABS_X)
min_x = x_axis.min
max_x = x_axis.max
y_axis = touchpad.absinfo(ecodes.ABS_Y)
min_y = y_axis.min
max_y = y_axis.max
log.debug('Touchpad min-max: x %d-%d, y %d-%d', min_x, max_x, min_y, max_y)


# Create a new keyboard device to send numpad events

enabled_keys: list[int] = [
    ecodes.KEY_LEFTSHIFT,
    ecodes.KEY_NUMLOCK,
    custom_key
]

for col in model_layout.keys:
    for key in col:
        enabled_keys.append(key)

if percentage_key != ecodes.KEY_5:
    enabled_keys.append(percentage_key)

capabilities = {ecodes.EV_KEY: enabled_keys}
udev = evdev.UInput(capabilities, name='Asus Touchpad/Numpad')


# Brightness 31: Low, 24: Half, 1: Full

BRIGHT_VAL = [hex(val) for val in [31, 24, 1]]


def change_brightness(brightness):
    """Cycles through the brightness values (min -> medium -> max)"""

    brightness = (brightness + 1) % len(BRIGHT_VAL)
    numpad_cmd = "i2ctransfer -f -y " + device_id + \
        " w13@0x15 0x05 0x00 0x3d 0x03 0x06 0x00 0x07 0x00 0x0d 0x14 0x03 " + \
        BRIGHT_VAL[brightness] + " 0xad"
    subprocess.call(numpad_cmd, shell=True)
    return brightness


def activate_numlock(brightness):
    """Activates the numpad"""

    numpad_cmd = "i2ctransfer -f -y " + device_id + \
        " w13@0x15 0x05 0x00 0x3d 0x03 0x06 0x00 0x07 0x00 0x0d 0x14 0x03 " + \
        BRIGHT_VAL[brightness] + " 0xad"
    udev.write(ecodes.EV_KEY, ecodes.KEY_NUMLOCK, 1)
    udev.syn()
    touchpad.grab()
    subprocess.call(numpad_cmd, shell=True)


def deactivate_numlock():
    """Disables the numpad"""

    numpad_cmd = "i2ctransfer -f -y " + device_id + \
        " w13@0x15 0x05 0x00 0x3d 0x03 0x06 0x00 0x07 0x00 0x0d 0x14 0x03 0x00 0xad"
    udev.write(ecodes.EV_KEY, ecodes.KEY_NUMLOCK, 0)
    udev.syn()
    touchpad.ungrab()
    subprocess.call(numpad_cmd, shell=True)


def launch_custom_action():
    """Send events related to the custom action key"""

    try:
        udev.write(ecodes.EV_KEY, custom_key, 1)
        udev.syn()
        udev.write(ecodes.EV_KEY, custom_key, 0)
        udev.syn()
    except OSError as e:
        pass


# Run - process and act on events

def event_filter(event: evdev.InputEvent) -> bool:
    """Indicates if an evdev event should be handled

    Handled events are position and finger events
    """
    return (
        event.type == ecodes.EV_ABS and (
            event.code == ecodes.ABS_MT_POSITION_X or
            event.code == ecodes.ABS_MT_POSITION_Y
        )
    ) or (
        event.type == ecodes.EV_KEY and
        event.code == ecodes.BTN_TOOL_FINGER
    )


async def wait(delay: float):
    """Async function waiting for `delay` seconds"""

    await asyncio.sleep(delay)


def call_later(delay: float, fun: Callable[[Any], Any]):
    """Creates a task which will call the specified function after a certain delay

    Parameters
    ----------
    delay: float
        The delay to wait in seconds
    fun: Callable[[Any], Any]
        The function to call after the delay. The first argument is the task,
        the function should return a value (i.e not a void function)

    Returns
    -------
    Task[None]
        The coroutine handle for the created task
    """

    task = asyncio.create_task(wait(delay))
    task.add_done_callback(fun)
    return task


async def main_loop(udev: evdev.UInput):
    """Main event loop"""

    numlock: bool = False
    x: int = 0
    y: int = 0
    row: int = 0
    col: int = 0
    brightness: int = 0 # (0 = low, 1 = medium, 2 = high)
    button_pressed: Optional[int] = None
    numlock_pressed: bool = False
    numlock_task: Optional[asyncio.Task] = None
    custom_key_pressed: bool = False
    custom_action_task: Optional[asyncio.Task] = None


    def trigger_numpad(task: asyncio.Task):
        """Callback called after the numlock key has been hit"""
        
        nonlocal numlock
        if not task.cancelled() and (x > 0.95 * max_x) and (y < 0.09 * max_y):
            numlock = not numlock
            if numlock:
                activate_numlock(brightness)
            else:
                deactivate_numlock()
        return None


    def trigger_custom_action(task: asyncio.Task):
        """Callback called after the custom action key has been hit"""

        if not task.cancelled() and (x < 0.06 * max_x) and (y < 0.07 * max_y):
            launch_custom_action()
        return None


    # Event loop

    async for event in touchpad.async_read_loop():
        if not event_filter(event):
            continue

        if event.type == ecodes.EV_ABS:
            # Get x position
            if event.code == ecodes.ABS_MT_POSITION_X:
                x = event.value
            # Get y position
            elif event.code == ecodes.ABS_MT_POSITION_Y:
                y = event.value
            continue

        # Else event is tap

        event = KeyEvent(event)

        if event.keystate == KeyEvent.key_up:
            log.debug('finger up at x %d y %d', x, y)

            if numlock_pressed:
                log.debug('numlock key is released')
                numlock_pressed = False
                if numlock_task is not None and not numlock_task.done():
                    numlock_task.cancel()
                numlock_task = None

            elif custom_key_pressed:
                log.debug('custom key is released')
                custom_key_pressed = False
                if custom_action_task is not None and not custom_action_task.done():
                    custom_action_task.cancel()
                custom_action_task = None

            elif button_pressed:
                log.debug('send key up event %s', button_pressed)
                try:
                    udev.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
                    udev.write(ecodes.EV_KEY, button_pressed, 0)
                    udev.syn()
                    button_pressed = None
                except OSError as err:
                    log.error("Cannot send release event, %s", err)
                    pass

        elif event.keystate == KeyEvent.key_down and not button_pressed:
            log.debug('finger down at x %d y %d', x, y)

            # Check if numlock was hit
            if (x > 0.95 * max_x) and (y < 0.09 * max_y):
                numlock_pressed = True
                numlock_task = call_later(numpad_delay, trigger_numpad)
                continue

            # Check if custom key was hit
            if (x < 0.06 * max_x) and (y < 0.07 * max_y):
                if numlock:
                    brightness = change_brightness(brightness)
                else:
                    custom_key_pressed = True
                    custom_action_task = call_later(custom_action_delay, trigger_custom_action)
                continue

            # If touchpad mode, ignore
            if not numlock:
                continue

            # else numpad mode is activated
            col = math.floor(model_layout.cols * x / (max_x+1))
            row = math.floor((model_layout.rows * y / max_y) - model_layout.top_offset)

            # Ignore top_offset region
            if row < 0:
                continue

            try:
                button_pressed = model_layout.keys[row][col]
            except IndexError:
                # skip invalid row and col values
                log.debug('Unhandled col/row %d/%d for position %d-%d', col, row, x, y)
                continue

            if button_pressed == ecodes.KEY_5:
                button_pressed = percentage_key

            # Send press key event
            log.debug('send press key event %s', button_pressed)

            try:
                if button_pressed == percentage_key:
                    udev.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)   
                udev.write(ecodes.EV_KEY, button_pressed, 1)
                udev.syn()
            except OSError as err:
                log.warning("Cannot send press event, %s", err)


# Run the loop

loop = asyncio.get_event_loop()
loop.run_until_complete(main_loop(udev))

