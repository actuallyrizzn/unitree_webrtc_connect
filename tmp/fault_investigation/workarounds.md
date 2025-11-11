# Sensor Monitor Stability Workarounds

This document catalogs the critical learnings from debugging the Go2 sensor monitor TUI, which was experiencing connection failures after 30-45 seconds. These workarounds are essential for building stable, long-running WebRTC applications with the `go2_webrtc_connect` library.

## Critical Issues and Solutions

### 1. **Non-Blocking Callbacks Are Mandatory**

**Problem:**
- WebRTC callbacks (`lowstate_callback`, `sportmode_callback`) were blocking the event loop
- This caused connection stalls, missed keepalives, and eventual disconnection after 30-45 seconds
- The connection would appear "connected" but stop receiving data

**Solution:**
Wrap all callbacks in `asyncio.create_task()` to make them non-blocking:

```python
def lowstate_callback(message: Dict[str, Any]) -> None:
    """Callback must be non-blocking."""
    asyncio.create_task(process_lowstate_async(message))

async def process_lowstate_async(message: Dict[str, Any]) -> None:
    """Actual processing happens here."""
    # Update data structures, track bandwidth, etc.
    pass

# Subscribe with the non-blocking wrapper
conn.datachannel.pub_sub.subscribe(
    RTC_TOPIC["LOW_STATE"],
    lowstate_callback,
)
```

**Key Insight:** The `aiortc` library's event loop is single-threaded. Any blocking operation in a callback will freeze the entire connection.

**Reference:** This pattern was discovered by examining the stable `lidar2/app.py` implementation.

---

### 2. **Keepalive Mechanism is Critical**

**Problem:**
- Without regular keepalive signals, the Go2 robot's "traffic saving" mode kicks in
- This causes the connection to silently degrade and eventually disconnect
- Connection state would show "connected" but `iceConnectionState` would be "closed"

**Solution:**
1. Send `disableTrafficSaving(True)` immediately after connection
2. Send keepalive every 15-20 seconds (matching `lidar2` implementation)
3. Continue sending keepalive even if datachannel appears "connecting" (don't fail on errors)

```python
# Immediately after connection
await conn.datachannel.disableTrafficSaving(True)

# In monitoring loop
keepalive_interval = 20.0  # seconds
last_keepalive = time.time()

while True:
    await asyncio.sleep(1.0)
    now = time.time()
    
    if now - last_keepalive >= keepalive_interval:
        try:
            await conn.datachannel.disableTrafficSaving(True)
            last_keepalive = now
        except Exception as exc:
            # Log but don't break - keep trying
            logging.warning("Keepalive failed: %s", exc)
```

**Key Insight:** The Go2 robot has an internal timeout mechanism. Regular keepalive signals prevent this timeout from triggering.

---

### 3. **Connection State Monitoring Must Check Multiple States**

**Problem:**
- `conn.isConnected` can report `True` even when the connection is dead
- `iceConnectionState` is the most reliable indicator of actual connectivity
- Need to monitor multiple states to detect failures early

**Solution:**
Monitor all available connection states and treat `iceConnectionState == "closed"` as a hard failure:

```python
def get_datachannel_state(conn: Go2WebRTCConnection) -> str:
    """Get the actual RTCDataChannel readyState."""
    try:
        dc = conn.datachannel._datachannel
        if dc:
            return dc.readyState
    except Exception:
        pass
    return "unknown"

# In monitoring loop
pc = conn._pc  # RTCPeerConnection
ice_state = getattr(pc, "iceConnectionState", "unknown")
pc_state = getattr(pc, "connectionState", "unknown")
sig_state = getattr(pc, "signalingState", "unknown")
dc_state = get_datachannel_state(conn)
is_connected = getattr(conn, "isConnected", False)

# Hard failure detection
if ice_state == "closed" or ice_state == "failed":
    raise ConnectionError(f"ICE connection lost: {ice_state}")
```

**Key Insight:** The library's `isConnected` property is not reliable. Always check `iceConnectionState` directly.

---

### 4. **Memory Accumulation Kills Performance**

**Problem:**
- Unbounded data structures (lists, dicts) grow indefinitely
- Bandwidth tracking accumulated samples without cleanup
- Error lists grew without bounds
- This caused gradual slowdown and eventual freeze

**Solution:**
Use capped data structures and aggressive cleanup:

```python
from collections import deque

# Capped deques for buffers
bandwidth_samples = deque(maxlen=80)  # ~8 seconds at 10 Hz
errors = deque(maxlen=100)  # Keep last 100 errors only

# Latest data only - overwrite, don't accumulate
latest_data: Dict[str, Any] = {
    "lowstate": None,  # Overwrite on each update
    "sportmode": None,
}

# Bandwidth calculation with time window
def get_bandwidth_kbps() -> float:
    with bandwidth_lock:
        samples = list(bandwidth_samples)
    
    if len(samples) < 2:
        return 0.0
    
    # Only use samples from last 2 seconds
    now = time.time()
    recent = [(t, b) for t, b in samples if now - t <= 2.0]
    
    if len(recent) < 2:
        return 0.0
    
    start_time = recent[0][0]
    end_time = recent[-1][0]
    total_bytes = sum(b for _, b in recent)
    
    if end_time > start_time:
        bytes_per_sec = total_bytes / (end_time - start_time)
        return (bytes_per_sec * 8) / 1024.0
    
    return 0.0
```

**Key Insight:** Never accumulate data without bounds. Always use `deque(maxlen=N)` or implement time-based cleanup.

---

### 5. **File I/O Can Kill Performance**

**Problem:**
- Writing every log message to disk with immediate flush causes high I/O
- This can block the event loop and cause connection stalls
- Disk writes were happening even when not needed

**Solution:**
Make file logging strictly opt-in and use direct writes (no complex buffering):

```python
log_file_handle = None
LOG_ENABLED = False

def log_to_file(message: str) -> None:
    """Write to file only if logging is enabled."""
    if LOG_ENABLED and log_file_handle:
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_file_handle.write(f"[{timestamp}] {message}\n")
            log_file_handle.flush()  # Direct write, no complex buffer
        except Exception:
            pass

# Only enable if flag is set
if args.log_file:
    enable_file_logging(args.log_file)
```

**Key Insight:** File I/O should be opt-in, not default. When enabled, keep it simple and direct.

---

### 6. **Textual TUI Layout Structure Matters**

**Problem:**
- Wrapping all widgets in an extra `Container` prevents them from rendering
- The TUI would show a blank screen even though widgets were created

**Solution:**
Use direct widget composition without unnecessary nesting:

```python
def compose(self) -> ComposeResult:
    """Create child widgets - direct composition."""
    yield Header(show_clock=True)
    yield Static("GO2 SENSOR MONITOR", id="header")
    yield Horizontal(
        Vertical(
            # Left panel widgets
            id="motors-panel",
        ),
        Vertical(
            # Right panel widgets
            id="info-panel",
        ),
    )
    yield Footer()
```

**Key Insight:** Textual requires widgets to be direct children of the screen or properly nested containers. Extra wrapper containers break rendering.

---

### 7. **Simplification Wins - Remove Unnecessary Complexity**

**Problem:**
- Database logging added overhead and complexity
- Complex UI update mechanisms (`update_cell`) were unreliable
- Multiple data stores caused synchronization issues

**Solution:**
Simplify to the minimum viable implementation:

1. **Remove database** - Use in-memory storage only
2. **Simple polling** - Poll at 2 Hz (0.5s interval), update widgets directly
3. **Single data store** - One `latest_data` dict, overwrite on each update
4. **Direct widget updates** - Use `clear()` and `add_columns()` for tables (reliable, if slower)

```python
# Simple polling at 2 Hz
self.set_interval(0.5, self.poll_latest_data)

def poll_latest_data(self) -> None:
    """Poll latest data and update UI - non-blocking."""
    with data_lock:
        lowstate = latest_data.get("lowstate")
        # ... update widgets directly
```

**Key Insight:** Complexity is the enemy of stability. Start simple, add features only when needed.

---

### 8. **CLI Version Essential for Debugging**

**Problem:**
- TUI makes it impossible to see what's happening during failures
- Can't easily add debug prints or observe connection state changes
- TUI overhead masks underlying issues

**Solution:**
Create a non-Textual CLI version for debugging:

```python
# sensor_monitor_cli.py - prints status to stdout
async def run_cli_monitor(args):
    while True:
        # Connection logic
        # Print status updates to stdout
        logging.info(f"ICE: {ice_state}, PC: {pc_state}, Uptime: {uptime:.1f}s")
```

**Key Insight:** Always have a non-UI version for debugging. The CLI version was crucial for isolating the keepalive and callback blocking issues.

---

### 9. **Windows Terminal Encoding Issues**

**Problem:**
- Emoji characters in status messages cause `'charmap' codec can't encode character` errors
- Windows terminals default to non-UTF-8 encoding

**Solution:**
1. Reconfigure stdout/stderr to UTF-8 on Windows
2. Strip emojis from status messages

```python
# Set UTF-8 encoding for stdout/stderr on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

# Strip emojis from status messages
emoji_pattern = re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF...]+', flags=re.UNICODE)

def _patched_print_status(status_type: str, status_message: str) -> None:
    clean_type = emoji_pattern.sub('', str(status_type))
    clean_msg = emoji_pattern.sub('', str(status_message))
    print(f"{clean_type}: {clean_msg}")
```

**Key Insight:** Always handle Windows encoding issues proactively. Emojis are nice but not worth breaking the app.

---

### 10. **Infinite Retry with Exponential Backoff**

**Problem:**
- Connection failures should trigger automatic reconnection
- Need to avoid hammering the robot with rapid reconnection attempts
- Should match the pattern used in stable implementations

**Solution:**
Implement infinite retry loop with exponential backoff:

```python
retry_count = 0
consecutive_fast_failures = 0
base_delay = 2.0
max_delay = 60.0

while True:
    try:
        # Attempt connection
        conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
        await conn.connect()
        # ... connection established, monitoring loop ...
        
    except Exception as exc:
        retry_count += 1
        
        # Exponential backoff
        if consecutive_fast_failures > 3:
            delay = min(base_delay * (2 ** min(retry_count, 5)), max_delay)
        else:
            delay = base_delay
            consecutive_fast_failures += 1
        
        logging.warning(f"Connection failed (attempt #{retry_count}), retrying in {delay:.1f}s...")
        await asyncio.sleep(delay)
        continue
```

**Key Insight:** Match the retry pattern from stable implementations (`lidar2`). Infinite retry with backoff is the standard approach.

---

## Summary of Best Practices

1. ✅ **Always wrap callbacks in `asyncio.create_task()`** - Never block the event loop
2. ✅ **Send keepalive every 15-20 seconds** - Prevent traffic saving mode
3. ✅ **Monitor `iceConnectionState` directly** - Don't trust `isConnected`
4. ✅ **Use capped data structures** - `deque(maxlen=N)` for all buffers
5. ✅ **Make file logging opt-in** - Don't write to disk by default
6. ✅ **Keep UI updates simple** - Poll at reasonable intervals, update directly
7. ✅ **Remove unnecessary complexity** - Start simple, add features only when needed
8. ✅ **Create CLI version for debugging** - Essential for troubleshooting
9. ✅ **Handle Windows encoding** - UTF-8 + emoji stripping
10. ✅ **Infinite retry with backoff** - Match stable implementation patterns

## Files Reference

- **Stable TUI:** `tmp/fault_investigation/sensor_monitor.py`
- **CLI Debug Version:** `tmp/fault_investigation/sensor_monitor_cli.py`
- **Reference Implementation:** `tmp/lidar2/app.py` (stable long-running connection)

## Testing Protocol

To verify stability:
1. Run CLI version with `--timeout 60` (1 minute)
2. Incrementally increase to `--timeout 600` (10 minutes)
3. Target: **Three consecutive 10-minute runs with no failures**

Success criteria:
- No connection drops
- `iceConnectionState` remains "connected"
- Message rates remain consistent
- No memory growth
- Bandwidth remains stable

