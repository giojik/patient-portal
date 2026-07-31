import os
import psycopg2

from app.crypto import decrypt_field, hash_lookup

PG_DSN = os.environ["PORTAL_DB_DSN"]


def get_patient_by_login(login: str):
    """იძებნება login_hash-ით, აბრუნებს (id, full_name) ან None."""
    login = login.strip()
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, full_name_enc FROM patients WHERE login_hash = %s",
            (hash_lookup(login),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "full_name": decrypt_field(row[1])}
    finally:
        con.close()


def _compute_status(result_value: str, low_enc, high_enc, is_out_of_norm) -> str:
    """
    აბრუნებს: 'abnormal' (წითელი), 'borderline' (ყვითელი),
    'normal' (მწვანე), ან 'unknown' (ფერის გარეშე — არ არსებობს
    საკმარისი ინფორმაცია შესადარებლად, მაგ. ტექსტური შედეგი).
    """
    if is_out_of_norm is True:
        return "abnormal"

    if low_enc is None or high_enc is None:
        return "unknown"

    try:
        value = float(result_value)
        low = float(decrypt_field(low_enc))
        high = float(decrypt_field(high_enc))
    except (ValueError, TypeError):
        return "unknown"

    if high <= low:
        return "unknown"

    if value < low or value > high:
        return "abnormal"

    # ზღვართან ახლოს (10% დიაპაზონის სიგანისგან) — ყვითელი
    margin = (high - low) * 0.1
    if value <= low + margin or value >= high - margin:
        return "borderline"

    return "normal"


def _mask_login(login: str) -> str:
    """ბოლო 3 სიმბოლოს გარდა ყველაფერს ფარავს (მაგ. "*******123")."""
    if not login:
        return ""
    if len(login) <= 3:
        return "*" * len(login)
    return "*" * (len(login) - 3) + login[-3:]


def get_terra_profile(patient_id: int):
    """
    Read-only პროფილის მონაცემები Terra წყაროსთვის.
    'patients' ცხრილში ამჟამად მხოლოდ სახელი და login ინახება —
    პირადი ნომერი/დაბადების თარიღი/მისამართი ჯერ არ არის სინქრონიზებული.
    """
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT full_name_enc, login_enc FROM patients WHERE id = %s",
            (patient_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        full_name_enc, login_enc = row
        return {
            "full_name": decrypt_field(full_name_enc),
            "login_masked": _mask_login(decrypt_field(login_enc)),
            "source": "terra",
        }
    finally:
        con.close()


def get_patient_full_name(patient_id: int) -> str:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT full_name_enc FROM patients WHERE id = %s", (patient_id,))
        row = cur.fetchone()
        return decrypt_field(row[0]) if row else ""
    finally:
        con.close()


def get_results_for_patient(patient_id: int):
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT panel_group_id, panel_name_enc, test_name_enc, result_value_enc, unit_enc,
                   norm_low_enc, norm_high_enc, is_out_of_norm, sample_date
            FROM results
            WHERE patient_id = %s
            ORDER BY sample_date DESC
            """,
            (patient_id,),
        )
        results = []
        for (panel_group_id, panel_name_enc, test_name_enc, result_value_enc, unit_enc,
             low_enc, high_enc, is_out_of_norm, sample_date) in cur.fetchall():
            result_value = decrypt_field(result_value_enc)
            results.append(
                {
                    "panel_group_id": panel_group_id,
                    "panel_name": decrypt_field(panel_name_enc) if panel_name_enc else "",
                    "category": "lab",
                    "test_name": decrypt_field(test_name_enc),
                    "result_value": result_value,
                    "unit": decrypt_field(unit_enc) if unit_enc else "",
                    "norm_low": decrypt_field(low_enc) if low_enc else None,
                    "norm_high": decrypt_field(high_enc) if high_enc else None,
                    "status": _compute_status(result_value, low_enc, high_enc, is_out_of_norm),
                    "sample_date": sample_date.isoformat(),
                }
            )
        return results
    finally:
        con.close()


def get_panel_results(patient_id: int, panel_group_id: str):
    """
    ერთი პანელის (მაგ. ერთი შეკვეთის ფარგლებში ჩატარებული ანალიტების)
    შედეგები — PDF report-ის გენერაციისთვის. patient_id-ს ფილტრი
    უზრუნველყოფს, რომ ვერავინ ვერ ნახოს სხვისი პანელი, თუნდაც
    panel_group_id-ს იცნობდეს.
    """
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT panel_name_enc, test_name_enc, result_value_enc, unit_enc,
                   norm_low_enc, norm_high_enc, is_out_of_norm, sample_date
            FROM results
            WHERE patient_id = %s AND panel_group_id = %s
            ORDER BY id
            """,
            (patient_id, panel_group_id),
        )
        rows = cur.fetchall()
        if not rows:
            return None

        panel_name = decrypt_field(rows[0][0]) if rows[0][0] else "კვლევის შედეგი"
        sample_date = rows[0][7]

        items = []
        for (_, test_name_enc, result_value_enc, unit_enc, low_enc, high_enc,
             is_out_of_norm, _sample_date) in rows:
            result_value = decrypt_field(result_value_enc)
            items.append(
                {
                    "test_name": decrypt_field(test_name_enc),
                    "result_value": result_value,
                    "unit": decrypt_field(unit_enc) if unit_enc else "",
                    "norm_low": decrypt_field(low_enc) if low_enc else None,
                    "norm_high": decrypt_field(high_enc) if high_enc else None,
                    "status": _compute_status(result_value, low_enc, high_enc, is_out_of_norm),
                }
            )

        return {
            "panel_name": panel_name,
            "sample_date": sample_date,
            "items": items,
        }
    finally:
        con.close()