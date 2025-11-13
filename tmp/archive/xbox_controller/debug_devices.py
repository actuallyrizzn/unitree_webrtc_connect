"""
Debug script to list all devices detected by the inputs library.
This helps troubleshoot controller detection issues.
"""

import sys
from inputs import devices

print("=" * 60)
print("Input Devices Debug Tool")
print("=" * 60)
print()

all_devices = list(devices)

if not all_devices:
    print("No devices detected by the inputs library.")
    print()
    print("Possible reasons:")
    print("  1. No input devices connected")
    print("  2. Permission issues (try running as Administrator)")
    print("  3. The inputs library may not have access to the devices")
    sys.exit(1)

print(f"Total devices detected: {len(all_devices)}")
print()

# Group by device type
by_type = {}
for device in all_devices:
    dev_type = device.device_type
    if dev_type not in by_type:
        by_type[dev_type] = []
    by_type[dev_type].append(device)

print("Devices by type:")
print()
for dev_type, dev_list in sorted(by_type.items()):
    print(f"  {dev_type}: {len(dev_list)} device(s)")
    for device in dev_list:
        print(f"    - '{device.name}'")
print()

# Look for Xbox controllers specifically
print("Searching for Xbox controllers...")
xbox_keywords = ["xbox", "microsoft", "controller"]
found_xbox = False

for device in all_devices:
    name_lower = device.name.lower()
    for keyword in xbox_keywords:
        if keyword in name_lower:
            print(f"  ✓ Potential Xbox controller found:")
            print(f"    Name: '{device.name}'")
            print(f"    Type: {device.device_type}")
            found_xbox = True
            break

if not found_xbox:
    print("  ✗ No devices matching Xbox controller keywords found.")
    print()
    print("  If your controller is connected but not listed above:")
    print("  - Try running this script as Administrator")
    print("  - Check Device Manager to see how Windows recognizes the controller")
    print("  - The inputs library may not have access to your specific controller model")

print()
print("=" * 60)

