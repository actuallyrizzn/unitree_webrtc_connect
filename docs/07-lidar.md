## LiDAR

The driver supports LiDAR data over the WebRTC data channel and offers two decoders via `UnifiedLidarDecoder`:

- libvoxel (default): WASM-based voxel decompression + geometry extraction using `wasmtime` and `libvoxel.wasm`
- native: Python implementation using `lz4` and bitfield expansion to `(x,y,z)` points

### Selecting Decoder

```python
# After conn.connect()
# Default is libvoxel; to switch:
conn.datachannel.set_decoder('native')  # or 'libvoxel'
```

### Payload Handling

Binary messages arriving on the data channel carry a compact header. The driver splits JSON header and binary body, decodes the body via the selected decoder, and inserts results back under `message['data']['data']`.

- `libvoxel` returns a dict with counts and raw arrays: `positions`, `uvs`, `indices`
- `native` returns `points` as an `(N,3)` array in world units

### Subscribing to LiDAR Topics

Relevant topics in `RTC_TOPIC` include:
- `ULIDAR`, `ULIDAR_ARRAY`, `ULIDAR_STATE`, `ROBOTODOM`, mapping/localization topics

Example subscription:
```python
from go2_webrtc_driver.constants import RTC_TOPIC

# Subscribe to voxel map updates
conn.datachannel.pub_sub.subscribe(RTC_TOPIC['ULIDAR'], lambda msg: handle_lidar(msg['data']['data']))
```

### Performance Notes

- `libvoxel` WASM path is efficient but requires `wasmtime` and the `libvoxel.wasm` asset to be accessible at runtime.
- The native path avoids WASM but performs more CPU work in Python; suitable for simpler or offline processing.
