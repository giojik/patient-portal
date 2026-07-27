from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
import jwt

from app.auth import verify_patient
from app.portal_queries import (
    get_patient_by_login,
    get_results_for_patient,
    get_panel_results,
    get_patient_full_name,
)
from app.pdf_report import generate_panel_pdf
from app.html_pdf_report import generate_radiology_pdf
from app.session import create_token, verify_token
from app.otp_auth import request_code, verify_code
from app.onec_client import get_patient_results, get_panel_by_id, get_patient_name

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Lab Patient Portal API")
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    raise HTTPException(status_code=429, detail="ძალიან ბევრი მცდელობა, სცადეთ მოგვიანებით")


class LoginRequest(BaseModel):
    login: str
    password: str


class RequestCodeBody(BaseModel):
    personal_id: str


class VerifyCodeBody(BaseModel):
    personal_id: str
    code: str


def get_current_session(authorization: str = Header(None)) -> dict:
    """
    ითხოვს `Authorization: Bearer <token>` header-ს ყველა დაცულ endpoint-ზე.
    აბრუნებს {"sub": ..., "src": "terra"|"onec"}.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="საჭიროა ავტორიზაცია")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return verify_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="სესია ვადაგასულია, გთხოვთ თავიდან შეხვიდეთ")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="არასწორი ან დაზიანებული token")


# ============ Terra LOGIN/PASS ავტორიზაცია (არსებული) ============

@app.post("/api/login")
@limiter.limit("5/15minute")
async def login(request: Request, body: LoginRequest):
    """
    ვერიფიცირებს Terra-ს LOGIN/PASS-ით პირდაპირ (real-time),
    შემდეგ აბრუნებს session token-ს (JWT) — არა patient_id-ს ღიად.
    """
    patient = verify_patient(body.login, body.password)
    if not patient:
        raise HTTPException(status_code=401, detail="არასწორი მონაცემები")

    portal_patient = get_patient_by_login(body.login)
    if not portal_patient:
        raise HTTPException(
            status_code=404,
            detail="თქვენი მონაცემები ჯერ არ არის ხელმისაწვდომი პორტალზე, სცადეთ მოგვიანებით",
        )

    token = create_token(portal_patient["id"], source="terra")
    return {
        "token": token,
        "full_name": portal_patient["full_name"],
    }


# ============ 1C პირადი ნომერი + OTP ავტორიზაცია (ახალი) ============

@app.post("/api/auth/request-code")
@limiter.limit("3/15minute")
async def auth_request_code(request: Request, body: RequestCodeBody):
    """
    ეძებს პაციენტს 1C-ში პირადი ნომრით, აგენერირებს OTP კოდს.
    ⚠️ SMS ჯერ არ არის ინტეგრირებული — `_debug_code` პასუხშივე
    ბრუნდება დროებით, ტესტირებისთვის. წაშალეთ ეს ველი, როცა
    SMS გაგზავნა ჩაირთვება.
    """
    result = request_code(body.personal_id)
    if not result:
        raise HTTPException(status_code=404, detail="პაციენტი ვერ მოიძებნა")
    return result


@app.post("/api/auth/verify-code")
@limiter.limit("5/15minute")
async def auth_verify_code(request: Request, body: VerifyCodeBody):
    """ამოწმებს OTP კოდს, წარმატების შემთხვევაში აბრუნებს session token-ს."""
    result = verify_code(body.personal_id, body.code)
    if not result:
        raise HTTPException(status_code=401, detail="არასწორი ან ვადაგასული კოდი")

    token = create_token(result["onec_ref"], source="onec")
    return {
        "token": token,
        "full_name": result["full_name"],
    }


# ============ დაცული endpoint-ები ============

@app.get("/api/results")
@limiter.limit("20/minute")
async def results(request: Request, authorization: str = Header(None)):
    """
    სესიის წყაროს მიხედვით (token-იდან), შედეგები იტვირთება
    ან Terra-ს Portal DB-დან, ან 1C-დან პირდაპირ (live query).
    """
    session = get_current_session(authorization)
    if session["src"] == "onec":
        return get_patient_results(session["sub"])
    return get_results_for_patient(int(session["sub"]))


@app.get("/api/report/{panel_group_id}")
@limiter.limit("20/minute")
async def download_report(request: Request, panel_group_id: str, authorization: str = Header(None)):
    """
    ერთი პანელის PDF report. მუშაობს ორივე წყაროსთვის (Terra/1C).
    """
    session = get_current_session(authorization)

    if session["src"] == "onec":
        panel_data = get_panel_by_id(panel_group_id, session["sub"])
        if not panel_data:
            raise HTTPException(status_code=404, detail="ეს კვლევა ვერ მოიძებნა")
        patient_name = get_patient_name(session["sub"])
        if panel_data.get("is_narrative"):
            pdf_bytes = generate_radiology_pdf(patient_name, panel_data)
        else:
            pdf_bytes = generate_panel_pdf(patient_name, panel_data)
    else:
        patient_id = int(session["sub"])
        panel_data = get_panel_results(patient_id, panel_group_id)
        if not panel_data:
            raise HTTPException(status_code=404, detail="ეს კვლევა ვერ მოიძებნა")
        patient_name = get_patient_full_name(patient_id)
        pdf_bytes = generate_panel_pdf(patient_name, panel_data)

    filename = f"report_{panel_group_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"name": "Lab Patient Portal API", "docs": "/docs", "portal": "/portal"}


app.mount("/portal", StaticFiles(directory="app/static", html=True), name="portal")