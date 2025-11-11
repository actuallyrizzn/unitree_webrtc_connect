# Xbox Controller Interface

A CLI interface for reading input from an Xbox Wireless Controller, designed to eventually control the Unitree Go2 robot.

## Features

- Detects and connects to "Xbox Wireless Controller"
- Gracefully exits if controller is not found
- Real-time display of controller input (buttons, sticks, triggers)
- Clean CLI output with timestamps

## Prerequisites

- Python 3.x
- Xbox Wireless Controller connected via Bluetooth or USB
- `inputs` library installed in the virtual environment

## Installation

The `inputs` library should already be installed in the venv. If not:

```bash
.\venv\Scripts\python.exe -m pip install inputs
```

**Note for Windows**: The `inputs` library may require administrator privileges on Windows. If you encounter permission errors, try running the script as administrator.

## Usage

```bash
.\venv\Scripts\python.exe tmp\xbox_controller\xbox_controller_cli.py
```

## Controller Input Mapping

The script will display:
- **Buttons**: Press/release events (A, B, X, Y, D-pad, etc.)
- **Analog Sticks**: Left stick (ABS_X, ABS_Y) and Right stick (ABS_RX, ABS_RY)
- **Triggers**: Left trigger (ABS_Z) and Right trigger (ABS_RZ)

## Future Integration

This CLI interface will be extended to:
1. Map controller inputs to Go2 robot commands
2. Provide a Flask web interface for remote control
3. Integrate with the Go2 WebRTC connection for real-time robot control

## Troubleshooting

### Controller Not Detected

- Ensure the controller is connected via Bluetooth or USB
- On Windows, you may need to pair the controller through Settings > Devices
- Try running the script as administrator if permission errors occur

### No Input Displayed

- Press buttons or move sticks to generate events
- The script only displays events when they occur
- Sync events are filtered out for cleaner output

