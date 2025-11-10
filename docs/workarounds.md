# Workarounds and Library Limitations

This document catalogs issues discovered in the original `go2_webrtc_connect` library and the workarounds implemented in our `tmp/` scripts.

## Overview

The `go2_webrtc_connect` library is a reverse-engineered implementation of the Unitree Go2 mobile app's WebRTC protocol. While it provides a working foundation, several components don't work as expected or require patches to function correctly.

## Critical Issues and Workarounds

### 1. SDP Format Compatibility (RFC 8841 vs Legacy)

**Problem:**
- The Go2 robot sends SDP answers in RFC 8841 format (using `a=sctp-port:5000`)
- The `aiortc` library expects legacy format (using `a=sctpmap:5000 webrtc-datachannel 65535`)
- Without conversion, the data channel fails to establish

**Workaround:**
All scripts in `tmp/` include SDP rewriting patches:

```python
def _rewrite_sdp_to_legacy(sdp: str) -> str:
    """Rewrite SDP from RFC 8841 format to legacy format for aiortc compatibility."""
    # Converts:
    # - m=application <port> UDP/DTLS/SCTP webrtc-datachannel
    # - a=sctp-port:5000
    # To:
    # - m=application <port> UDP/DTLS/SCTP 5000
    # - a=sctpmap:5000 webrtc-datachannel 65535
```

**Files affected:**
- `tmp/min_connect_status.py`
- `tmp/sit-stand.py`
- `tmp/lidar2/app.py`
- `tmp/lidar/app.py`
- `tmp/plot_lidar.py`

**Key insight:** The remote SDP answer should NOT be rewritten - only the local offer needs conversion.

---

### 2. DTLS Fingerprint Compatibility

**Problem:**
- `aiortc` generates SDP offers with multiple fingerprint algorithms (sha-256, sha-384, sha-512)
- The Go2 robot may reject offers with sha-384 or sha-512 fingerprints
- This causes SDP exchange failures

**Workaround:**
Strip sha-384 and sha-512 fingerprints from the SDP offer:

```python
filtered = [
    line for line in offer_sdp.splitlines()
    if not line.startswith("a=fingerprint:sha-384")
    and not line.startswith("a=fingerprint:sha-512")
]
```

**Files affected:**
- All scripts with SDP patches (see above)

---

### 3. Data Channel Timeout Too Short

**Problem:**
- The library's `wait_datachannel_open()` has a default 5-second timeout
- SCTP association establishment can take 10-30 seconds
- Premature timeout causes connection failures

**Workaround:**
Extended timeout to 30 seconds with better logging:

```python
async def _patched_wait_datachannel_open(self, timeout=5):
    """Extended wait for data channel with better logging."""
    deadline = time.time() + 30.0
    # ... polling logic with 2-second log intervals ...
```

**Files affected:**
- All scripts in `tmp/` that establish WebRTC connections

---

### 4. Broken API: GetState (API ID 1034)

**Problem:**
- `SPORT_CMD["GetState"]` (API ID 1034) is defined in `constants.py`
- When called, returns empty data: `{"data": ""}`
- Cannot be used to determine robot state

**Workaround:**
Subscribe to `LF_SPORT_MOD_STATE` topic instead and extract state from streaming messages:

```python
# DON'T USE THIS (doesn't work):
response = await conn.datachannel.pub_sub.publish_request_new(
    RTC_TOPIC["SPORT_MOD"],
    {"api_id": SPORT_CMD["GetState"]}  # Returns empty data
)

# USE THIS INSTEAD:
conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], callback)
# Extract body_height from message['data']['body_height']
```

**Files affected:**
- `tmp/sit-stand.py` - Uses subscription-based state detection
- `tmp/min_connect_status.py` - Attempts GetState but falls back to StandUp

**Evidence:**
- `tmp/min_connect_status.py` line 239-255: GetState returns empty data
- `tmp/sit-stand.py` line 201-256: Full workaround implementation

---

### 5. Broken API: GetBodyHeight (API ID 1024)

**Problem:**
- `SPORT_CMD["GetBodyHeight"]` (API ID 1024) is defined in `constants.py`
- When called, returns error code 3203 (not available/not supported)
- Cannot be used to query robot body height

**Workaround:**
Same as GetState - subscribe to `LF_SPORT_MOD_STATE` and extract `body_height` from messages:

```python
# DON'T USE THIS (returns error 3203):
response = await conn.datachannel.pub_sub.publish_request_new(
    RTC_TOPIC["SPORT_MOD"],
    {"api_id": SPORT_CMD["GetBodyHeight"]}  # Error: code 3203
)

# USE THIS INSTEAD:
# Subscribe to LF_SPORT_MOD_STATE and read body_height from messages
```

**Files affected:**
- `tmp/sit-stand.py` - Uses subscription-based body height detection

**Evidence:**
- `tmp/sit-stand.py` line 204-205: Comment documents this limitation
- Test output shows: `Warning: GetBodyHeight returned error: {'code': 3203}`

---

### 6. Legacy SDP Endpoint Deprecation (Port 8081)

**Problem:**
- The library tries to use legacy endpoint `http://192.168.12.1:8081/offer` first
- This endpoint is no longer available (connection refused)
- Must fall back to encrypted endpoint on port 9991

**Workaround:**
The library's fallback mechanism works, but we've added logging to track this:

```python
# In _patched_send_sdp:
# Library tries 8081 first, fails, then falls back to 9991
# We log both attempts for debugging
```

**Files affected:**
- All connection scripts (library handles this automatically)

**Evidence:**
- `tmp/check_go2_status.py` - Tests both endpoints
- Connection logs show: `ERROR: ... Failed to establish a new connection: [WinError 10061]`

---

### 7. Windows Terminal Emoji Rendering Issues

**Problem:**
- The library uses emoji characters in status messages (✓, ✗, ⚫, etc.)
- Windows terminals (PowerShell, CMD) don't render these correctly
- Causes garbled output and potential encoding errors

**Workaround:**
Monkey-patch `print_status` to strip emojis:

```python
import builtins as _builtins
import re as _re

_builtin_print = _builtins.print
emoji_pattern = _re.compile(r'[\U0001F300-\U0001FAD6...]+', flags=_re.UNICODE)

def _no_emoji_print(*args, **kwargs):
    args = tuple(emoji_pattern.sub('', str(a)) for a in args)
    return _builtin_print(*args, **kwargs)

_builtins.print = _no_emoji_print

# Also patch util.print_status
def _patched_print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")

_util.print_status = _patched_print_status
```

**Files affected:**
- All scripts in `tmp/`

---

### 8. Windows CSV Field Size Limit

**Problem:**
- Python's `csv.field_size_limit()` on Windows has a maximum of `sys.maxsize`
- `sys.maxsize` on 64-bit Windows is too large for C long type
- Causes `OverflowError: Python int too large to convert to C long`

**Workaround:**
Catch the overflow and use a safe maximum:

```python
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2147483647)  # 2^31 - 1 (safe maximum)
```

**Files affected:**
- `tmp/lidar2/app.py` line 68-71

---

### 9. Connection State Monitoring

**Problem:**
- The library doesn't provide robust connection state monitoring
- Dead connections can appear active
- No timeout detection for message streams

**Workaround:**
Implement custom connection monitoring:

```python
# Check peer connection state
if conn.pc.connectionState != 'connected':
    # Handle disconnection

# Monitor last message time
if time.time() - last_message_time > 10.0:
    # Connection appears dead, trigger reconnect
```

**Files affected:**
- `tmp/lidar2/app.py` - Implements connection state checks and message timeout detection

---

### 10. Keepalive Mechanism

**Problem:**
- WebRTC connections can timeout after ~60 seconds of inactivity
- The library doesn't provide active keepalive
- Connections drop silently

**Workaround:**
Periodically call `disableTrafficSaving(True)` to keep connection alive:

```python
# Every 20 seconds
await conn.datachannel.disableTrafficSaving(True)
```

**Files affected:**
- `tmp/lidar2/app.py` - Implements periodic keepalive

---

## Summary of Broken/Unreliable Features

| Feature | Status | Workaround |
|---------|--------|------------|
| `GetState` API (1034) | ❌ Broken (empty data) | Subscribe to `LF_SPORT_MOD_STATE` |
| `GetBodyHeight` API (1024) | ❌ Broken (error 3203) | Subscribe to `LF_SPORT_MOD_STATE` |
| Legacy SDP endpoint (8081) | ❌ Deprecated | Use encrypted endpoint (9991) |
| RFC 8841 SDP format | ⚠️ Incompatible | Rewrite to legacy format |
| Data channel timeout | ⚠️ Too short | Extend to 30 seconds |
| DTLS fingerprints | ⚠️ Rejected | Strip sha-384/sha-512 |
| Emoji output | ⚠️ Windows issues | Strip emojis |
| Connection monitoring | ⚠️ Limited | Implement custom checks |
| Keepalive | ⚠️ Missing | Periodic `disableTrafficSaving()` |

## Working Features

These features work reliably without workarounds:

- ✅ `StandUp` (1004), `StandDown` (1005), `Sit` (1009) commands
- ✅ `Hello` (wave) command (1016)
- ✅ Pub/Sub subscriptions (`LF_SPORT_MOD_STATE`, `ULIDAR`, etc.)
- ✅ Video and audio channels
- ✅ LiDAR data streaming
- ✅ Connection establishment (with SDP patches)
- ✅ Data channel validation

## Recommendations

1. **Always use SDP patches** - Required for any WebRTC connection
2. **Use subscriptions instead of GetState/GetBodyHeight** - The APIs are broken
3. **Implement connection monitoring** - Library doesn't detect dead connections
4. **Add keepalive** - Prevents silent disconnections
5. **Handle Windows-specific issues** - Emoji stripping, CSV limits

## Future Considerations

- The library may fix some of these issues in future versions
- Unitree firmware updates may change protocol behavior
- Consider contributing fixes upstream to `legion1581/go2_webrtc_connect`

