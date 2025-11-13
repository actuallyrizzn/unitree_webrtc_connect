# Go2 LiDAR Visualization

A real-time 3D visualization tool for Unitree Go2 robot LiDAR data using WebRTC.

## Features

- **Real-time 3D visualization** using Three.js
- **WebRTC connection** to Go2 robot in AP mode
- **Color-coded point clouds** based on distance
- **Interactive controls** (rotate, pan, zoom)
- **Live statistics** (message count, point count, FPS)
- **Professional UI** with Flask templates and static assets

## Project Structure

```
tmp/lidar/
├── app.py                 # Main Flask application
├── templates/
│   └── index.html        # Main visualization page
├── static/
│   ├── css/
│   │   └── style.css     # Styling
│   └── js/
│       └── lidar_viewer.js  # Three.js visualization logic
└── README.md             # This file
```

## Requirements

- Unitree Go2 robot connected in AP mode (192.168.12.1)
- Python 3.11+ with dependencies from `requirements.txt`
- Modern web browser with WebGL support

## Usage

1. **Connect to Go2 WiFi** (ensure mobile app is closed)

2. **Start the server:**
   ```bash
   cd tmp/lidar
   python app.py
   ```

3. **Open browser** to http://127.0.0.1:8080/

4. **View real-time LiDAR data** in 3D

## Controls

- **Rotate:** Left mouse button + drag
- **Pan:** Right mouse button + drag
- **Zoom:** Mouse wheel

## Configuration

Edit `app.py` to adjust:
- `ROTATE_X_ANGLE`, `ROTATE_Z_ANGLE`: Point cloud rotation
- `minYValue`, `maxYValue`: Height filtering
- Point size and colors: Edit `static/js/lidar_viewer.js`

## Troubleshooting

- **No connection:** Ensure Go2 is powered on and mobile app is closed
- **No data:** Check that LiDAR is enabled (automatic on connection)
- **Performance issues:** Reduce point size or add downsampling

## Technology Stack

- **Backend:** Flask, Flask-SocketIO, aiortc
- **Frontend:** Three.js, Socket.IO
- **Protocol:** WebRTC with SDP/ICE for robot communication

