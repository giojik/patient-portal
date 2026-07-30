"""
აუდიტ ლოგი: ვინ შემოვიდა, საიდან (IP), რა ნახა/ჩამოტვირთა და როდის.
პაციენტის მოქმედებებზე ინახავს დაშიფრულ პირად ნომერს და სახელს/გვარს
(იგივე AES-256-GCM, რაც patients/results ცხრილებში) — გაშიფვრა ხდება
მხოლოდ ავტორიზებული ადმინის პანელში ჩვენებისას.

log_event() არასდროს არ უნდა ისროლოს გამონაკლისი მთავარი მოთხოვნისკენ —
თუ ბაზასთან კავშირი ჩავარდა, აუდიტის ჩანაწერის დაკარგვა არ უნდა
შეაჩეროს რეალური ფუნქციონალი (login, ჩამოტვირთვა და ა.შ.).
"""
import os
import logging
import psycopg2

from app.crypto import encrypt_field, decrypt_field

PG_DSN = os.environ["PORTAL_DB_DSN"]
logger = logging.getLogger("audit")


def log_event(
    actor_type: str,
    actor_label: str,
    ip_address: str,
    action: str,
    details: str = None,
    personal_id: str = None,
    full_name: str = None,
):
    try:
        con = psycopg2.connect(PG_DSN)
        try:
            cur = con.cursor()
            cur.execute(
                """
                INSERT INTO audit_log
                    (actor_type, actor_label, ip_address, action, details,
                     patient_personal_id_enc, patient_full_name_enc)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    actor_type, actor_label, ip_address, action, details,
                    encrypt_field(personal_id) if personal_id else None,
                    encrypt_field(full_name) if full_name else None,
                ),
            )
            con.commit()
        finally:
            con.close()
    except Exception:
        logger.exception("აუდიტ ლოგის ჩაწერა ჩავარდა (%s / %s)", actor_type, action)


def _safe_decrypt(token):
    if not token:
        return None
    try:
        return decrypt_field(token)
    except Exception:
        return None


def list_events(limit: int = 100, before_id: int = None) -> list:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        base_query = """
            SELECT id, occurred_at, actor_type, actor_label, ip_address, action, details,
                   patient_personal_id_enc, patient_full_name_enc
            FROM audit_log {where} ORDER BY id DESC LIMIT %s
        """
        if before_id:
            cur.execute(base_query.format(where="WHERE id < %s"), (before_id, limit))
        else:
            cur.execute(base_query.format(where=""), (limit,))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "occurred_at": r[1].isoformat() if r[1] else None,
                "actor_type": r[2],
                "actor_label": r[3],
                "ip_address": r[4],
                "action": r[5],
                "details": r[6],
                "patient_personal_id": _safe_decrypt(r[7]),
                "patient_full_name": _safe_decrypt(r[8]),
            }
            for r in rows
        ]
    finally:
        con.close()