# Go2 Video Stream Flask App

A Flask-based web application for streaming live video from the Unitree Go2 robot via WebRTC.

## Features

- **Real-time video streaming** from Go2 robot camera
- **WebRTC connection** with automatic reconnection
- **Responsive web interface** with live statistics
- **Connection monitoring** with automatic keepalive
- **Base64-encoded JPEG frames** for reliable transmission

## Prerequisites

- Go2 robot in AP mode
- Connected to Go2 WiFi network (192.168.12.1)
- Unitree mobile app NOT connected (only one WebRTC connection allowed)
- Python virtual environment with required packages installed

## Installation

1. Ensure you're in the virtual environment:
   ```bash
   # Windows PowerShell
   .\venv\Scripts\Activate.ps1
   ```

2. The app uses the same dependencies as the main project.

## Usage

1. Start the video stream server:
   ```bash
   python tmp/video_stream/app.py
   ```

2. Open your web browser and navigate to:
   ```
   http://127.0.0.1:8080/
   ```

3. The video feed should appear automatically once the robot connects.

## How It Works

### Backend (Flask + WebRTC)

1. **WebRTC Connection**: Establishes connection to Go2 robot via WebRTC
2. **Video Channel**: Enables the robot's video channel
3. **Frame Processing**: Receives video frames and converts them to JPEG
4. **WebSocket Streaming**: Sends base64-encoded frames to the browser via Socket.IO

### Frontend (HTML + JavaScript)

1. **Canvas Display**: Renders video frames on an HTML5 canvas
2. **Real-time Updates**: Displays connection status and statistics
3. **Responsive Design**: Works on desktop and mobile devices

## Architecture

```
Go2 Robot ──WebRTC──► Flask Server ──WebSocket──► Browser
    │                      │                         │
    ├── Video frames       ├── JPEG encoding         ├── Canvas rendering
    ├── Connection status  ├── Statistics tracking   └── Live stats display
    └── Data channel       └── Base64 encoding       └── Responsive UI
```

## Connection Details

- **Connection Method**: Local AP mode (192.168.12.1)
- **Video Format**: JPEG frames at ~10 FPS
- **Transport**: Base64-encoded over WebSocket
- **Keepalive**: Automatic every 20 seconds
- **Reconnection**: Automatic on connection loss

## Troubleshooting

### No Video Appears
- Ensure Go2 is powered on and in AP mode
- Check that Unitree mobile app is closed
- Verify you're connected to the correct WiFi network
- Check console logs for connection errors

### Connection Fails
- Try restarting the Go2 robot
- Ensure no other applications are using WebRTC
- Check firewall settings
- Verify virtual environment is activated

### Poor Performance
- Reduce browser tabs/windows
- Check network connection stability
- Monitor system resources

## Configuration

The app runs on `127.0.0.1:8080` by default. To change:

```python
socketio.run(app, host='127.0.0.1', port=8080, ...)
```

## Dependencies

- Flask
- Flask-SocketIO
- OpenCV (cv2)
- WebRTC libraries (aiortc, aioice)
- Socket.IO client library (included via CDN)

## Browser Compatibility

- Chrome/Chromium (recommended)
- Firefox
- Safari
- Edge

## Known Limitations

- Video resolution is determined by the robot's camera
- Frame rate capped at 10 FPS for reliability
- Requires stable network connection
- Only one WebRTC connection allowed per robot

## Related Projects

- `tmp/lidar2/app.py` - LiDAR visualization app
- `tmp/min_connect_status.py` - Basic connection testing
- `tmp/sit-stand.py` - Robot posture control
