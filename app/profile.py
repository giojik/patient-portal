"""
პაციენტის პროფილის API — მხოლოდ საკითხავი (read-only).

განზრახ არ არსებობს არც ერთი PUT/PATCH/DELETE route ამ router-ში.
რედაქტირება არც API დონეზეა შესაძლებელი და არც frontend-ზე
(index.html-ში პროფილის ველები <span>-ებია, არა <input>-ები) —
ორივე დონე დახურულია განზრახ, პაციენტის მონაცემების უსაფრთხოებისთვის.
"""
from fastapi import APIRouter, HTTPException, Header, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.session import verify_token
from app.portal_queries import get_terra_profile
from app.onec_client import get_onec_profile

import jwt

router = APIRouter(prefix="/api/patient", tags=["profile"])
limiter = Limiter(key_func=get_remote_address)


def _get_session(authorization: str) -> dict:
    """იგივე ვერიფიკაცია, რასაც main.py იყენებს დაცულ endpoint-ებზე."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="საჭიროა ავტორიზაცია")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return verify_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="სესია ვადაგასულია, გთხოვთ თავიდან შეხვიდეთ")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="არასწორი ან დაზიანებული token")


@router.get("/profile")
@limiter.limit("20/minute")
async def patient_profile(request: Request, authorization: str = Header(None)):
    """
    სესიის წყაროს მიხედვით (token-იდან) აბრუნებს პაციენტის საკუთარ
    read-only მონაცემებს. არავითარ შემთხვევაში არ ღებულობს body-ს
    ცვლილებისთვის — ეს endpoint მხოლოდ GET-ია.
    """
    session = _get_session(authorization)

    if session["src"] == "onec":
        profile = get_onec_profile(session["sub"])
    else:
        profile = get_terra_profile(int(session["sub"]))

    if not profile:
        raise HTTPException(status_code=404, detail="პროფილი ვერ მოიძებნა")

    return profile