# Go2 Audio Stream Flask App

A Flask-based web application for streaming live audio from the Unitree Go2 robot via WebRTC.

## Features

- **Real-time audio streaming** from the Go2 microphone
- **WebRTC connection** with automatic reconnection and keepalive
- **Web Audio API playback** with user-controlled start
- **Live statistics** showing connection state, sample rate, and channel count
- **Base64-encoded PCM chunks** transmitted over Socket.IO for reliability

## Prerequisites

- Go2 robot in AP mode
- Connected to Go2 WiFi network (`192.168.12.1`)
- Unitree mobile app **not** connected (only one WebRTC session allowed)
- Python virtual environment activated with project dependencies installed

## Usage

1. Activate the virtual environment (if not already active):
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

2. Start the audio streaming server:
   ```powershell
   python tmp/audio_stream/app.py
   ```

3. Open a web browser and navigate to:
   ```
   http://127.0.0.1:8090/
   ```

4. Click **Start Audio** to initialize playback (required by browsers for audio output).

## Architecture

```
Go2 Microphone ──WebRTC──► Flask Server ──WebSocket──► Browser AudioContext
      │                       │                            │
      ├── PCM frames          ├── PCM → Base64             ├── Decode to Float32
      └── Connection status   └── Stats + keepalive        └── Schedule playback
```

## Implementation Details

### Backend
- Establishes a `Go2WebRTCConnection` in Local AP mode
- Registers an async audio track handler via `conn.audio.add_track_callback`
- Converts `AudioFrame` objects to interleaved 16-bit PCM bytes
- Sends Base64-encoded chunks to the browser through Socket.IO
- Tracks connection state, chunk counts, sample rate, and channel count
- Issues keepalive messages (`disableTrafficSaving(True)`) every 20 seconds

### Frontend
- Uses Socket.IO to receive `audio_chunk` events
- Initializes a `AudioContext` when the user clicks the start button
- Converts Base64 PCM data to `AudioBuffer` and schedules playback
- Displays live statistics and connection state updates

## Configuration

Default HTTP endpoint: `http://127.0.0.1:8090/`

You can adjust the host or port in `app.py`:
```python
socketio.run(app, host='127.0.0.1', port=8090, ...)
```

## Troubleshooting

| Issue | Possible Fix |
|-------|--------------|
| No audio playback | Ensure the **Start Audio** button was pressed (browsers block autoplay). |
| Silence after a period | Check console logs for disconnection messages; ensure keepalive is reaching the robot. |
| Connection errors | Verify WiFi connection to Go2, close the Unitree mobile app, and restart the robot if needed. |
| Browser errors | Use a modern browser (Chrome/Edge/Firefox). Safari may require additional user interactions. |

## Related Apps
- `tmp/video_stream/app.py` – Live video streaming
- `tmp/lidar2/app.py` – LiDAR visualization
- `tmp/min_connect_status.py` – Minimal connection test script

## Notes
- Audio frames are streamed as raw PCM (`int16`) for minimal latency.
- Browser playback is mixed to the default output device using the Web Audio API.
- Frame pacing is approximated using chunk duration to avoid buffer underruns.
