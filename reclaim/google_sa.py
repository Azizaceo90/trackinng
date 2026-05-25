"""Service-account access tokens for Google APIs — stdlib only.

Mints an OAuth access token from a Google service-account key using the
JWT-bearer grant, with optional domain-wide-delegation impersonation
(`subject`). Used so the GitHub Actions crons can write to Calendar with
credentials that never expire on the 7-day OAuth clock.

RS256 signing is implemented in pure Python (RSA via the built-in
arbitrary-precision `pow`, PKCS#1 v1.5 padding) so we depend on nothing
outside the standard library — matching the rest of this package and
sidestepping environments where `cryptography` won't import.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time

from reclaim.gmail_fetch import _post_form

DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"

# DER-encoded DigestInfo prefix for SHA-256 (RFC 8017, section 9.2).
_SHA256_DIGESTINFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _pem_to_der(pem: str) -> bytes:
    lines: list[str] = []
    capture = False
    for line in pem.splitlines():
        if line.startswith("-----BEGIN"):
            capture = True
            continue
        if line.startswith("-----END"):
            break
        if capture:
            lines.append(line.strip())
    return base64.b64decode("".join(lines))


def _read_tlv(data: bytes, off: int) -> tuple[int, bytes, int]:
    """Read one DER TLV. Returns (tag, value_bytes, next_offset)."""
    tag = data[off]
    off += 1
    length = data[off]
    off += 1
    if length & 0x80:
        nbytes = length & 0x7F
        length = int.from_bytes(data[off:off + nbytes], "big")
        off += nbytes
    value = data[off:off + length]
    return tag, value, off + length


def _read_int(data: bytes, off: int) -> tuple[int, int]:
    tag, value, off = _read_tlv(data, off)
    if tag != 0x02:
        raise ValueError(f"expected INTEGER tag, got {tag:#x}")
    return int.from_bytes(value, "big"), off


def _parse_pkcs8_rsa(pem: str) -> tuple[int, int, int]:
    """Extract (modulus n, public exponent e, private exponent d) from a
    PKCS#8 unencrypted RSA private key PEM."""
    der = _pem_to_der(pem)
    tag, content, _ = _read_tlv(der, 0)
    if tag != 0x30:
        raise ValueError("PKCS#8: expected outer SEQUENCE")
    off = 0
    _, off = _read_int(content, off)                 # version
    tag, _alg, off = _read_tlv(content, off)         # AlgorithmIdentifier
    if tag != 0x30:
        raise ValueError("PKCS#8: expected algorithm SEQUENCE")
    tag, pk_octets, off = _read_tlv(content, off)    # privateKey OCTET STRING
    if tag != 0x04:
        raise ValueError("PKCS#8: expected privateKey OCTET STRING")
    tag, rsa_seq, _ = _read_tlv(pk_octets, 0)        # RSAPrivateKey
    if tag != 0x30:
        raise ValueError("RSAPrivateKey: expected SEQUENCE")
    o = 0
    _, o = _read_int(rsa_seq, o)   # version
    n, o = _read_int(rsa_seq, o)   # modulus
    e, o = _read_int(rsa_seq, o)   # publicExponent
    d, o = _read_int(rsa_seq, o)   # privateExponent
    return n, e, d


def rsa_sign_sha256(message: bytes, n: int, d: int) -> bytes:
    """RSASSA-PKCS1-v1_5 signature over SHA-256(message)."""
    k = (n.bit_length() + 7) // 8
    digest = hashlib.sha256(message).digest()
    t = _SHA256_DIGESTINFO_PREFIX + digest
    ps_len = k - len(t) - 3
    if ps_len < 8:
        raise ValueError("RSA key too small for SHA-256 signature")
    em = b"\x00\x01" + b"\xff" * ps_len + b"\x00" + t
    m = int.from_bytes(em, "big")
    s = pow(m, d, n)
    return s.to_bytes(k, "big")


def signed_jwt(sa_info: dict, scopes: list[str], subject: str | None) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    if sa_info.get("private_key_id"):
        header["kid"] = sa_info["private_key_id"]
    token_uri = sa_info.get("token_uri", DEFAULT_TOKEN_URI)
    claims = {
        "iss": sa_info["client_email"],
        "scope": " ".join(scopes),
        "aud": token_uri,
        "iat": now,
        "exp": now + 3600,
    }
    if subject:
        claims["sub"] = subject
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    )
    n, _e, d = _parse_pkcs8_rsa(sa_info["private_key"])
    sig = rsa_sign_sha256(signing_input.encode("ascii"), n, d)
    return signing_input + "." + _b64url(sig)


def access_token(sa_info: dict, scopes: list[str], subject: str | None = None) -> str:
    """Exchange a signed JWT for an OAuth access token (jwt-bearer grant)."""
    assertion = signed_jwt(sa_info, scopes, subject)
    token_uri = sa_info.get("token_uri", DEFAULT_TOKEN_URI)
    resp = _post_form(token_uri, {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    })
    if "access_token" not in resp:
        raise RuntimeError(f"service-account token exchange failed: {resp}")
    return resp["access_token"]
