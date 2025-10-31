import asyncio
import logging
import json
import sys
import os
from typing import Optional, Dict, Any
import aioice

# Patch aioice Connection class to use random username and password
class Connection(aioice.Connection):
    local_username = aioice.utils.random_string(4)
    local_password = aioice.utils.random_string(22)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.local_username = Connection.local_username
        self.local_password = Connection.local_password

aioice.Connection = Connection  # type: ignore

import aiortc
from packaging import version
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
from aiortc.contrib.media import MediaPlayer
from aiortc.mediastreams import MediaStreamError
from .unitree_auth import send_sdp_to_local_peer, send_sdp_to_remote_peer
from .webrtc_datachannel import WebRTCDataChannel
from .webrtc_audio import WebRTCAudioChannel
from .webrtc_video import WebRTCVideoChannel
from .constants import DATA_CHANNEL_TYPE, WebRTCConnectionMethod
from .util import fetch_public_key, fetch_token, fetch_turn_server_info, print_status
from .multicast_scanner import discover_ip_sn

# Handle aiortc version-specific X509 digest algorithms
ver = version.Version(aiortc.__version__)
if ver == version.Version("1.10.0"):
    X509_DIGEST_ALGORITHMS = {
        "sha-256": "SHA256",
    }
    aiortc.rtcdtlstransport.X509_DIGEST_ALGORITHMS = X509_DIGEST_ALGORITHMS

elif ver >= version.Version("1.11.0"):
    # Syntax changed in aiortc 1.11.0, so we need to use the hashes module
    from cryptography.hazmat.primitives import hashes

    X509_DIGEST_ALGORITHMS = {
        "sha-256": hashes.SHA256(),  # type: ignore
    }
    aiortc.rtcdtlstransport.X509_DIGEST_ALGORITHMS = X509_DIGEST_ALGORITHMS

# # Enable logging for debugging
# logging.basicConfig(level=logging.INFO)

class Go2WebRTCConnection:
    def __init__(self, connectionMethod: WebRTCConnectionMethod, serialNumber=None, ip=None, username=None, password=None) -> None:
        self.pc = None
        self.sn = serialNumber
        self.ip = ip
        self.connectionMethod = connectionMethod
        self.isConnected = False
        self.token = fetch_token(username, password) if username and password else ""

    async def connect(self):
        print_status("WebRTC connection", "🟡 started")
        if self.connectionMethod == WebRTCConnectionMethod.Remote:
            self.public_key = fetch_public_key()
            turn_server_info = fetch_turn_server_info(self.sn, self.token, self.public_key)
            await self.init_webrtc(turn_server_info)
        elif self.connectionMethod == WebRTCConnectionMethod.LocalSTA:
            if not self.ip and self.sn:
                discovered_ip_sn_addresses = discover_ip_sn()
                
                if discovered_ip_sn_addresses:
                    if self.sn in discovered_ip_sn_addresses:
                        self.ip = discovered_ip_sn_addresses[self.sn]
                    else:
                        raise ValueError("The provided serial number wasn't found on the network. Provide an IP address instead.")
                else:
                    raise ValueError("No devices found on the network. Provide an IP address instead.")

            await self.init_webrtc(ip=self.ip)
        elif self.connectionMethod == WebRTCConnectionMethod.LocalAP:
            self.ip = "192.168.12.1"
            await self.init_webrtc(ip=self.ip)
    
    async def disconnect(self):
        if self.pc:
            await self.pc.close()
            self.pc = None
        self.isConnected = False
        print_status("WebRTC connection", "🔴 disconnected")

    async def reconnect(self):
        await self.disconnect()
        await self.connect()
        print_status("WebRTC connection", "🟢 reconnected")

    def create_webrtc_configuration(self, turn_server_info, stunEnable=True, turnEnable=True) -> RTCConfiguration:
        ice_servers = []

        if turn_server_info:
            username = turn_server_info.get("user")
            credential = turn_server_info.get("passwd")
            turn_url = turn_server_info.get("realm")
            
            if username and credential and turn_url:
                if turnEnable:
                    ice_servers.append(
                        RTCIceServer(
                            urls=[turn_url],
                            username=username,
                            credential=credential
                        )
                    )
                if stunEnable:
                    # Use Google's public STUN server
                    stun_url = "stun:stun.l.google.com:19302"
                    ice_servers.append(
                        RTCIceServer(
                            urls=[stun_url]
                        )
                    )
            else:
                raise ValueError("Invalid TURN server information")
        
        configuration = RTCConfiguration(
            iceServers=ice_servers
        )
        
        return configuration

    async def init_webrtc(self, turn_server_info=None, ip=None):
        # Check if aioice returns the same credentials for each instantiation
        # (workaround for a bug in aioice is active)
        from aioice import Connection
        a = Connection(ice_controlling=False)
        b = Connection(ice_controlling=False)
        if a.local_username != b.local_username:
            print("aoice installation/instantiation error. This is not allowed.")
            sys.exit(1)
        
        configuration = self.create_webrtc_configuration(turn_server_info)
        self.pc = RTCPeerConnection(configuration)


        self.datachannel = WebRTCDataChannel(self, self.pc)

        self.audio = WebRTCAudioChannel(self.pc, self.datachannel)
        self.video = WebRTCVideoChannel(self.pc, self.datachannel)

        @self.pc.on("icegatheringstatechange")
        async def on_ice_gathering_state_change():
            state = self.pc.iceGatheringState
            if state == "new":
                print_status("ICE Gathering State", "🔵 new")
            elif state == "gathering":
                print_status("ICE Gathering State", "🟡 gathering")
            elif state == "complete":
                print_status("ICE Gathering State", "🟢 complete")


        @self.pc.on("iceconnectionstatechange")
        async def on_ice_connection_state_change():
            state = self.pc.iceConnectionState
            if state == "checking":
                print_status("ICE Connection State", "🔵 checking")
            elif state == "completed":
                print_status("ICE Connection State", "🟢 completed")
            elif state == "failed":
                print_status("ICE Connection State", "🔴 failed")
            elif state == "closed":
                print_status("ICE Connection State", "⚫ closed")


        @self.pc.on("connectionstatechange")
        async def on_connection_state_change():
            state = self.pc.connectionState
            if state == "connecting":
                print_status("Peer Connection State", "🔵 connecting")
            elif state == "connected":
                self.isConnected= True
                print_status("Peer Connection State", "🟢 connected")
            elif state == "closed":
                self.isConnected= False
                print_status("Peer Connection State", "⚫ closed")
            elif state == "failed":
                print_status("Peer Connection State", "🔴 failed")
        
        @self.pc.on("signalingstatechange")
        async def on_signaling_state_change():
            state = self.pc.signalingState
            if state == "stable":
                print_status("Signaling State", "🟢 stable")
            elif state == "have-local-offer":
                print_status("Signaling State", "🟡 have-local-offer")
            elif state == "have-remote-offer":
                print_status("Signaling State", "🟡 have-remote-offer")
            elif state == "closed":
                print_status("Signaling State", "⚫ closed")
        
        @self.pc.on("track")
        async def on_track(track):
            """Handle incoming media tracks."""
            logging.debug(f"Track received: {track.kind}")

            try:
                if track.kind == "video":
                    # Wait for first frame and start video processing
                    frame = await track.recv()
                    await self.video.track_handler(track)
                    
                elif track.kind == "audio":
                    # Start audio processing loop
                    frame = await track.recv()
                    while True:
                        try:
                            frame = await track.recv()
                            await self.audio.frame_handler(frame)
                        except Exception as e:
                            logging.debug(f"Audio processing stopped: {e}")
                            break
                            
            except MediaStreamError:
                # MediaStreamError is normal during connection cleanup
                logging.debug(f"Track processing ended for {track.kind} (connection closing)")
                return
            except Exception as e:
                # Handle other unexpected exceptions during track processing
                logging.debug(f"Track processing ended for {track.kind}: {e}")
                return

        logging.info("Creating offer...")
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        if self.connectionMethod == WebRTCConnectionMethod.Remote:
            peer_answer_json = await self.get_answer_from_remote_peer(self.pc, turn_server_info)
        elif self.connectionMethod == WebRTCConnectionMethod.LocalSTA or self.connectionMethod == WebRTCConnectionMethod.LocalAP:
            peer_answer_json = await self.get_answer_from_local_peer(self.pc, self.ip)

        if peer_answer_json is not None:
            peer_answer = json.loads(peer_answer_json)
        else:
            print("Could not get SDP from the peer. Check if the Go2 is switched on")
            sys.exit(1)

        if peer_answer['sdp'] == "reject":
            print("Go2 is connected by another WebRTC client. Close your mobile APP and try again.")
            sys.exit(1)

        remote_sdp = RTCSessionDescription(sdp=peer_answer['sdp'], type=peer_answer['type']) 
        await self.pc.setRemoteDescription(remote_sdp)
   
        await self.datachannel.wait_datachannel_open()

    
    async def get_answer_from_remote_peer(self, pc, turn_server_info):
        sdp_offer = pc.localDescription

        sdp_offer_json = {
            "id": "",
            "turnserver": turn_server_info,
            "sdp": sdp_offer.sdp,
            "type": sdp_offer.type,
            "token": self.token
        }

        logging.debug("Local SDP created: %s", sdp_offer_json)

        peer_answer_json = send_sdp_to_remote_peer(self.sn, json.dumps(sdp_offer_json), self.token, self.public_key)

        return peer_answer_json

    async def get_answer_from_local_peer(self, pc, ip):
        sdp_offer = pc.localDescription

        sdp_offer_json = {
            "id": "STA_localNetwork" if self.connectionMethod == WebRTCConnectionMethod.LocalSTA else "",
            "sdp": sdp_offer.sdp,
            "type": sdp_offer.type,
            "token": self.token
        }

        peer_answer_json = send_sdp_to_local_peer(ip, json.dumps(sdp_offer_json))

        return peer_answer_json


