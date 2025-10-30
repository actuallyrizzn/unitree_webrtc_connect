## Extension Guide

This guide shows how to add new topic handlers, new request flows, and extend the driver safely.

### Add a New Subscription Handler

1) Identify the topic (add to `RTC_TOPIC` if needed).
2) Register a callback after `conn.connect()`:
```python
def on_message(msg):
    payload = msg['data']
    # ...

conn.datachannel.pub_sub.subscribe("rt/custom/topic", on_message)
```

### Add a New Request API

- Prefer using `publish_request_new(topic, { api_id, parameter, priority? })` to standardize envelope structure.
- If the API needs a unique ID, `publish_request_new` will generate one when missing.

```python
resp = await conn.datachannel.pub_sub.publish_request_new(
    "rt/api/custom/request",
    {
        "api_id": 9001,
        "parameter": {"foo": "bar"}
    }
)
```

### Correlation Semantics

- The resolver correlates responses based on a key built from `(type, topic, identifier)` where `identifier` is derived from `data.uuid`, `header.identity.id`, `info.uuid`, or `info.req_uuid`.
- When designing new APIs, include one of these fields consistently to leverage automatic correlation.

### Chunked Data

- For large payloads, use `content_info.enable_chunking` with `chunk_index` and `total_chunk_num` in `data`, or file-style chunks inside `info.file` for `rtc_inner_req`.
- The `FutureResolver` will assemble chunks before completing the future.

### Media Channel Toggles

- To wire new robot-side toggles via the data channel, follow the `switchVideoChannel`/`switchAudioChannel` pattern: a simple `publish_without_callback` with `type` set appropriately and data `on`/`off`.

### Where to Put Logic

- If it’s a generic data channel helper, place it under `msgs/`.
- If it’s a new high-level domain helper (like `webrtc_audiohub`), create a new module alongside `webrtc_audiohub.py` and back it with constants.

### Error Handling

- Map new error families in `constants.py` if they have formal codes.
- Use `msgs/error_handler.py` patterns to pretty-print structured errors.

