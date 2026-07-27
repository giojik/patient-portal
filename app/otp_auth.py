"""
ერთჯერადი კოდის (OTP) გენერაცია/ვერიფიკაცია 1C-დან ნაპოვნი პაციენტისთვის.

⚠️ ამ ეტაპზე SMS ჯერ არ არის ინტეგრირებული — request_code() აბრუნებს
კოდს პასუხშივე ტესტირებისთვის (`_debug_code`). SMS პროვაიდერის
არჩევის შემდეგ ეს უნდა ჩანაცვლდეს რეალური გაგზავნით და `_debug_code`
მოიხსნას პასუხიდან.
"""
import os
import secrets
import psycopg2
from datetime import datetime, timedelta

from app.crypto import encrypt_field, decrypt_field, hash_lookup
from app.onec_client import find_patient_by_personal_id

PG_DSN = os.environ["PORTAL_DB_DSN"]
OTP_EXPIRY_MINUTES = int(os.environ.get("OTP_EXPIRY_MINUTES", "5"))
MAX_OTP_ATTEMPTS = int(os.environ.get("MAX_OTP_ATTEMPTS", "5"))


def _mask_phone(phone):
    if not phone or len(phone) < 4:
        return "****"
    return f"***{phone[-4:]}"


def request_code(personal_id: str):
    """
    პოულობს პაციენტს 1C-ში, აგენერირებს 6-ციფრიან კოდს და ინახავს დროებით.
    აბრუნებს None, თუ პაციენტი ვერ მოიძებნა.
    """
    patient = find_patient_by_personal_id(personal_id)
    if not patient:
        return None

    code = f"{secrets.randbelow(1000000):06d}"
    personal_id_hash = hash_lookup(personal_id.strip())
    expires_at = datetime.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)

    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM otp_codes WHERE personal_id_hash = %s", (personal_id_hash,))
        cur.execute(
            """
            INSERT INTO otp_codes
                (personal_id_hash, code_hash, onec_ref, full_name_enc, phone_enc, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                personal_id_hash,
                hash_lookup(code),
                patient["ref_key"],
                encrypt_field(patient["full_name"]),
                encrypt_field(patient["phone"] or ""),
                expires_at,
            ),
        )
        con.commit()
    finally:
        con.close()

    # TODO: SMS პროვაიდერის არჩევის შემდეგ: send_sms(patient["phone"], code)
    #       და წავშალოთ "_debug_code" პასუხიდან.
    return {
        "phone_hint": _mask_phone(patient["phone"]),
        "_debug_code": code,
    }


def verify_code(personal_id: str, code: str):
    """აბრუნებს {"onec_ref": ..., "full_name": ...} თუ ვალიდურია, სხვანაირად None."""
    personal_id_hash = hash_lookup(personal_id.strip())
    code = code.strip()

    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, code_hash, onec_ref, full_name_enc, expires_at, attempts "
            "FROM otp_codes WHERE personal_id_hash = %s",
            (personal_id_hash,),
        )
        row = cur.fetchone()
        if not row:
            return None
        otp_id, code_hash, onec_ref, full_name_enc, expires_at, attempts = row

        if datetime.now() > expires_at:
            cur.execute("DELETE FROM otp_codes WHERE id = %s", (otp_id,))
            con.commit()
            return None

        if attempts >= MAX_OTP_ATTEMPTS:
            return None

        if hash_lookup(code) != code_hash:
            cur.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = %s", (otp_id,))
            con.commit()
            return None

        cur.execute("DELETE FROM otp_codes WHERE id = %s", (otp_id,))
        con.commit()

        return {"onec_ref": onec_ref, "full_name": decrypt_field(full_name_enc)}
    finally:
        con.close()