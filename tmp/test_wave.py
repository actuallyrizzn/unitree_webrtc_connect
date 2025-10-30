import builtins as _builtins
import re as _re
_builtin_print = _builtins.print
emoji_pattern = _re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF]+', flags=_re.UNICODE)
def no_emoji_print(*args, **kwargs):
    args = tuple(emoji_pattern.sub('', str(a)) for a in args)
    return _builtin_print(*args, **kwargs)
_builtins.print = no_emoji_print

import asyncio
import logging
import json
import sys
import time
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC, SPORT_CMD

import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
_orig_wait_datachannel_open = _webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open

async def _patched_wait_datachannel_open(self, timeout=5):
    """For LocalAP connections, skip validation and just wait for readyState == 'open'."""
    # Check if this is a LocalAP connection (direct IP, no validation required)
    if self.conn.connectionMethod == WebRTCConnectionMethod.LocalAP:
        start = time.time()
        while time.time() - start < timeout:
            if getattr(self.channel, "readyState", None) == "open":
                # Set flag and start services (like validation callback does)
                self.data_channel_opened = True
                self.heartbeat.start_heartbeat()
                self.rtc_inner_req.network_status.start_network_status_fetch()
                _builtin_print("Data channel ready (LocalAP - validation skipped)")
                return
            await asyncio.sleep(0.1)
        raise asyncio.TimeoutError(f"Data channel readyState did not become 'open' within {timeout}s")
    else:
        # For Remote/LocalSTA, use original behavior (wait for validation)
        return await _orig_wait_datachannel_open(self, timeout)

_webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open = _patched_wait_datachannel_open

import go2_webrtc_driver.unitree_auth as _unitree_auth
_orig_send_local = _unitree_auth.send_sdp_to_local_peer

def _patched_send_sdp(ip, sdp):
    result = _orig_send_local(ip, sdp)
    if result:
        _builtin_print("Received SDP answer (first 300 chars):", str(result)[:300])
    else:
        _builtin_print("No SDP answer received")
    return result

_unitree_auth.send_sdp_to_local_peer = _patched_send_sdp

import aioice.ice as _aioice_ice

_original_get_host_addresses = _aioice_ice.get_host_addresses

def _filtered_get_host_addresses(use_ipv4: bool, use_ipv6: bool):
    addresses = _original_get_host_addresses(use_ipv4, use_ipv6)
    filtered = [addr for addr in addresses if addr.startswith('192.168.')]
    _builtin_print("Host addresses:", addresses, "Filtered:", filtered)
    return filtered if filtered else addresses

_aioice_ice.get_host_addresses = _filtered_get_host_addresses

def print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")

import go2_webrtc_driver.util as _util
_util.print_status = print_status

logging.basicConfig(level=logging.DEBUG)
logger_names = ["aiortc.rtcsctptransport", "aiortc.rtcdatachannel", "aiortc.rtcdtlstransport"]
for name in logger_names:
    logging.getLogger(name).setLevel(logging.DEBUG)
    
async def main():
    try:
        print("="*60)
        print("GO2 WAVE TEST - Extended Timeout Version")
        print("="*60)
        print("\nIMPORTANT: Make sure the Unitree Go2 mobile app is CLOSED")
        print("           The Go2 can only handle one WebRTC connection.\n")
        
        conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
        
        print("Connecting to Go2 (timeout: 60 seconds)...")
        print("This may take a while as ICE negotiation completes...")
        try:
            await asyncio.wait_for(conn.connect(), timeout=60.0)
        except asyncio.TimeoutError:
            print("\nERROR: Connection timed out after 60 seconds")
            print("\nTroubleshooting:")
            print("  1. Make sure the Unitree Go2 mobile app is completely closed")
            print("  2. Try restarting the Go2 robot")
            print("  3. Reconnect to the Go2's WiFi network")
            sys.exit(1)
        
        print("\n" + "="*60)
        print("CONNECTION SUCCESSFUL!")
        print("="*60)
        
        print(f"Data channel readyState right after connect: {getattr(conn.datachannel.channel, 'readyState', None)}")

        print("pc.sctp:", conn.pc.sctp)
        if conn.pc.sctp:
            dtls_state = getattr(conn.pc.sctp.transport, 'state', None)
            print("DTLS transport state:", dtls_state)

        if conn.pc.sctp:
            print("pc.sctp.transport:", getattr(conn.pc.sctp, 'transport', None))

        sctp_obj = conn.pc.sctp
        if sctp_obj is not None:
            assoc_state = getattr(sctp_obj, '_association_state', None)
            print("SCTP association state enum:", assoc_state)

        if conn.pc.localDescription:
            print("Local SDP snippet:")
            print('\n'.join(conn.pc.localDescription.sdp.splitlines()[:20]))
            for line in conn.pc.localDescription.sdp.splitlines():
                if line.startswith('m=application') or 'webrtc-datachannel' in line:
                    print('LOCAL:', line)
        if conn.pc.remoteDescription:
            print("Remote SDP snippet:")
            print('\n'.join(conn.pc.remoteDescription.sdp.splitlines()[:20]))
            for line in conn.pc.remoteDescription.sdp.splitlines():
                if line.startswith('m=application') or 'webrtc-datachannel' in line:
                    print('REMOTE:', line)

        print("\nTesting data channel...")
        try:
            response = await asyncio.wait_for(
                conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["MOTION_SWITCHER"], 
                    {"api_id": 1001}
                ),
                timeout=15.0
            )
            
            if response and 'data' in response:
                data = json.loads(response['data']['data'])
                current_mode = data.get('name', 'unknown')
                print(f"Current motion mode: {current_mode}")
                
                if current_mode != "normal":
                    print(f"Switching to normal mode...")
                    await conn.datachannel.pub_sub.publish_request_new(
                        RTC_TOPIC["MOTION_SWITCHER"], 
                        {"api_id": 1002, "parameter": {"name": "normal"}}
                    )
                    await asyncio.sleep(5)
                
        except asyncio.TimeoutError:
            print("WARNING: Data channel test timed out, but will try wave command anyway...")
        
        print("\nSending wave/hello command...")
        try:
            await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"], 
                {"api_id": SPORT_CMD["Hello"]}
            )
            
            print("\n" + "="*60)
            print("SUCCESS! Go2 should be waving now!")
            print("="*60)
            print("\nYour Go2 connection is working and ready!")
            print("You can now write your code.\n")
            
        except Exception as e:
            print(f"Wave command error: {e}")
        
        await asyncio.sleep(5)
        await conn.disconnect()
        print("Test complete.\n")
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
