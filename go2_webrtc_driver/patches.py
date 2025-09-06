# Monkey-patch aioice.Connection to use a fixed username and password across all instances.

# Monkey-patch aiortc.rtcdtlstransport.X509_DIGEST_ALGORITHMS to restrict SHA algorithms.
# In aiortc 1.10.0, additional SHA algorithms were introduced which cause Unity Go2 to use a new SCTP format.
# However, aiortc still uses the older SCTP syntax, which is not compatible with the new format.
# This patch ensures compatibility by limiting the digest algorithms to SHA-256 only.

import aioice
import aiortc
from packaging.version import Version


# ===== Patch aioice.Connection to use fixed username/password =====
class SharedCredentialsConnection(aioice.Connection):
    local_username = aioice.utils.random_string(4)
    local_password = aioice.utils.random_string(22)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.local_username = SharedCredentialsConnection.local_username
        self.local_password = SharedCredentialsConnection.local_password


aioice.Connection = SharedCredentialsConnection


# ===== Patch aiortc.rtcdtlstransport.X509_DIGEST_ALGORITHMS =====
rtcdtlstransport = aiortc.rtcdtlstransport

version = Version(aiortc.__version__)
if version == Version("1.10.0"):
    rtcdtlstransport.X509_DIGEST_ALGORITHMS = {
        "sha-256": "SHA256",
    }
elif version >= Version("1.11.0"):
    from cryptography.hazmat.primitives import hashes
    rtcdtlstransport.X509_DIGEST_ALGORITHMS = {
        "sha-256": hashes.SHA256(),
    }