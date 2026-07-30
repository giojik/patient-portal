"""
ადმინის API — ფუნქციების ჩართვა/გამორთვა, პაციენტთა ჯგუფების მართვა,
კლინიკის პარამეტრები, ადმინის მომხმარებლები (როლებით) და აუდიტ ლოგი.
სრულიად ცალკეა პაციენტის /api/* endpoint-ებისგან: ცალკე ავტორიზაცია
(admin_users + role claim), ამიტომ პაციენტის token აქ არ მუშაობს.
"""
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
import jwt

from app.admin_auth import (
    verify_admin,
    create_admin_token,
    verify_admin_token,
    role_at_least,
    list_admin_users,
    create_admin_user,
    update_admin_user,
    delete_admin_user,
)
from app.portal_queries import get_patient_by_login
from app.onec_client import find_patient_by_personal_id
from app import feature_flags as ff
from app import clinic_settings as cs
from app import audit

router = APIRouter(prefix="/api/admin", tags=["admin"])
limiter = Limiter(key_func=get_remote_address)


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class FeatureToggleRequest(BaseModel):
    enabled: bool


class GroupCreateRequest(BaseModel):
    name: str


class OverrideRequest(BaseModel):
    enabled: bool


class MemberAddRequest(BaseModel):
    source: str  # "terra" | "onec"
    identifier: str  # terra: LOGIN; onec: პირადი ნომერი


class SettingsUpdateRequest(BaseModel):
    timezone: Optional[str] = None
    clinic_name: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None


class AdminUserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class AdminUserUpdateRequest(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None


def get_current_admin(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="საჭიროა ადმინის ავტორიზაცია")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return verify_admin_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="სესია ვადაგასულია, გთხოვთ თავიდან შეხვიდეთ")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="არასწორი ან დაზიანებული token")


def require_role(admin: dict, minimum: str):
    if not role_at_least(admin["role"], minimum):
        raise HTTPException(status_code=403, detail="არასაკმარისი უფლებები ამ მოქმედებისთვის")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/login")
@limiter.limit("5/15minute")
async def admin_login(request: Request, body: AdminLoginRequest):
    admin = verify_admin(body.username, body.password)
    if not admin:
        audit.log_event("admin", body.username, _client_ip(request), "login_failed")
        raise HTTPException(status_code=401, detail="არასწორი მონაცემები")
    token = create_admin_token(admin["id"], admin["username"], admin["role"])
    audit.log_event("admin", admin["username"], _client_ip(request), "login")
    return {"token": token, "username": admin["username"], "role": admin["role"]}


# ============ ფუნქციების გადამრთველები ============

@router.get("/features")
async def list_features(authorization: str = Header(None)):
    get_current_admin(authorization)
    return ff.list_features_with_overrides()


@router.put("/features/{feature_key}")
async def toggle_feature(
    feature_key: str, body: FeatureToggleRequest, request: Request, authorization: str = Header(None)
):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    if not ff.set_global_flag(feature_key, body.enabled):
        raise HTTPException(status_code=404, detail="ეს ფუნქცია ვერ მოიძებნა")
    audit.log_event(
        "admin", admin["username"], _client_ip(request), "toggle_feature",
        f"{feature_key} -> {'ჩართული' if body.enabled else 'გამორთული'}",
    )
    return {"feature_key": feature_key, "enabled": body.enabled}


@router.put("/features/{feature_key}/overrides/{group_id}")
async def upsert_override(feature_key: str, group_id: int, body: OverrideRequest, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    ff.set_override(feature_key, group_id, body.enabled)
    return {"feature_key": feature_key, "group_id": group_id, "enabled": body.enabled}


@router.delete("/features/{feature_key}/overrides/{group_id}")
async def delete_override(feature_key: str, group_id: int, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    ff.remove_override(feature_key, group_id)
    return {"status": "ok"}


# ============ პაციენტთა ჯგუფები ============

@router.get("/groups")
async def get_groups(authorization: str = Header(None)):
    get_current_admin(authorization)
    return ff.list_groups()


@router.post("/groups")
async def add_group(body: GroupCreateRequest, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="სახელი სავალდებულოა")
    group_id = ff.create_group(name)
    return {"id": group_id, "name": name}


@router.delete("/groups/{group_id}")
async def remove_group(group_id: int, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    ff.delete_group(group_id)
    return {"status": "ok"}


@router.get("/groups/{group_id}/members")
async def get_group_members(group_id: int, authorization: str = Header(None)):
    get_current_admin(authorization)
    return ff.list_group_members(group_id)


@router.post("/groups/{group_id}/members")
async def add_member(group_id: int, body: MemberAddRequest, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    identifier = body.identifier.strip()

    if body.source == "terra":
        patient = get_patient_by_login(identifier)
        if not patient:
            raise HTTPException(status_code=404, detail="პაციენტი ამ login-ით ვერ მოიძებნა")
        ff.add_group_member(group_id, "terra", patient["id"], patient["full_name"])
        return {"status": "ok", "full_name": patient["full_name"]}

    if body.source == "onec":
        patient = find_patient_by_personal_id(identifier)
        if not patient:
            raise HTTPException(status_code=404, detail="პაციენტი ამ პირადი ნომრით ვერ მოიძებნა")
        ff.add_group_member(group_id, "onec", patient["ref_key"], patient["full_name"])
        return {"status": "ok", "full_name": patient["full_name"]}

    raise HTTPException(status_code=400, detail="source უნდა იყოს 'terra' ან 'onec'")


@router.delete("/groups/{group_id}/members/{member_id}")
async def remove_member(group_id: int, member_id: int, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    ff.remove_group_member(member_id)
    return {"status": "ok"}


# ============ კლინიკის პარამეტრები (მთავარი) ============

@router.get("/settings")
async def get_settings(authorization: str = Header(None)):
    get_current_admin(authorization)
    return cs.get_settings()


@router.put("/settings")
async def put_settings(body: SettingsUpdateRequest, request: Request, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    values = {k: v for k, v in body.dict().items() if v is not None}
    updated = cs.update_settings(values)
    audit.log_event("admin", admin["username"], _client_ip(request), "update_settings", str(values))
    return updated


# ============ ადმინის მომხმარებლები (მხოლოდ superadmin) ============

@router.get("/users")
async def get_admin_users(authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    return list_admin_users()


@router.post("/users")
async def add_admin_user(body: AdminUserCreateRequest, request: Request, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    if body.role not in ("superadmin", "manager", "viewer"):
        raise HTTPException(status_code=400, detail="role უნდა იყოს superadmin, editor ან viewer")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="პაროლი უნდა იყოს მინიმუმ 8 სიმბოლო")
    try:
        new_id = create_admin_user(body.username, body.password, body.role)
    except Exception:
        raise HTTPException(status_code=409, detail="ეს username უკვე დაკავებულია")
    audit.log_event("admin", admin["username"], _client_ip(request), "create_admin_user", body.username)
    return {"id": new_id, "username": body.username, "role": body.role}


@router.put("/users/{admin_id}")
async def edit_admin_user(admin_id: int, body: AdminUserUpdateRequest, request: Request, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    if body.role is not None and body.role not in ("superadmin", "manager", "viewer"):
        raise HTTPException(status_code=400, detail="role უნდა იყოს superadmin, editor ან viewer")
    if body.password and len(body.password) < 8:
        raise HTTPException(status_code=400, detail="პაროლი უნდა იყოს მინიმუმ 8 სიმბოლო")
    ok = update_admin_user(admin_id, role=body.role, password=body.password)
    if not ok:
        raise HTTPException(status_code=404, detail="მომხმარებელი ვერ მოიძებნა")
    audit.log_event("admin", admin["username"], _client_ip(request), "update_admin_user", str(admin_id))
    return {"status": "ok"}


@router.delete("/users/{admin_id}")
async def remove_admin_user(admin_id: int, request: Request, authorization: str = Header(None)):
    admin = get_current_admin(authorization)
    require_role(admin, "superadmin")
    if str(admin_id) == str(admin["id"]):
        raise HTTPException(status_code=400, detail="საკუთარი ანგარიშის წაშლა ამ გვერდიდან არ შეიძლება")
    try:
        ok = delete_admin_user(admin_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="მომხმარებელი ვერ მოიძებნა")
    audit.log_event("admin", admin["username"], _client_ip(request), "delete_admin_user", str(admin_id))
    return {"status": "ok"}


# ============ აუდიტ ლოგი ============

@router.get("/audit")
async def get_audit_log(authorization: str = Header(None), before_id: int = None, limit: int = 100):
    admin = get_current_admin(authorization)
    require_role(admin, "manager")
    limit = min(max(limit, 1), 200)
    return audit.list_events(limit=limit, before_id=before_id)