## FAQ

- How do I connect locally without knowing IP?
  - Use Local STA and pass `serialNumber`. The driver multicasts a discovery query and maps SN → IP when the Go2 responds.

- I get "reject" when setting remote description
  - The robot is already connected to another WebRTC client (e.g., mobile app). Close the other client and reconnect.

- Video window is blank but no errors
  - Make sure `switchVideoChannel(True)` is called and the callback pushes frames into your rendering loop. On Windows, avoid blocking the asyncio loop; use a thread/queue as in the example.

- Can I issue high-level movement commands?
  - Yes. Use the data channel request API with `RTC_TOPIC['SPORT_MOD']` and `SPORT_CMD` constants.

- How do I list and play audio clips on the robot?
  - Use `WebRTCAudioHub`: `get_audio_list()`, `play_by_uuid(uuid)`, and `upload_audio_file(path)`.

- How do I switch LiDAR decoders?
  - `conn.datachannel.set_decoder('libvoxel')` or `'native'` at runtime.

- What Python versions are supported?
  - Target is Python 3.10+; ensure dependency compatibility in `requirements.txt`.

- What if `pyaudio` fails on Windows?
  - Install Build Tools or use a prebuilt wheel; confirm PortAudio is available.
