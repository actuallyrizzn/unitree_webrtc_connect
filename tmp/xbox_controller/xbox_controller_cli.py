"""
Xbox Controller CLI Interface
==============================

This script provides a CLI interface for reading input from an Xbox Wireless Controller.
It will gracefully exit if the controller is not detected.

Usage:
    python tmp/xbox_controller/xbox_controller_cli.py

Prerequisites:
    - Xbox Wireless Controller connected via Bluetooth or USB
    - inputs library installed (pip install inputs)
"""

import sys
import time
from inputs import get_gamepad, devices, UnpluggedError


def find_xbox_controller():
    """
    Find the Xbox Wireless Controller in the list of connected devices.
    Uses flexible matching to handle name variations and device types.
    On Windows, Xbox controllers may be classified as "joystick" instead of "gamepad".
    
    Returns:
        device: The gamepad device object, or None if not found
    """
    # Common Xbox controller name variations
    xbox_patterns = [
        "xbox wireless controller",
        "xbox controller",
        "microsoft xbox",
        "xbox 360",
        "xbox one",
        "xbox series",
        "x-box",  # For "Microsoft X-Box 360 pad"
        "xbox"
    ]
    
    # Check joysticks first (Windows often classifies Xbox controllers as joysticks)
    joysticks = [device for device in devices if device.device_type == "joystick"]
    for joystick in joysticks:
        name_lower = joystick.name.lower()
        for pattern in xbox_patterns:
            if pattern in name_lower:
                return joystick
    
    # Check gamepads
    gamepads = [device for device in devices if device.device_type == "gamepad"]
    for gamepad in gamepads:
        name_lower = gamepad.name.lower()
        for pattern in xbox_patterns:
            if pattern in name_lower:
                return gamepad
    
    # If not found, check all devices (fallback)
    for device in devices:
        name_lower = device.name.lower()
        for pattern in xbox_patterns:
            if pattern in name_lower:
                return device
    
    return None


def format_event(event):
    """
    Format a gamepad event for display.
    
    Args:
        event: The event object from inputs library
        
    Returns:
        str: Formatted string representation of the event
    """
    event_type = event.ev_type
    code = event.code
    state = event.state
    
    # Format based on event type
    if event_type == "Key":
        # Button press/release
        return f"Button: {code:20s} | State: {'PRESSED' if state else 'RELEASED'}"
    elif event_type == "Absolute":
        # Analog stick or trigger
        if "ABS_X" in code or "ABS_Y" in code or "ABS_RX" in code or "ABS_RY" in code:
            # Analog stick (values typically -32768 to 32767)
            normalized = state / 32768.0
            return f"Stick:  {code:20s} | Value: {state:6d} ({normalized:+.3f})"
        elif "ABS_Z" in code or "ABS_RZ" in code:
            # Trigger (values typically 0 to 255)
            normalized = state / 255.0
            return f"Trigger: {code:20s} | Value: {state:6d} ({normalized:.3f})"
        else:
            return f"Absolute: {code:20s} | Value: {state:6d}"
    elif event_type == "Sync":
        # Sync event (usually can be ignored for display)
        return None
    else:
        return f"{event_type}: {code:20s} | State: {state}"


def main():
    """Main function to run the Xbox controller CLI interface."""
    print("=" * 60)
    print("Xbox Controller CLI Interface")
    print("=" * 60)
    print()
    
    # List all connected devices for debugging
    print("Scanning for connected devices...")
    print()
    
    # Show all devices first (for debugging)
    all_devices = list(devices)
    gamepads = [device for device in devices if device.device_type == "gamepad"]
    joysticks = [device for device in devices if device.device_type == "joystick"]
    
    print(f"Total devices detected: {len(all_devices)}")
    print(f"Gamepads detected: {len(gamepads)}")
    print(f"Joysticks detected: {len(joysticks)}")
    print()
    
    if all_devices:
        print("All detected devices:")
        for i, device in enumerate(all_devices, 1):
            print(f"  {i}. Name: '{device.name}' | Type: {device.device_type}")
        print()
    
    if not gamepads and not joysticks:
        print("WARNING: No devices classified as 'gamepad' or 'joystick' detected.")
        print("This might be normal - checking all devices for Xbox controller...")
        print()
    
    # Find Xbox Wireless Controller
    xbox_controller = find_xbox_controller()
    
    if not xbox_controller:
        print("ERROR: Xbox controller not found.")
        print()
        print("Troubleshooting:")
        print("  1. Ensure your Xbox controller is connected (Bluetooth or USB)")
        print("  2. On Windows, you may need to run this script as Administrator")
        print("  3. Try unplugging and reconnecting the controller")
        print("  4. Check Device Manager to verify the controller is recognized")
        print()
        if all_devices:
            print("The devices listed above are what the inputs library can see.")
            print("If your controller is not in the list, it may not be accessible to the inputs library.")
        sys.exit(1)
    
    print(f"✓ Found Xbox controller: {xbox_controller.name} (Type: {xbox_controller.device_type})")
    print()
    print("Controller input will be displayed below.")
    print("Press Ctrl+C to exit.")
    print()
    print("-" * 60)
    print()
    
    try:
        # Main event loop
        while True:
            events = get_gamepad()
            for event in events:
                formatted = format_event(event)
                if formatted:  # Skip None (sync events, etc.)
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"[{timestamp}] {formatted}")
    
    except KeyboardInterrupt:
        print()
        print("-" * 60)
        print("Exiting...")
        sys.exit(0)
    except UnpluggedError:
        print()
        print("-" * 60)
        print("Controller disconnected.")
        print("The Xbox controller was unplugged or disconnected.")
        print("Please reconnect the controller and run the script again.")
        print("-" * 60)
        sys.exit(0)
    except Exception as e:
        print()
        print("-" * 60)
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        print("-" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()

