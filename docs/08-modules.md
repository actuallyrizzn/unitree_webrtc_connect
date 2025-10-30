## Modules Reference

Top-level package: `go2_webrtc_driver`

- `webrtc_driver.py`
  - `Go2WebRTCConnection`: orchestrates connection init, SDP exchange, and channel setup
  - Instantiates `WebRTCDataChannel`, `WebRTCAudioChannel`, `WebRTCVideoChannel`
  - Handles connection state logging, reconnect, configuration of ICE servers
  - Sends SDP via local or remote methods based on `WebRTCConnectionMethod`

- `webrtc_datachannel.py`
  - Creates `RTCDataChannel('data')`
  - Composes subsystems: `WebRTCDataChannelPubSub`, `WebRTCDataChannelHeartBeat`, `WebRTCDataChannelValidaton`, `WebRTCDataChannelRTCInnerReq`
  - Provides `switchVideoChannel`, `switchAudioChannel`, `disableTrafficSaving`, `set_decoder`
  - Routes incoming messages by `type` to appropriate handlers; parses binary payloads and LiDAR

- `webrtc_video.py`
  - Adds video transceiver (recvonly), manages callbacks, toggles video via data channel

- `webrtc_audio.py`
  - Adds audio transceiver (sendrecv), registers frame callbacks, toggles audio via data channel

- `webrtc_audiohub.py`
  - High-level helper over `rt/api/audiohub/request`: listing, playing, upload (chunks), megaphone, rename/delete, play mode

- `constants.py`
  - Enums/Maps: `DATA_CHANNEL_TYPE`, `WebRTCConnectionMethod`, `RTC_TOPIC`, `SPORT_CMD`, `AUDIO_API`, and error text map

- `multicast_scanner.py`
  - Multicast discovery for Local STA by serial number → IP mapping

- `unitree_auth.py`
  - Remote API authentication and request signing (headers, time/nonce/sign)
  - Local SDP exchange (old `:8081/offer` and new `:9991` staged key-exchange flow)

- `util.py`
  - Token fetching, public key retrieval, TURN info fetch (RSA/AES crypto bridging)
  - Helpers: `generate_uuid`, `get_nested_field`, status printing

- `encryption.py`
  - AES (ECB + PKCS#5) and RSA (PKCS1 v1_5) helpers for Unitree API contract

- `msgs/` subsystem
  - `pub_sub.py`: publish/subscribe, request helper, subscriptions, futures manager
  - `future_resolver.py`: correlation, chunk assembly for generic data and files
  - `validation.py`: validation handshake
  - `heartbeat.py`: heartbeat send/receive
  - `rtc_inner_req.py`: network status polling, probe response, file up/download helpers
  - `error_handler.py`: error mapping/printing

- `lidar/`
  - `lidar_decoder_unified.py`: decoder selector facade
  - `lidar_decoder_libvoxel.py`: WASM voxel pipeline
  - `lidar_decoder_native.py`: Python/LZ4 point cloud decoder

Examples under `examples/` demonstrate typical usage by area (video, audio, data channel topics, LiDAR plotting).
