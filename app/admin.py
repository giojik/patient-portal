"""
ადმინის API — ფუნქციების ჩართვა/გამორთვა და პაციენტთა ჯგუფების მართვა.
სრულიად ცალკეა პაციენტის /api/* endpoint-ებისგან: ცალკე ავტორიზაცია
(admin_users + role="admin" JWT claim), ამიტომ პაციენტის token აქ არ მუშაობს.
"""
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
import jwt

from app.admin_auth import verify_admin, create_admin_token, verify_admin_token
from app.portal_queries import get_patient_by_login
from app.onec_client import find_patient_by_personal_id
from app import feature_flags as ff

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


@router.post("/login")
@limiter.limit("5/15minute")
async def admin_login(request: Request, body: AdminLoginRequest):
    admin = verify_admin(body.username, body.password)
    if not admin:
        raise HTTPException(status_code=401, detail="არასწორი მონაცემები")
    token = create_admin_token(admin["id"], admin["username"])
    return {"token": token, "username": admin["username"]}


# ============ ფუნქციების გადამრთველები ============

@router.get("/features")
async def list_features(authorization: str = Header(None)):
    get_current_admin(authorization)
    return ff.list_features_with_overrides()


@router.put("/features/{feature_key}")
async def toggle_feature(feature_key: str, body: FeatureToggleRequest, authorization: str = Header(None)):
    get_current_admin(authorization)
    if not ff.set_global_flag(feature_key, body.enabled):
        raise HTTPException(status_code=404, detail="ეს ფუნქცია ვერ მოიძებნა")
    return {"feature_key": feature_key, "enabled": body.enabled}


@router.put("/features/{feature_key}/overrides/{group_id}")
async def upsert_override(feature_key: str, group_id: int, body: OverrideRequest, authorization: str = Header(None)):
    get_current_admin(authorization)
    ff.set_override(feature_key, group_id, body.enabled)
    return {"feature_key": feature_key, "group_id": group_id, "enabled": body.enabled}


@router.delete("/features/{feature_key}/overrides/{group_id}")
async def delete_override(feature_key: str, group_id: int, authorization: str = Header(None)):
    get_current_admin(authorization)
    ff.remove_override(feature_key, group_id)
    return {"status": "ok"}


# ============ პაციენტთა ჯგუფები ============

@router.get("/groups")
async def get_groups(authorization: str = Header(None)):
    get_current_admin(authorization)
    return ff.list_groups()


@router.post("/groups")
async def add_group(body: GroupCreateRequest, authorization: str = Header(None)):
    get_current_admin(authorization)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="სახელი სავალდებულოა")
    group_id = ff.create_group(name)
    return {"id": group_id, "name": name}


@router.delete("/groups/{group_id}")
async def remove_group(group_id: int, authorization: str = Header(None)):
    get_current_admin(authorization)
    ff.delete_group(group_id)
    return {"status": "ok"}


@router.get("/groups/{group_id}/members")
async def get_group_members(group_id: int, authorization: str = Header(None)):
    get_current_admin(authorization)
    return ff.list_group_members(group_id)


@router.post("/groups/{group_id}/members")
async def add_member(group_id: int, body: MemberAddRequest, authorization: str = Header(None)):
    get_current_admin(authorization)
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
    get_current_admin(authorization)
    ff.remove_group_member(member_id)
    return {"status": "ok"}