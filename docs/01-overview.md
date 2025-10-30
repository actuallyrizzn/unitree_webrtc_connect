## Overview

This repository implements a Python WebRTC client for the Unitree Go2 robot. It establishes a WebRTC PeerConnection (audio/video/data) to the robot and exposes a convenient Python API to:

- Receive video frames (recvonly video transceiver)
- Receive audio frames and optionally control audio channel state (sendrecv audio transceiver)
- Interact with Go2 high-level APIs via the WebRTC data channel using a publish/subscribe and request/response pattern (topics in `RTC_TOPIC`)
- Decode LiDAR streams using either a WASM-based decoder or a native Python decoder

High-level entrypoint: create `Go2WebRTCConnection`, choose connection method, then use `conn.video`, `conn.audio`, and `conn.datachannel`.

Core components:
- WebRTC setup and SDP exchange (local AP/STA or remote via Unitree cloud)
- Data channel middleware (Pub/Sub, validation handshake, heartbeat, request APIs, chunking)
- Media channels for audio and video with callback-centric consumption
- LiDAR decoding pipeline

Supported connection modes:
- Local AP (direct Wi‑Fi): `WebRTCConnectionMethod.LocalAP`
- Local STA (same LAN via IP or auto-discovery by serial): `WebRTCConnectionMethod.LocalSTA`
- Remote (TURN via Unitree cloud): `WebRTCConnectionMethod.Remote`

See [Architecture](./03-architecture.md) for flow diagrams and responsibilities, and [Examples](./09-examples.md) for runnable scripts.


