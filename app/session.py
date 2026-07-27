"""
JWT session tokens — ავტორიზაციის შემდეგ პაციენტს ეძლევა token,
რომელიც უნდა გადმოეცეს Authorization header-ით დაცულ endpoint-ებზე.

განზოგადებულია "subject"-ზე (string) — ეს შეიძლება იყოს ან Terra-ს
Postgres patient_id (int, string-ად კონვერტირებული), ან 1C-ის
Ref_Key (UUID string), წყაროს მიხედვით.
"""
import os
import time
import jwt

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = int(os.environ.get("JWT_EXPIRY_SECONDS", str(60 * 60 * 24)))  # 24 საათი


def create_token(subject: str, source: str = "terra") -> str:
    """
    subject: პაციენტის უნიკალური identifier (Postgres id ან 1C Ref_Key)
    source: "terra" ან "onec" — რომელი წყაროდანაა ეს იდენტიფიკატორი
    """
    payload = {
        "sub": str(subject),
        "src": source,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """აბრუნებს {"sub": ..., "src": ...}-ს, თუ token ვალიდურია."""
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    return {"sub": payload["sub"], "src": payload.get("src", "terra")}