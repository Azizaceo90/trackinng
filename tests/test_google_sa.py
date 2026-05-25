"""Verify the pure-Python RS256 signer byte-for-byte against an openssl
reference signature, plus a sign->verify roundtrip."""
import base64

from reclaim.google_sa import (
    _b64url,
    _parse_pkcs8_rsa,
    rsa_sign_sha256,
    signed_jwt,
)

# Throwaway 2048-bit RSA key generated with `openssl genpkey`. Test-only.
TEST_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDk6/jVx1HVHZPP
JHzZmCAEZ/XOyobyqQ3/c4KX8sZr9NRDqGLl2pqH1ikoG3YsifnAkEzycwi5FT9K
//VeLhXymKouHSUFmJYdVqX81oH1grbUPaG5UvuDMya7EZ4yUFWT038J4FvmqblV
N8P+G1LG39l7BJtbtXuquKm0b5ouglWYOLrBwHSUElAD5cqaKW70M3E73s3CFZlk
mhsGjGXdS2dPHPsPOvxgnk8aR14/J6jT095DaXdclFbicGgxx+CzwBRakxFvRq57
yQ3ktBrlZMiKkU6y8eEeG0uGtzHJxu1MI78ytDDnCNoX4oUMBDtpSxngR4YVlLd4
Tbuejo1ZAgMBAAECgf8WNplluLDcQ/TrIz1glKAgUdgOVX2Sle0GjN8Lpk52lLYu
WQLrZgc1TkjpUZy4CgLHNcPtXGZisGxdQqAzwRThoSr6ZqNftfG58x9wfO2v+KlJ
i6Jmh/OmsOsStGg3c50uj1K872dG0gEiWl2ErKycWmSdcbJcUGiZCLKWH3durlxK
9ei8I29qC+u65rE0UeKhXdJpmtsjFmdIyXnmeZcvpy4FEWs+vlV+vm4r8UYY62Js
I+1YKsIedyhsosnzvGKiVGqCK1sJe0uTdGSNL/XuboGkDXu1i2pGmkZc9mT/tJnx
NNqF1YBRWBdtd6NWF80+eerD0OKfvhkJgDhWGw8CgYEA+00TsowU3Q3+PA5Aji9Q
OBIYAtZXEgSQ7Bfi8gNEungcJC5kLPT64/DtZiqGTOKfq4yaKovAuuqcoAjpw23j
qQYZ7RKbrgABXi38GLb2bpk7fF/TvN7EI4n0STFmFSjMgA1Ts70x+vXvBKwvH40g
lbzVUArkT1J5btuG8wA+8v8CgYEA6TPFK9cY9LM/Hu4VhI83tQB9diE80+txxSvW
DzK0JyiZhHQRNwW5P7653KcMElhq5bCCisYlpay05++YVGKVRKhH21qqpjupq/ly
1XihTGKMcmYV09BBMRfRjtclDYIu3Im4Q5hfOkIbfwZnXm0ca5TdAGRqJ0GEPEX7
W8j296cCgYB6h/uJvHnTyyXifISHj5RKsq/YelBcLbPIGmGC5YsWbMgz8BbSQOUw
TWJDxYpUZM+74sOs6RWhThHuikoJC0TNPndXvBIChmgkVsGr/1IrXTW/EC560hfK
yFI/egGvYYRND7J3WlHLby0LFzWm6bYwrLFJ5PWro6goIIwtYjpPWwKBgQC6kgCd
ImmE3CMTy8bLVwlqdgnqCI1xvlw3Mur/HcGj0od/wJxFOP8MULrCHaM6yiI7wQuv
mvdjpNjW9okYegaR91AF3nPIqtMEE34b63agdfeHTsUHwQVnEXdGoDm0pQJ4znXt
HmqRYXI+HhF1KjYim+Zz+eIzpeb1kceXlyB+4QKBgQChqb2Q37q5TLixXIyohO40
4ko6xDQHx0nLniqCzA6F2Kf2Jt9mjYFXJFNFnQ/gztrCYgjAEQuisLpjk1K1OrM8
R+OKL1gOGCh0r2llUCSWeTZFXPNgv2y28EgxMPqns9OcmYMb/vHgs02Vs/XOUx2j
x1KV/vnPgN37Rc554KXK2g==
-----END PRIVATE KEY-----"""

# openssl dgst -sha256 -sign <key> over the exact bytes b"hello.jwt.signing.input"
REF_MESSAGE = b"hello.jwt.signing.input"
REF_SIG_B64URL = (
    "vpC5MROWjgizFJZvgIY4ygHfaFJTr7E-wkaulKSBZMUAa7zYuVxyGEt46A0TWMTm_Et14LYC3NTUP1c95-0HJ82"
    "I4gwtAl5rpUlJkxiXqSOH1mWBhgW657qAsEp3NhVneR1e7fm6y3tXeEPVVhtL3R9jk_ogbU8ouGDQNM1r_4Le1Z"
    "srTLOhkwTb6TFnzOZkxZ-oWRZMZjeIxM185thwOKn6mdRy2CG4AXjoHsHMIKj7-S0xc0-B3LbrZn2lB_gq9DrwN"
    "R5QPajLStpUyCm6eLtX6fL70ZJ2F_Ml2lkZZqgihrwW_tH-50GEuxgVbHcZ-5ySx3ZA6tGE0ZGooDpo2A"
)


def test_signer_matches_openssl_byte_for_byte():
    n, e, d = _parse_pkcs8_rsa(TEST_PEM)
    sig = rsa_sign_sha256(REF_MESSAGE, n, d)
    assert _b64url(sig) == REF_SIG_B64URL


def test_sign_verify_roundtrip():
    # Verify with the public key: s^e mod n must reconstruct the PKCS#1 v1.5
    # encoded message, which ends with the SHA-256 DigestInfo.
    import hashlib
    n, e, d = _parse_pkcs8_rsa(TEST_PEM)
    sig = rsa_sign_sha256(b"another message", n, d)
    s = int.from_bytes(sig, "big")
    recovered = pow(s, e, n).to_bytes((n.bit_length() + 7) // 8, "big")
    assert recovered.startswith(b"\x00\x01\xff")
    assert recovered.endswith(hashlib.sha256(b"another message").digest())


def test_signed_jwt_structure():
    sa = {
        "client_email": "reclaim-cron@example.iam.gserviceaccount.com",
        "private_key": TEST_PEM,
        "private_key_id": "abc123",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    jwt = signed_jwt(sa, ["https://www.googleapis.com/auth/calendar.events"],
                     subject="user@workspace.com")
    parts = jwt.split(".")
    assert len(parts) == 3

    def _decode(seg):
        import base64 as b64
        import json
        return json.loads(b64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))

    header = _decode(parts[0])
    claims = _decode(parts[1])
    assert header == {"alg": "RS256", "typ": "JWT", "kid": "abc123"}
    assert claims["iss"] == "reclaim-cron@example.iam.gserviceaccount.com"
    assert claims["sub"] == "user@workspace.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/calendar.events"
    assert claims["aud"] == "https://oauth2.googleapis.com/token"
    assert claims["exp"] - claims["iat"] == 3600
