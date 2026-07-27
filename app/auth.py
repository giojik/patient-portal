"""
პაციენტის ვერიფიკაცია LOGIN/PASS მექანიზმით (Terra-ს არსებული ველები).
"""
from app.db import get_connection


def verify_patient(login: str, password: str):
    con = get_connection()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT ID, SURNAME, NAME FROM DIC_CLIENTS "
            "WHERE LOGIN = ? AND PASS = ? AND IS_ACTIVE = 1",
            (login, password),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"client_id": row[0], "surname": row[1], "name": row[2]}
    finally:
        con.close()
