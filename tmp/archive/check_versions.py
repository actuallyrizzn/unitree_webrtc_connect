import aiortc
import aioice
from packaging import version

print("Current versions:")
print(f"  aiortc: {aiortc.__version__}")
print(f"  aioice: {aioice.__version__}")

ver = version.Version(aiortc.__version__)
print(f"\nVersion checks for patches:")
print(f"  Is exactly 1.10.0: {ver == version.Version('1.10.0')}")
print(f"  Is >= 1.11.0: {ver >= version.Version('1.11.0')}")
print(f"  Is < 1.10.0: {ver < version.Version('1.10.0')}")

if ver < version.Version('1.10.0'):
    print("\nNOTE: aiortc 1.9.0 is older than the patched versions.")
    print("      The X509_DIGEST_ALGORITHMS patches won't apply,")
    print("      which is likely fine since 1.9.0 may not need them.")

