## Setup

This project targets Python on Windows, Linux, and macOS. On Windows 11, prefer using a virtual environment in this repo (`venv/` exists in the workspace snapshot, but you can create your own).

Prerequisites:
- Python 3.10+ recommended
- For audio: PortAudio drivers (PyAudio may need build tools on Windows)
- For video: OpenCV runtime dependencies
- For LiDAR WASM: `wasmtime`

Install (editable):

```powershell
# In PowerShell
cd C:\\projects\\animus\\go2-testing\\unitree_webrtc_connect

# Create venv (optional if you already have one)
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1

# Install runtime deps
pip install -r requirements.txt

# Install package (editable)
pip install -e .
```

Notes for Windows:
- If `pyaudio` fails to install, install Microsoft C++ Build Tools and/or use a prebuilt wheel for your Python version.
- If microphone/speaker routing is needed, verify device permissions and sample rate.
- Firewall: see `tmp/fix_firewall.ps1` if present; data channel/HTTP may need local permits.

Runtime requirements:
- Local AP: connect Wi‑Fi to Go2’s AP, IP assumed `192.168.12.1`
- Local STA: robot and client on same LAN; provide `ip` or discover via `serialNumber`
- Remote: Unitree account credentials to fetch `token` and TURN server info

Quick test:
```powershell
# Display video
python .\\examples\\video\\camera_stream\\display_video_channel.py

# LowState status stream
python .\\examples\\data_channel\\lowstate\\lowstate.py
```
