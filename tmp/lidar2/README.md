# Animus Go2 LiDAR Visualization

A Flask-based web server for real-time 3D visualization of Unitree Go2 LiDAR data with full Animus branding.

## Features

- **Real-time 3D point cloud visualization** using Three.js
- **WebRTC connection** to Go2 robot with auto-reconnect
- **Binary WebSocket protocol** for efficient data transmission
- **Active keepalive** (every 20 seconds) to maintain connection
- **Graceful shutdown** handling
- **Auto-reload** for development (Flask debug mode)
- **Connection monitoring** with multiple detection methods

## Improvements from Original Version

This version incorporates key learnings from the Rerun implementation:

1. **Better Connection Monitoring**
   - Checks `pc.connectionState` in addition to `isConnected` flag
   - Detects dead connections via message timeout (10s without messages)
   - Monitors connection every 1 second (instead of 5)

2. **Active Keepalive**
   - Sends `disableTrafficSaving(True)` every 20 seconds
   - Helps maintain connection stability

3. **Improved Error Handling**
   - Protected connection status checks (won't hang on errors)
   - Faster disconnect timeout (2s instead of 5s)
   - Better cleanup on shutdown

4. **Graceful Shutdown**
   - Signal handler for Ctrl+C
   - Shutdown flag checked in all loops
   - Clean thread termination

## Usage

```bash
python tmp/lidar2/app.py
```

Then open http://127.0.0.1:8080/ in your browser.

### Command Line Options

- `--host HOST` - Host to bind to (default: 127.0.0.1)
- `--port PORT` - Port to bind to (default: 8080)
- `--debug` - Enable debug mode

## Requirements

- Go2 robot powered on and in AP mode
- Connected to Go2 WiFi network (192.168.12.1)
- Unitree Go2 mobile app **CLOSED** (only one WebRTC connection allowed)

## Architecture

- **Flask** - Web server
- **Socket.IO** - Real-time bidirectional communication
- **Three.js** - 3D visualization in browser
- **WebRTC** - Connection to Go2 robot
- **NumPy** - Point cloud processing

## Data Flow

1. Go2 robot → WebRTC → Python backend
2. Python processes LiDAR data (rotation, filtering, downsampling)
3. Binary data sent via Socket.IO to browser
4. Three.js renders 3D point cloud in real-time

## Connection Stability

The system includes multiple mechanisms to maintain connection:

- **Traffic saving disabled** - Enables Go2's internal heartbeat
- **Active keepalive** - Sends keepalive every 20 seconds
- **Auto-reconnect** - Exponential backoff on disconnect
- **Connection monitoring** - Multiple detection methods

## Development

The server runs with Flask's auto-reloader enabled. When you save changes to `app.py`, the server will automatically restart (WebRTC connection will reconnect).

## Troubleshooting

- **Connection fails**: Make sure mobile app is closed and robot is powered on
- **No points showing**: Check browser console for errors
- **Connection drops frequently**: Check WiFi signal strength
- **Port already in use**: Change port with `--port` option

