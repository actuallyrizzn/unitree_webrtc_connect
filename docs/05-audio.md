## Audio

There are two independent audio features:
- Media audio track (sendrecv) via `WebRTCAudioChannel`
- Audio Hub control API (playlist/upload/megaphone) via data channel (`WebRTCAudioHub`)

### Media Audio Channel

Create connection and add audio frame callbacks:
```python
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod

async def on_audio_frame(frame):
    # frame is an aiortc AudioFrame; pull samples as needed
    pass

conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip="192.168.x.x")
await conn.connect()

# Turn audio channel on/off
conn.audio.switchAudioChannel(True)

# Register callback(s)
conn.audio.add_track_callback(on_audio_frame)
```

Notes:
- Audio channel is created with `sendrecv` transceiver. This driver currently forwards received frames to callbacks.
- Use data channel toggles to enable/disable the robot’s audio streaming.

### Audio Hub (Playlist/Upload/Megaphone)

`WebRTCAudioHub` wraps the topic `rt/api/audiohub/request` and exposes convenience methods. Typical workflow:

```python
from go2_webrtc_driver.webrtc_audiohub import WebRTCAudioHub

conn = ...
audio_hub = WebRTCAudioHub(conn)

# Query audio list
resp = await audio_hub.get_audio_list()

# Upload a file (mp3 or wav). MP3 is converted to WAV (44100Hz)
await audio_hub.upload_audio_file("path/to/file.wav")

# Play by UUID (from audio list)
await audio_hub.play_by_uuid("UUID")

# Pause/Resume
await audio_hub.pause()
await audio_hub.resume()

# Megaphone mode (stream short chunks)
await audio_hub.enter_megaphone()
await audio_hub.upload_megaphone("path/to/file.wav")
await audio_hub.exit_megaphone()
```

Implementation notes:
- Uploads chunk base64 strings (~4KB each), with MD5 and metadata, as API requests.
- `AUDIO_API` Enum in `constants.py` defines the operation codes.
- Use `publish_request_new` under the hood; responses carry status in `data.header.status.code == 0`.
