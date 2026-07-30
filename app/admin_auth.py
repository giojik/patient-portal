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
        cur.execute(
            "SELECT id, password_hash, role FROM admin_users WHERE username = %s", (username.strip(),)
        )
        row = cur.fetchone()
        if not row:
            return None
        admin_id, password_hash, role = row
        if not verify_password(password, password_hash):
            return None
        return {"id": admin_id, "username": username.strip(), "role": role}
    finally:
        con.close()


def create_admin_token(admin_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(admin_id),
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ADMIN_JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_admin_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("role") not in ("superadmin", "manager", "viewer"):
        raise jwt.InvalidTokenError("ეს არ არის ადმინის token")
    return {"id": payload["sub"], "username": payload.get("username"), "role": payload["role"]}


def list_admin_users() -> list:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT id, username, role, created_at FROM admin_users ORDER BY username")
        return [
            {"id": i, "username": u, "role": r, "created_at": c.isoformat() if c else None}
            for i, u, r, c in cur.fetchall()
        ]
    finally:
        con.close()


def create_admin_user(username: str, password: str, role: str) -> int:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO admin_users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
            (username.strip(), hash_password(password), role),
        )
        new_id = cur.fetchone()[0]
        con.commit()
        return new_id
    finally:
        con.close()


def update_admin_user(admin_id: int, role: str = None, password: str = None) -> bool:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        if role is not None:
            cur.execute("UPDATE admin_users SET role = %s WHERE id = %s", (role, admin_id))
        if password:
            cur.execute(
                "UPDATE admin_users SET password_hash = %s WHERE id = %s",
                (hash_password(password), admin_id),
            )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def delete_admin_user(admin_id: int) -> bool:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM admin_users WHERE role = 'superadmin'")
        superadmin_count = cur.fetchone()[0]
        cur.execute("SELECT role FROM admin_users WHERE id = %s", (admin_id,))
        row = cur.fetchone()
        if not row:
            return False
        if row[0] == "superadmin" and superadmin_count <= 1:
            raise ValueError("ბოლო superadmin-ის წაშლა არ შეიძლება")
        cur.execute("DELETE FROM admin_users WHERE id = %s", (admin_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


_ROLE_RANK = {"viewer": 0, "manager": 1, "superadmin": 2}


def role_at_least(role: str, minimum: str) -> bool:
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK.get(minimum, 99)