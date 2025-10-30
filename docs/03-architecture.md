## Architecture

This codebase centers on a `Go2WebRTCConnection` that provisions an `aiortc` `RTCPeerConnection` and attaches three major channels:
- Data channel: `WebRTCDataChannel` (messaging, validation, heartbeat, request/response, LiDAR payload decoding)
- Audio channel: `WebRTCAudioChannel` (sendrecv transceiver, frame callbacks, channel switch)
- Video channel: `WebRTCVideoChannel` (recvonly transceiver, frame callbacks, channel switch)

### Connection Lifecycle

1) Choose connection method via `WebRTCConnectionMethod`:
   - LocalAP: fixed IP `192.168.12.1`
   - LocalSTA: same LAN, pass `ip` or discover by `serialNumber` using multicast
   - Remote: get TURN info using Unitree cloud API (needs `username/password` → token)

2) Create SDP offer and set local description.

3) Send SDP to peer to obtain answer:
   - Local: HTTP endpoints on robot (`:8081/offer` old or `:9991` key-exchange new)
   - Remote: Unitree cloud `webrtc/connect` with RSA/AES encryption

4) Set remote description, wait for data channel validation to complete.

5) Data channel on-validate callback starts heartbeat and network status polling.

### Data Channel Subsystem

Key classes under `go2_webrtc_driver/msgs` and `webrtc_datachannel.py`:
- Pub/Sub (`WebRTCDataChannelPubSub`):
  - `publish` (awaits response), `publish_without_callback` (fire-and-forget)
  - `subscribe`/`unsubscribe` for topics
  - Futures keyed by message type/topic/uuid using `FutureResolver`
- Validation (`WebRTCDataChannelValidaton`):
  - Handles initial handshake; MD5-based response then signals success
- Heartbeat (`WebRTCDataChannelHeartBeat`):
  - Periodic heartbeat message every ~2 seconds
- RTC Inner Req (`WebRTCDataChannelRTCInnerReq`):
  - Network status polling; probe responses
- FutureResolver:
  - Correlates responses using keys (`uuid`, `header.identity.id`, etc.)
  - Handles chunked data assembly (both generic payloads and file transfers)

LiDAR payloads can be indicated by headers and decoded via the configured decoder.

### Media Channels

- Video: `recvonly` track; add callbacks with `conn.video.add_track_callback(callback)` receiving an `aiortc.MediaStreamTrack` to `await track.recv()` frames
- Audio: `sendrecv` track; audio frames are received in `frame_handler` and forwarded to registered callbacks.
- Both channels expose `switchVideoChannel(True/False)` and `switchAudioChannel(True/False)` through the data channel control messages to the robot.

### Topics and Commands

Topic constants and API IDs live in `constants.py` (e.g., `RTC_TOPIC`, `SPORT_CMD`, `AUDIO_API`). The data channel request pattern is standardized via `publish_request_new(topic, { api_id, parameter, priority? })`.

### LiDAR Decoding Pipeline

- Unified interface `UnifiedLidarDecoder` selects:
  - `libvoxel` (default): WASM pipeline (`wasmtime`) for voxel decompression + geometry extraction
  - `native`: Python + `lz4` to decompress bitfields and compute `(x,y,z)` points
- Binary payloads received in data channel messages are routed to the decoder based on header semantics. Decoded data is placed back into `message['data']['data']` for downstream consumers.

### Error Handling

- Device error notifications are handled under `msgs/error_handler.py` leveraging `app_error_messages` map in `constants.py` for human-readable text.

### Sequence Summary

- Init: `Go2WebRTCConnection.connect()`
- ICE/Signaling: state handlers log status
- Offer/Answer exchange (local or remote)
- Data channel created → validation handshake → heartbeat/network-status
- Subscriptions/requests active; optional video/audio switches + callbacks
