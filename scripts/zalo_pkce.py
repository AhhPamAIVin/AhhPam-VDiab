"""Generate a PKCE code_verifier / code_challenge pair for Zalo OAuth login.

Zalo's OAuth authorization flow (used to obtain a ZALO_OA_ACCESS_TOKEN via
https://oauth.zaloapp.com/v4/oa/permission) requires PKCE:
https://developers.zalo.me/docs/api/social-api-tong-quan/xac-thuc-va-uy-quyen/code-verifier-code-challenge-post-4842

- code_verifier: a random string of 43-128 characters from the unreserved
  character set [A-Z a-z 0-9 - . _ ~].
- code_challenge: BASE64URL(SHA256(code_verifier)), no padding.

Usage: python -m scripts.zalo_pkce
"""

import base64
import hashlib
import secrets
import string

UNRESERVED_CHARS = string.ascii_letters + string.digits + "-._~"


def generate_code_verifier(length: int = 64) -> str:
    if not 43 <= length <= 128:
        raise ValueError("code_verifier length must be between 43 and 128 characters")
    return "".join(secrets.choice(UNRESERVED_CHARS) for _ in range(length))


def generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_pkce_pair(length: int = 64) -> tuple[str, str]:
    code_verifier = generate_code_verifier(length)
    return code_verifier, generate_code_challenge(code_verifier)


if __name__ == "__main__":
    verifier, challenge = generate_pkce_pair()
    print(f"code_verifier:  {verifier}")
    print(f"code_challenge: {challenge}")
    print()
    print("Keep code_verifier secret — you'll need it again to exchange the")
    print("authorization code for an access token. Only code_challenge goes")
    print("in the /v4/oa/permission authorization URL.")
