## Data Channel

The data channel is the primary control and telemetry interface. It implements:
- Validation handshake with the robot
- Pub/Sub for topics
- Request/Response (API calls) with correlation
- Heartbeat
- Chunked payloads and file transfer helpers
- LiDAR binary payload decoding glue

### Concepts

- Message `type` values are enumerated in `DATA_CHANNEL_TYPE` (e.g., `validation`, `heartbeat`, `rtc_inner_req`, `msg`, `request`, `response`, `vid`, `aud`, error types, etc.).
- Topics for subscriptions and requests are enumerated in `RTC_TOPIC` (e.g., `rt/lf/lowstate`, `rt/api/sport/request`, `rt/api/audiohub/request`, LiDAR topics, etc.).
- API IDs for specific subsystems (e.g., `AUDIO_API`, `SPORT_CMD`) are defined in `constants.py`.

### Usage Patterns

Subscribe to topic updates:
```python
from go2_webrtc_driver.constants import RTC_TOPIC

# After conn.connect()

def on_lowstate(message):
    lowstate = message['data']
    # ...handle fields...

conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LOW_STATE'], on_lowstate)
```

Publish a request and await response:
```python
response = await conn.datachannel.pub_sub.publish_request_new(
    RTC_TOPIC['SPORT_MOD'],
    {
        'api_id': 1008,  # SPORT_CMD['Move']
        'parameter': {'x': 0.5, 'y': 0, 'z': 0}
    }
)
```

Fire-and-forget control (e.g., enable video):
```python
conn.datachannel.switchVideoChannel(True)
```

### Validation Handshake

- Upon data channel open, the robot first sends a validation requirement.
- Driver calculates and sends a derived token (`MD5('UnitreeGo2_' + key)` then base64) back.
- When validation succeeds, registered callbacks fire and the driver:
  - Starts heartbeat
  - Starts network status polling (via `rtc_inner_req`)

### Heartbeat

- A short status payload is published every ~2 seconds to keep the session alive and check reachability.
- Responses update last-seen times.

### Correlation and Chunking

- `FutureResolver` constructs keys from `(type, topic, uuid)` where `uuid` is drawn from multiple possible fields (e.g., `data.uuid`, `header.identity.id`, `info.uuid`, `info.req_uuid`).
- Chunked payloads:
  - Generic chunked data: `content_info.enable_chunking` with `chunk_index/total_chunk_num` enables assembling buffers before resolving.
  - File transfers (`rtc_inner_req`): `info.file` with chunk semantics assembled to a final byte payload.

### File Transfers

- Upload (`WebRTCDataChannelFileUploader`): base64 file content sliced into chunks; progress callback supported; cancel supported.
- Download (`WebRTCDataChannelFileDownloader`): requests file and assembles chunks; returns decoded bytes.

### LiDAR Payloads

- Binary `bytes` messages are interpreted using a small header to distinguish LiDAR vs normal payload format.
- Decoding is delegated to `UnifiedLidarDecoder` (see [LiDAR](./07-lidar.md)).

### Errors

- Error events from robot are mapped to readable text using `app_error_messages` and logged/printed.
