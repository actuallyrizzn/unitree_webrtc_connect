## Title
aiortc ↔ Unitree Go2 WebRTC datachannel handshake fails (AP mode) — RFC8841 SDP interop

## Summary (TL;DR)
- Symptom: WebRTC connects to Go2 AP, ICE completes and A/V tracks arrive, but the data channel never opens; app-level validation never runs; script times out.
- Root cause: Known interop bug between aiortc (≥1.10 up to ≥1.14) and Go2’s newer SCTP SDP format (RFC 8841). aiortc’s offer causes the Go2 to respond in new SDP form; aiortc 1.14 then fails the DTLS/SCTP bring-up.
- aiortc 1.9.0 (under Python 3.11) restores the DTLS handshake and media streams, but the Go2 still replies with the new `a=sctp-port` syntax, so SCTP never transitions to open. The problem is now isolated to the SDP answer.
- Confirmed workaround in progress: Strip `a=fingerprint:sha-384` / `sha-512` and rewrite the answer’s SCTP section to old-style `m=application ... DTLS/SCTP 5000` + `a=sctpmap:5000 webrtc-datachannel 1024` (implementation outstanding).
- Alternative if patching is undesirable: Stay on aiortc 1.9.x permanently (requires Python ≤3.11 so prebuilt `av<13` wheels exist).

## Environment
- OS: Windows 10
- Network: Direct AP
  - Host Wi‑Fi: 192.168.12.121
  - Go2: 192.168.12.1
  - TCP 9991 reachable; 8081 closed (expected)
- Python interpreters:
  - `venv_aiortc114` (legacy) — Python 3.13, aiortc 1.14.0+
  - `venv_aiortc19` (current) — local portable Python 3.11 installed under `C:\projects\animus\python311`, aiortc 1.9.0, `av==12.3.0`
- Constraint: Do not modify core package files; all experiments in `tmp/`

## Repro steps
1) Connect host to Go2 AP; close mobile app.
2) Run minimal script to connect and subscribe to `RTC_TOPIC["LOW_STATE"]`.
3) Observe logs:
   - ICE gathering: complete
   - Old SDP (8081): refused; New SDP (9991): success
   - ICE connection: checking → completed
   - Signaling: stable; video/audio tracks received
   - Datachannel: never opens → timeout (5–60s)

## Observations and evidence
- Without workaround:
  - ICE completes, DTLS begins, but SCTP/datachannel never transitions to open.
  - No "Validation Needed."/"Validation Ok." messages observed (validation is app‑layer on datachannel, so it never starts).
- With SDP fingerprint strip workaround:
  - ICE completes reliably; A/V tracks flow.
  - Datachannel still may require longer wait to open on some runs; extend wait to ≥30s for confirmation.

## Workaround attempt #1 (non-invasive in tmp/)
Add this before creating the connection in test scripts:

```python
from aiortc import RTCPeerConnection, RTCSessionDescription

_orig_setLocalDescription = RTCPeerConnection.setLocalDescription

async def _patched_setLocalDescription(self, description):
    try:
        if (
            description and isinstance(description, RTCSessionDescription)
            and description.type == "offer" and isinstance(description.sdp, str)
        ):
            filtered = []
            for line in description.sdp.splitlines():
                if line.startswith("a=fingerprint:sha-384") or line.startswith("a=fingerprint:sha-512"):
                    continue
                filtered.append(line)
            description = RTCSessionDescription(sdp="\r\n".join(filtered) + "\r\n", type=description.type)
    except Exception:
        pass
    return await _orig_setLocalDescription(self, description)

RTCPeerConnection.setLocalDescription = _patched_setLocalDescription
```

After this, proceed with normal `Go2WebRTCConnection(LocalAP)` connect and wait longer for datachannel open (≥30s) before failing.

Outcome with aiortc 1.14: DTLS handshake still fails with "connection error" before SCTP starts (no progress).

Outcome with aiortc 1.9.0 + Python 3.11: DTLS completes, audio/video tracks flow, but the Go2 continues to answer with `m=application … UDP/DTLS/SCTP` + `a=sctp-port:5000`, so SCTP never opens. We now need to patch the **answer** as well.

### Workaround extension (planned)
Intercept the SDP answer returned by `send_sdp_to_local_peer` and coerce the application section to the legacy style:

```python
if line.startswith("m=application"):
    filtered.append("m=application 9 DTLS/SCTP 5000")
elif line.startswith("a=sctp-port"):
    filtered.append("a=sctpmap:5000 webrtc-datachannel 1024")
```

This is expected to trigger the Go2’s legacy SCTP negotiation (“old school it is”) which community reports confirmed works. Implementation still pending while the robot recharges.

## Why this works
- Per reports, advertising sha‑384/512 fingerprints in the offer nudges the Go2 into replying with new SCTP SDP (RFC 8841: `a=sctp-port:5000`). aiortc versions in use don’t fully interoperate with this negotiation.
- Stripping those lines keeps the exchange in the older style (`a=sctpmap:5000 webrtc-datachannel 1024`), which aiortc handles.

## Alternatives / next steps
- (Active) Finalise the answer-SDP rewrite patch in `tmp` script and retest once the robot has battery.
- If rewriting succeeds, upstream a cleaner hook or tiny helper that converts Go2 answers for aiortc.
- If conversion fails, investigate SCTP validation messages from the Go2 (`validation`, `validation err`) to ensure the app-layer handshake is being reached.
- If DTLS ever regresses, consider narrowing cipher suites or sticking with the Python 3.11 + aiortc 1.9.0 configuration permanently.
- Long‑term: adopt aiortc with proper RFC 8841 support (upstream issue remains open/stale as of late 2025).

### Alternative fallback: pin aiortc 1.9.x
aiortc 1.9.x did not trigger the Go2’s newer SDP response, so the datachannel could open without the SDP fingerprint hack. This can be simpler than monkey‑patching in some environments, but there are caveats around Python and wheel availability.

Suggested constraints (Windows):
- Prefer Python 3.10–3.11 for best wheel coverage
- Ensure `av` version compatible with aiortc 1.9.x (typically `av<13`) and available as wheels for your Python version

Example (new/isolated venv recommended):
```powershell
# In a clean venv
pip install --upgrade pip setuptools wheel

# Pin aiortc and compatible av
pip install "aiortc==1.9.0" "av<13"

# Then install remaining deps from project as needed (excluding aiortc/av pins)
# pip install -r requirements.txt --no-deps
# pip install <other-packages>
```

Notes:
- If wheels are unavailable for your Python version/arch, pip may try to build `av` from source (FFmpeg headers required). If that happens, either switch to a Python version with wheel coverage or use the SDP fingerprint workaround instead.
- When pinning aiortc, keep your test scripts unmodified; the older aiortc should interoperate with the Go2 out‑of‑the‑box.

## Links and references
- RFC 8841 SDP for SCTP data channels interop: aiortc Issue #1338 ("SCTP data channel won't open when mentioning sha-384 and sha-512").
- DTLS cipher suite size and fragmentation concerns: aiortc Issue #956.
- Community reports indicate success by forcing old SDP style; multiple embedded stacks show similar interop gaps.

Note: This document describes a tmp‑only mitigation path. We do not modify core files; the patch above should live only in test scripts until a permanent upstream fix is available.


