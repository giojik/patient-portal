"""
კლინიკის ზოგადი პარამეტრები (მთავარი მენიუ ადმინის პანელში):
სასაათო სარტყელი, კლინიკის სახელი, მისამართი, საიტი, ელ. ფოსტა.
ერთადერთი მწკრივი ცხრილში (id=1).
"""
import os
import psycopg2

PG_DSN = os.environ["PORTAL_DB_DSN"]

_FIELDS = ["timezone", "clinic_name", "address", "website", "email"]


def get_settings() -> dict:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT timezone, clinic_name, address, website, email FROM clinic_settings WHERE id = 1"
        )
        row = cur.fetchone()
        if not row:
            return {f: "" for f in _FIELDS}
        return dict(zip(_FIELDS, row))
    finally:
        con.close()


def update_settings(values: dict) -> dict:
    """values: {field: new_value} — მხოლოდ ცნობილი ველები დაშვებულია."""
    updates = {k: v for k, v in values.items() if k in _FIELDS}
    if not updates:
        return get_settings()

    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        cur.execute(
            f"UPDATE clinic_settings SET {set_clause}, updated_at = now() WHERE id = 1",
            list(updates.values()),
        )
        con.commit()
        return get_settings()
    finally:
        con.close()