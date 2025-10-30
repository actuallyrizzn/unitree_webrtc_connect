## Examples Walkthroughs

This repository includes runnable examples organized by feature. Use them to validate your setup and learn the APIs.

Run examples on Windows PowerShell:
```powershell
cd C:\\projects\\animus\\go2-testing\\unitree_webrtc_connect
.\\.venv\\Scripts\\Activate.ps1  # if using a venv
```

### Video: Camera Stream

File: `examples/video/camera_stream/display_video_channel.py`

- Creates an OpenCV window and prints frame details
- Connects via `Go2WebRTCConnection`
- `conn.video.switchVideoChannel(True)`
- Registers a video track callback and displays frames

Run:
```powershell
python .\\examples\\video\\camera_stream\\display_video_channel.py
```

### Data Channel: LowState

File: `examples/data_channel/lowstate/lowstate.py`

- Subscribes to `RTC_TOPIC['LOW_STATE']`
- Continuously prints IMU, motor states, BMS info, foot forces, etc.

Run:
```powershell
python .\\examples\\data_channel\\lowstate\\lowstate.py
```

### Data Channel: Sport Mode

File: `examples/data_channel/sportmode/sportmode.py`

- Queries current motion mode and switches to `normal` if needed
- Executes example moves via `SPORT_CMD`
- Demonstrates mode switching and simple motion requests

Run:
```powershell
python .\\examples\\data_channel\\sportmode\\sportmode.py
```

### Audio Hub: Upload and Play

File: `examples/audio/mp3_player/webrtc_audio_player.py`

- Connects and constructs `WebRTCAudioHub`
- Optionally uploads an audio file (MP3 → WAV conversion), then plays by UUID

Run:
```powershell
python .\\examples\\audio\\mp3_player\\webrtc_audio_player.py
```

### LiDAR: Stream and Plot

Files: `examples/data_channel/lidar/lidar_stream.py`, `plot_lidar_stream.py`

- Subscribe to LiDAR data topics
- Plot voxel/point data (requires numpy/matplotlib-type stack if extended)

Run:
```powershell
python .\\examples\\data_channel\\lidar\\lidar_stream.py
```

Notes:
- Update connection configuration in each example: LocalSTA IP, serial, or Remote credentials.
- If rendering with OpenCV on Windows, ensure the display loop doesn’t block the asyncio event loop (use thread + queue pattern as shown in the video example).
