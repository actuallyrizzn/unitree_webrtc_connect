## Troubleshooting

### Connection fails or times out
- Verify the chosen mode:
  - Local AP: Wi‑Fi connected to robot AP; `ip = 192.168.12.1`
  - Local STA: robot and client on same LAN; try direct `ip`, or ensure multicast discovery is permitted
  - Remote: correct Unitree credentials; internet available
- Firewall on Windows may block local HTTP ports (`8081`, `9991`) or UDP ICE; allow python.exe/network
- Ensure only one client is connected; the robot will reject SDP if another client is active

### Data channel does not validate
- The robot will first send a key; driver responds with MD5-derived base64 token
- If validation loops, ensure clock/timezone isn’t wildly off and connection is stable
- Check logs for `Validation Needed.` messages and that the response is sent

### No video or audio frames
- Confirm `conn.video.switchVideoChannel(True)` / `conn.audio.switchAudioChannel(True)`
- Ensure callbacks are registered before expecting frames
- For video on Windows, drive GUI from a non-async thread (see example)

### LiDAR decoding errors
- If WASM path errors, verify `wasmtime` installed and `libvoxel.wasm` is present and loadable
- Switch to native decoder: `conn.datachannel.set_decoder('native')`

### API request returns non-zero status
- Inspect `response['data']['header']['status']` for codes/text
- Check `api_id` and `parameter` schema; many APIs expect JSON-stringified `parameter`

### Device busy or SDP rejected
- Error: `sdp == "reject"` → another client connected (close mobile app and retry)

### Intermittent connection / ICE failed
- Network segments/firewalls can block STUN/TURN UDP
- Remote mode relies on correct token and TURN info; refresh token and retry
