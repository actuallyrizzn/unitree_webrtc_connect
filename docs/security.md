## Security Notes

### Remote Mode (STA-T / Cloud)

- The driver authenticates with Unitree cloud using email/password (password is MD5’d before POST).
- It fetches a public RSA key, generates a random AES key, encrypts the AES key with RSA, and encrypts the SDP/data with AES.
- Remote requests include signed headers (timestamp/nonce/signature) to resemble the mobile app.
- TURN server info is retrieved and used to build `RTCIceServer` entries; STUN is also configured.

Threat considerations:
- Keep credentials and tokens secure. Do not commit or log them.
- AES key is ephemeral per exchange; RSA public keys fetched from Unitree are used for transport security of secrets.

### Local Mode (AP/STA)

Two methods are supported for SDP exchange:
- Old method: HTTP POST to `http://<ip>:8081/offer` with JSON body → plain-text response
- New method: staged key exchange via `http://<ip>:9991` where the robot publishes a key payload, the client encrypts using RSA/AES, and receives an encrypted answer

Notes:
- Local HTTP endpoints should be reachable only within your LAN/AP network segment.
- Windows firewall can block local TCP; allow for the chosen ports.

### Data Channel Validation

- Initial handshake requires responding to a validation challenge (`MD5('UnitreeGo2_' + key)` then base64) before normal messaging is accepted.
- This avoids accidental cross-talk and ensures a minimally authenticated session bootstrap.
