import builtins as _builtins
import re as _re
_builtin_print = _builtins.print
emoji_pattern = _re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF]+', flags=_re.UNICODE)
def _no_emoji_print(*args, **kwargs):
    args = tuple(emoji_pattern.sub('', str(a)) for a in args)
    return _builtin_print(*args, **kwargs)
_builtins.print = _no_emoji_print

import asyncio
import logging
import sys
import time

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC


# --- aiortc SDP patch: remove sha-384/sha-512 fingerprints to avoid triggering RFC8841 on Go2 ---
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription

    _orig_setLocalDescription = RTCPeerConnection.setLocalDescription

    async def _patched_setLocalDescription(self, description):
        try:
            if (
                description
                and isinstance(description, RTCSessionDescription)
                and description.type == "offer"
                and isinstance(description.sdp, str)
            ):
                filtered = []
                for line in description.sdp.splitlines():
                    if line.startswith("a=fingerprint:sha-384") or line.startswith("a=fingerprint:sha-512"):
                        continue
                    filtered.append(line)
                description = RTCSessionDescription(sdp="\r\n".join(filtered) + "\r\n", type=description.type)
        except Exception:
            # If anything goes wrong, fall back silently to original behavior
            pass
        return await _orig_setLocalDescription(self, description)

    RTCPeerConnection.setLocalDescription = _patched_setLocalDescription
except Exception:
    pass

# --- Increase datachannel wait and accept readyState open as success (non-invasive) ---
try:
    import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel

    _orig_wait_open = _webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open

    async def _patched_wait_datachannel_open(self, timeout=5):
        deadline = time.time() + 30.0  # extend to 30s
        last_log = 0
        while time.time() < deadline:
            if getattr(self, 'data_channel_opened', False):
                return
            rs = getattr(getattr(self, 'channel', None), 'readyState', None)
            if rs == 'open':
                return
            # periodic debug
            if time.time() - last_log > 2:
                print(f"Waiting for datachannel... readyState={rs}")
                last_log = time.time()
            await asyncio.sleep(0.1)
        raise asyncio.TimeoutError("Data channel did not open within 30s")

    _webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open = _patched_wait_datachannel_open
except Exception:
    pass


def _patched_print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")

import go2_webrtc_driver.util as _util
_util.print_status = _patched_print_status


async def main():
    logging.basicConfig(level=logging.INFO)

    print("============================================================")
    print("GO2 MINIMAL CONNECT + STATUS TEST")
    print("============================================================")
    print("Direct AP mode: 192.168.12.1, validating datachannel if requested")

    conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)

    # Connect with timeout
    try:
        await asyncio.wait_for(conn.connect(), timeout=60.0)
    except asyncio.TimeoutError:
        print("ERROR: Connection timed out after 60s")
        sys.exit(1)

    print("Connected. Subscribing to LOW_STATE and waiting for one message...")

    got_message = asyncio.get_event_loop().create_future()

    def _on_low_state(msg):
        if not got_message.done():
            got_message.set_result(msg)

    # Subscribe and wait for first LOW_STATE frame
    conn.datachannel.pub_sub.subscribe(RTC_TOPIC["LOW_STATE"], callback=_on_low_state)

    try:
        message = await asyncio.wait_for(got_message, timeout=15.0)
        # message is already parsed by webrtc_datachannel handlers
        print("LOW_STATE received (truncated):")
        try:
            # Print compactly if structure matches {"data": {...}}
            data = message.get("data", {})
            preview = {k: data.get(k) for k in list(data.keys())[:10]}
            print(preview)
        except Exception:
            print(str(message)[:500])
    except asyncio.TimeoutError:
        print("WARNING: No LOW_STATE received within 15s")

    await conn.disconnect()
    print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)


