from fastapi import FastAPI, HTTPException, Request, Header, BackgroundTasks
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
from app.onec_client import get_panel_by_id, get_patient_name, get_personal_id_by_kartoteka, get_pending_tests
from app.onec_cache import get_backfill_status, claim_backfill, run_patient_backfill, get_patient_results_cached
from app import feature_flags as ff
from app import audit
from app.admin import router as admin_router
from app import profile as profile_router

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Lab Patient Portal API")
app.state.limiter = limiter
app.include_router(admin_router)
app.include_router(profile_router.router)


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
        audit.log_event("patient", f"terra_login_attempt:{body.login}", request.client.host if request.client else "", "login_failed")
        raise HTTPException(status_code=401, detail="არასწორი მონაცემები")

    portal_patient = get_patient_by_login(body.login)
    if not portal_patient:
        raise HTTPException(
            status_code=404,
            detail="თქვენი მონაცემები ჯერ არ არის ხელმისაწვდომი პორტალზე, სცადეთ მოგვიანებით",
        )

    token = create_token(portal_patient["id"], source="terra")
    audit.log_event(
        "patient", f"terra:{portal_patient['id']}", request.client.host if request.client else "",
        "login", full_name=portal_patient["full_name"],
    )
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
    audit.log_event(
        "patient", f"onec:{result['onec_ref']}", request.client.host if request.client else "",
        "login", personal_id=body.personal_id, full_name=result["full_name"],
    )
    return {
        "token": token,
        "full_name": result["full_name"],
    }


# ============ დაცული endpoint-ები ============

@app.get("/api/results")
@limiter.limit("20/minute")
async def results(request: Request, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    """
    სესიის წყაროს მიხედვით (token-იდან), შედეგები იტვირთება
    ან Terra-ს Portal DB-დან, ან 1C-ის Postgres cache-დან (onec_documents).

    1C-ის შემთხვევაში ეს endpoint აღარასდროს ელაპარაკება 1C-ს
    სინქრონულად:
      - თუ ეს პაციენტის პირველი login-ია (status == "none"), ვიწყებთ
        backfill-ს ფონურად (fire-and-forget) და ვაბრუნებთ ცარიელ/
        ნაწილობრივ სიას სტატუსით "syncing" — frontend-მა უნდა გაიმეოროს
        request რამდენიმე წამში.
      - status == "in_progress" — backfill უკვე მიმდინარეობს (ან ამ, ან
        პარალელური request-ის მიერ), ისევ ვაბრუნებთ იმას რაც უკვე
        დაცერილია cache-ში + სტატუსს "syncing".
      - status == "done" — ჩვეულებრივი, სწრაფი წაკითხვა Postgres-იდან.

    ახალ/განახლებულ ჩანაწერებს (backfill-ის შემდეგ) ავსებს ცალკე
    periodic worker (onec_sync_worker.py), ამ endpoint-ისგან
    დამოუკიდებლად.
    """
    session = get_current_session(authorization)
    status = "ready"

    if session["src"] == "onec":
        backfill_status = get_backfill_status(session["sub"])
        if backfill_status == "none":
            if claim_backfill(session["sub"]):
                background_tasks.add_task(run_patient_backfill, session["sub"])
            status = "syncing"
        elif backfill_status == "in_progress":
            status = "syncing"

        all_results = get_patient_results_cached(session["sub"])
        patient_name = get_patient_name(session["sub"])
        personal_id = get_personal_id_by_kartoteka(session["sub"])
    else:
        all_results = get_results_for_patient(int(session["sub"]))
        patient_name = get_patient_full_name(int(session["sub"]))
        personal_id = None

    flags = ff.get_effective_flags(session["src"], session["sub"])
    filtered = [r for r in all_results if flags.get(r.get("category"), True)]
    audit.log_event(
        "patient", f"{session['src']}:{session['sub']}", request.client.host if request.client else "",
        "view_results", f"{len(filtered)} ჩანაწერი", personal_id=personal_id, full_name=patient_name,
    )
    return {"status": status, "results": filtered}


@app.get("/api/patient/pending-tests")
@limiter.limit("20/minute")
async def pending_tests(request: Request, authorization: str = Header(None)):
    """
    "დანიშნული, ჯერ არშესრულებული" კვლევები/ანალიზები.
    ამჟამად მხოლოდ 1C წყაროსთვის — Terra-ს მხარეს შესაბამისი
    "შეკვეთა vs შესრულებული" მონაცემი არასდროს სინქრონიზებულა
    (და Terra-ს sync ისედაც გაჩერებულია).
    """
    session = get_current_session(authorization)
    if session["src"] != "onec":
        return []
    return get_pending_tests(session["sub"])


@app.get("/api/report/{panel_group_id}")
@limiter.limit("20/minute")
async def download_report(request: Request, panel_group_id: str, authorization: str = Header(None)):
    """
    ერთი პანელის PDF report. მუშაობს ორივე წყაროსთვის (Terra/1C).
    """
    session = get_current_session(authorization)
    flags = ff.get_effective_flags(session["src"], session["sub"])
    personal_id = None

    if session["src"] == "onec":
        panel_data = get_panel_by_id(panel_group_id, session["sub"])
        if not panel_data:
            raise HTTPException(status_code=404, detail="ეს კვლევა ვერ მოიძებნა")
        if not flags.get(panel_data.get("category"), True):
            raise HTTPException(status_code=403, detail="ეს ფუნქცია ამჟამად გამორთულია")
        patient_name = get_patient_name(session["sub"])
        personal_id = get_personal_id_by_kartoteka(session["sub"])
        if panel_data.get("is_narrative"):
            pdf_bytes = generate_radiology_pdf(patient_name, panel_data)
        else:
            pdf_bytes = generate_panel_pdf(patient_name, panel_data)
    else:
        patient_id = int(session["sub"])
        panel_data = get_panel_results(patient_id, panel_group_id)
        if not panel_data:
            raise HTTPException(status_code=404, detail="ეს კვლევა ვერ მოიძებნა")
        if not flags.get("lab", True):
            raise HTTPException(status_code=403, detail="ეს ფუნქცია ამჟამად გამორთულია")
        patient_name = get_patient_full_name(patient_id)
        pdf_bytes = generate_panel_pdf(patient_name, panel_data)

    filename = f"report_{panel_group_id[:8]}.pdf"
    audit.log_event(
        "patient", f"{session['src']}:{session['sub']}", request.client.host if request.client else "",
        "download_report", panel_group_id, personal_id=personal_id, full_name=patient_name,
    )
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
app.mount("/admin", StaticFiles(directory="app/static_admin", html=True), name="admin")