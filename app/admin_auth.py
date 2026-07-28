"""
ადმინის ავტორიზაცია — ცალკე მომხმარებლები (admin_users) და ცალკე JWT
claim ("role": "admin"), რომ პაციენტის session token-მა ვერასდროს
ვერ გახსნას ადმინის endpoint-ები და პირიქით.
"""
import os
import time
import hashlib
import secrets
import jwt
import psycopg2

PG_DSN = os.environ["PORTAL_DB_DSN"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
ADMIN_JWT_EXPIRY_SECONDS = int(os.environ.get("ADMIN_JWT_EXPIRY_SECONDS", str(60 * 60 * 8)))  # 8 საათი

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    """აბრუნებს "salt$hash" ფორმატის სტრიქონს, შესანახად admin_users.password_hash-ში."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest_hex = stored_hash.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return secrets.compare_digest(digest.hex(), digest_hex)


def verify_admin(username: str, password: str):
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT id, password_hash FROM admin_users WHERE username = %s", (username.strip(),))
        row = cur.fetchone()
        if not row:
            return None
        admin_id, password_hash = row
        if not verify_password(password, password_hash):
            return None
        return {"id": admin_id, "username": username.strip()}
    finally:
        con.close()


def create_admin_token(admin_id: int, username: str) -> str:
    payload = {
        "sub": str(admin_id),
        "username": username,
        "role": "admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + ADMIN_JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_admin_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("role") != "admin":
        raise jwt.InvalidTokenError("ეს არ არის ადმინის token")
    return {"id": payload["sub"], "username": payload.get("username")}