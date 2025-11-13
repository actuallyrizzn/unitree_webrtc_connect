# Go2 Fault Investigation

This folder contains tools for diagnosing robot faults and sensor issues.

## sensor_monitor.py

A comprehensive sensor monitoring script that connects to the Go2 and monitors all critical sensor feeds.

### Features

- **Motor Temperature Monitoring**: Real-time display of all 12 motor temperatures with color-coded warnings
- **Motor State**: Position, lost packet counts for each motor
- **Battery Management System (BMS)**: SOC, current, temperature sensors
- **IMU Data**: Roll, pitch, yaw, and IMU temperature
- **Foot Force Sensors**: Force readings from all 4 feet
- **Sport Mode State**: Mode, body height, position, velocity
- **Error Capture**: Automatically captures and logs all error messages from the robot
- **File Logging**: All sensor data is logged to `sensor_log.txt` with timestamps

### Usage

```bash
python tmp/fault_investigation/sensor_monitor.py
```

### What to Look For

**Overheating Indicators:**
- Motor temperatures above 70°C (warning)
- Motor temperatures above 80°C (critical)
- Error codes:
  - `300_10`: "Winding overheating"
  - `300_4`: "Driver overheating"
  - `600_4`: "Overheating software protection"

**Communication Issues:**
- High "Lost" packet counts on motors
- Error codes:
  - `100_80`: "Motor communication error"
  - `300_100`: "Motor communication interruption"

**Other Fault Indicators:**
- `300_1`: "Overcurrent"
- `300_2`: "Overvoltage"
- `300_20`: "Encoder abnormal"
- `400_1`: "Motor rotate speed abnormal"

### Output

The script displays a real-time dashboard showing:
- Motor temperatures (highlighted if >60°C)
- BMS state
- IMU orientation
- Foot force readings
- Sport mode state
- Error count

All data is also logged to `sensor_log.txt` for later analysis.

### Connection

Uses the same robust connection logic as `sit-stand.py`:
- Automatic retry on connection failures
- Handles intermittent `NoneType.media` errors
- 3-attempt retry loop with 1-second delays

Make sure the Unitree mobile app is **CLOSED** before running, as the Go2 can only handle one WebRTC connection at a time.

