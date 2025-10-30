## Video

Video is a `recvonly` transceiver. Frames are obtained by registering a callback that receives an `aiortc.MediaStreamTrack`. You pull frames by `await track.recv()` and render them (e.g., OpenCV).

### Basic Usage

```python
import asyncio
import cv2
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from aiortc import MediaStreamTrack

async def on_video_track(track: MediaStreamTrack):
    while True:
        frame = await track.recv()
        img = frame.to_ndarray(format="bgr24")
        cv2.imshow('Video', img)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

async def main():
    conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip="192.168.x.x")
    await conn.connect()

    conn.video.switchVideoChannel(True)
    conn.video.add_track_callback(on_video_track)

asyncio.run(main())
```

Notes:
- `switchVideoChannel(True)` publishes a control message to enable video streaming on the robot side.
- Use a UI thread or a loop with queues for OpenCV rendering on Windows to avoid event loop blocking (see example).
