"""Plugin-registry signing key.

The public key here is a placeholder Ed25519 key baked into the client.
To rotate: generate a new keypair, update this constant, and ship a new release.

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    k = Ed25519PrivateKey.generate()
    print("private:", k.private_bytes_raw().hex())
    print("public: ", k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())
"""

from __future__ import annotations

# 32-byte Ed25519 public key (hex-encoded).
# Replace this with the real project-controlled key before publishing.
REGISTRY_PUBKEY_HEX: str = (
    "e380d811d0e9b56542c31c2b31b2cf5230c48924455cc2cf3539f2ecfaf60aac"
)
