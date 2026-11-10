"""Passwords and login tokens.

Passwords are never stored. We store a *hash*: a scrambled fingerprint that
can check a password but can't be turned back into it. Each password gets a
random "salt", so two people with the same password get different hashes.
scrypt is deliberately slow, which makes guessing passwords expensive.

After logging in, the browser keeps a random token in a cookie. We store only
a hash of the token too, so a leaked database can't be used to log in.
"""

import hashlib
import hmac
import secrets

# scrypt settings: about 16 MB of memory and a few milliseconds per check.
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **SCRYPT)
    # compare_digest takes the same time whether or not the values match,
    # so attackers can't learn anything from how fast we answer.
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def codes_match(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())
