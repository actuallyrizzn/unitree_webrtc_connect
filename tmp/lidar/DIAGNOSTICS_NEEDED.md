# Diagnostics Needed - 40 Second Disconnect Issue

## Current Problems

1. **Consistent 36-40s disconnects** (too regular to be random)
2. **20-second lag** between real world → browser
3. **2 FPS browser** (low performance)
4. **Auto-reconnect is hit or miss**

## Changes Made for Diagnostics

### Server-Side (app.py)
- ✅ **Removed throttling** (sending every frame instead of every 2nd)
- ✅ **Added emit timing** logs every 20 messages
- ✅ **Added timestamps** to measure end-to-end lag
- ✅ **Added payload size** logging

### Client-Side (lidar_viewer.js)
- ✅ **Added lag measurement** (server time → browser time)
- ✅ **Added payload logging** every 20 messages
- ✅ **Logs show**: Message#, point count, payload KB, LAG in ms

## What To Look For

### 1. Server Terminal Output

**Every 20 messages you'll see:**
```
Message 20: Raw positions=1572780, Parsed points=524260
  After rotation: 524260 points, After Y-filter [0,100]: 524120 points
  Downsampled: 524120 -> 15416 points
  Emit: 2.3ms, Payload: 185.6KB   ← NEW: Shows emit time and payload size
```

**What to check:**
- Is `Emit` time < 10ms? (Good if yes)
- Is payload ~ 185KB? (Binary is working if yes)
- Does emit time grow over time? (Indicates buffer buildup if yes)

### 2. Browser Console Output

**Every 20 messages you'll see:**
```
Msg 20: 15416 pts, 185.6KB, LAG: 45ms   ← NEW: End-to-end lag measurement
```

**What to check:**
- Is LAG < 100ms initially? (Good if yes)
- Does LAG grow over time? (Major problem if yes - indicates buffer buildup)
- Does LAG = 20 seconds? (Would explain your observation)

### 3. Before Disconnect

**Watch the last 10 messages before disconnect:**
```
Message 326: ...
  Emit: 2.3ms, Payload: 185.6KB
Message 336: ...
  Emit: 5.8ms, Payload: 185.6KB   ← Emit time growing?
[DISCONNECT]
```

**Key questions:**
- Does emit time increase before disconnect?
- Do we get any errors before disconnect?
- Is there a pattern at message #40? #80? #120?

## Theories About 40-Second Disconnect

### Theory 1: DTLS Session Timeout
**Evidence Needed:**
- Check if disconnect happens at exactly 40s every time
- Look for DTLS-related errors in logs

**Possible Fix:**
```python
# In webrtc_driver.py, increase DTLS timeout
# (Need to investigate aiortc DTLS configuration)
```

### Theory 2: ICE Connection Degradation
**Evidence Needed:**
- Check if ICE state changes before disconnect
- Look for "ICE Connection State: failed" before disconnect

**Possible Fix:**
```python
# Add ICE restart logic
await pc.restartIce()
```

### Theory 3: Go2 Firmware Timeout
**Evidence Needed:**
- Consistent 40s regardless of data volume
- No errors on our side before disconnect

**Possible Fix:**
- Send periodic command to keep connection "active"
```python
# Every 20 seconds, send a harmless command
asyncio.create_task(self.send_keepalive_command())
```

### Theory 4: Buffer Overflow Causing Crash
**Evidence Needed:**
- LAG grows from 50ms → 1000ms → 20000ms → disconnect
- Emit time grows from 2ms → 5ms → 50ms → disconnect

**Possible Fix:**
- Drop frames when buffer is full (already planned)
- Increase buffer sizes even more
- Separate viz from AI processing (Phase 2)

## Testing Protocol

### Run 1: Baseline Measurement
1. **Start server** with diagnostic code
2. **Open browser console** (F12)
3. **Let run for 2 minutes** (should disconnect once or twice)
4. **Record:**
   - Initial LAG value (from browser console)
   - LAG value at 30 seconds
   - LAG value just before disconnect
   - Emit time at start vs end
   - Exact uptime when disconnect occurs

### Run 2: Watch Network Tab
1. **Open browser DevTools** → Network tab → Filter: WS (WebSocket)
2. **Watch frame sizes** in WebSocket connection
3. **Look for:**
   - Frame size consistency (~185KB)
   - Frame rate (should be ~2-3/second)
   - Any error messages
   - Connection close reason

### Run 3: Monitor aiortc Logs
1. **Enable aiortc debug logging:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger('aiortc').setLevel(logging.DEBUG)
```
2. **Look for:**
   - DTLS handshake messages
   - ICE state changes
   - SCTP errors
   - Buffer warnings

## What We Expect to Find

### Scenario A: Buffer Buildup (Most Likely)
```
Message 20: LAG: 50ms, Emit: 2.1ms
Message 40: LAG: 150ms, Emit: 2.3ms
Message 60: LAG: 450ms, Emit: 2.8ms
Message 80: LAG: 1200ms, Emit: 4.5ms
Message 100: LAG: 3500ms, Emit: 12.8ms
Message 120: LAG: 8200ms, Emit: 35.2ms
Message 140: LAG: 18500ms, Emit: 120.5ms
[DISCONNECT - buffers full, connection collapses]
```

**Solution:** Aggressive frame dropping, separate AI path

### Scenario B: DTLS Timeout (Less Likely)
```
Message 20: LAG: 50ms, Emit: 2.1ms
Message 40: LAG: 55ms, Emit: 2.1ms  ← Consistent, no buildup
Message 60: LAG: 52ms, Emit: 2.2ms
Message 80: LAG: 48ms, Emit: 2.0ms
[DISCONNECT at exactly 40.0s - external timeout]
```

**Solution:** Find DTLS config, increase timeout, add ICE restart

### Scenario C: Go2 Firmware Limit (Possible)
```
Message 20: LAG: 50ms, Emit: 2.1ms
Message 40: LAG: 48ms, Emit: 2.1ms
[DISCONNECT at exactly 40.0s every time]
[Robot sends close frame]
```

**Solution:** Send periodic keepalive command, investigate firmware docs

## Immediate Next Steps

1. **Restart server** with diagnostic code
2. **Open browser** and console (F12)
3. **Record output** for 2 minutes
4. **Share the logs:**
   - Server terminal: Last 50 lines before disconnect
   - Browser console: All log messages
   - Browser Network tab: WebSocket frame pattern

5. **Answer these questions:**
   - What's the initial LAG value?
   - Does LAG grow over time or stay constant?
   - What's the emit time pattern?
   - Is disconnect at exactly 40s or does it vary (38-42s)?

## Once We Have Data

Based on the diagnostics, we'll implement one of these solutions:

### If Buffer Buildup:
- Implement aggressive frame dropping
- Add backpressure detection
- Separate AI processing path
- Consider switching to UDP-based protocol

### If DTLS/ICE Timeout:
- Configure aiortc DTLS session timeout
- Implement ICE restart on connection degradation
- Add periodic ICE connectivity checks

### If Go2 Firmware Limit:
- Send periodic no-op commands to keep session alive
- Investigate Go2 WebRTC firmware documentation
- Consider maintaining multiple connections

## Expected Resolution Timeline

- **Diagnostics:** 10 minutes of testing
- **Root cause identified:** Based on logs
- **Fix implemented:** 30-60 minutes
- **Testing fix:** 5 minutes to verify
- **Documentation:** Update with solution

Let's run these diagnostics and get to the bottom of this!


