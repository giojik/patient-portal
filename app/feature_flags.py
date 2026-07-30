"""
ფუნქციების გადამრთველები (feature flags). გლობალურ ჩართვა/გამორთვას
კატეგორიის მიხედვით (lab, radiology, forma100, prescription, ...)
ემატება არჩევითი override კონკრეტული პაციენტთა ჯგუფისთვის.

წესი კონფლიქტისას: თუ პაციენტი რამდენიმე ჯგუფშია სხვადასხვა
override-ით, გამორთვა იმარჯვებს (fail-closed, არა fail-open).
"""
import os
import psycopg2

from app.crypto import encrypt_field, decrypt_field

PG_DSN = os.environ["PORTAL_DB_DSN"]


def get_effective_flags(source: str, subject_ref: str) -> dict:
    """{feature_key: enabled} კონკრეტული პაციენტისთვის, ჯგუფის override-ების გათვალისწინებით."""
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT feature_key, enabled FROM feature_flags")
        flags = {key: enabled for key, enabled in cur.fetchall()}

        cur.execute(
            "SELECT group_id FROM patient_group_members WHERE source = %s AND subject_ref = %s",
            (source, str(subject_ref)),
        )
        group_ids = [row[0] for row in cur.fetchall()]
        if not group_ids:
            return flags

        cur.execute(
            "SELECT feature_key, enabled FROM feature_flag_overrides WHERE group_id = ANY(%s)",
            (group_ids,),
        )
        overrides: dict = {}
        for key, enabled in cur.fetchall():
            overrides[key] = overrides.get(key, True) and enabled
        flags.update(overrides)
        return flags
    finally:
        con.close()


def is_enabled(source: str, subject_ref: str, feature_key: str) -> bool:
    """
    ცნობილი კატეგორიისთვის (lab/radiology/...) აბრუნებს ჩართულ/გამორთულს.
    კატეგორია, რომელიც feature_flags-ში საერთოდ არ ფიგურირებს, ნაგულისხმევად
    ჩართულია — ეს ხელს უშლის ახალი, ჯერ არ დარეგისტრირებული კატეგორიის
    შემთხვევით დამალვას.
    """
    return get_effective_flags(source, subject_ref).get(feature_key, True)


# ============ ადმინის CRUD: ფუნქციები ============

def list_features_with_overrides() -> list:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT feature_key, label, enabled FROM feature_flags ORDER BY feature_key")
        features = [
            {"feature_key": key, "label": label, "enabled": enabled, "overrides": []}
            for key, label, enabled in cur.fetchall()
        ]
        by_key = {f["feature_key"]: f for f in features}

        cur.execute(
            """
            SELECT o.feature_key, o.group_id, g.name, o.enabled
            FROM feature_flag_overrides o
            JOIN patient_groups g ON g.id = o.group_id
            ORDER BY g.name
            """
        )
        for feature_key, group_id, group_name, enabled in cur.fetchall():
            if feature_key in by_key:
                by_key[feature_key]["overrides"].append(
                    {"group_id": group_id, "group_name": group_name, "enabled": enabled}
                )
        return features
    finally:
        con.close()


def set_global_flag(feature_key: str, enabled: bool) -> bool:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "UPDATE feature_flags SET enabled = %s, updated_at = now() WHERE feature_key = %s",
            (enabled, feature_key),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def set_override(feature_key: str, group_id: int, enabled: bool):
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO feature_flag_overrides (feature_key, group_id, enabled)
            VALUES (%s, %s, %s)
            ON CONFLICT (feature_key, group_id) DO UPDATE SET enabled = EXCLUDED.enabled
            """,
            (feature_key, group_id, enabled),
        )
        con.commit()
    finally:
        con.close()


def remove_override(feature_key: str, group_id: int):
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM feature_flag_overrides WHERE feature_key = %s AND group_id = %s",
            (feature_key, group_id),
        )
        con.commit()
    finally:
        con.close()


# ============ ადმინის CRUD: ჯგუფები ============

def list_groups() -> list:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT g.id, g.name, COUNT(m.id)
            FROM patient_groups g
            LEFT JOIN patient_group_members m ON m.group_id = g.id
            GROUP BY g.id, g.name
            ORDER BY g.name
            """
        )
        return [{"id": i, "name": n, "member_count": c} for i, n, c in cur.fetchall()]
    finally:
        con.close()


def create_group(name: str) -> int:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("INSERT INTO patient_groups (name) VALUES (%s) RETURNING id", (name,))
        group_id = cur.fetchone()[0]
        con.commit()
        return group_id
    finally:
        con.close()


def delete_group(group_id: int):
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM patient_groups WHERE id = %s", (group_id,))
        con.commit()
    finally:
        con.close()


def list_group_members(group_id: int) -> list:
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, source, subject_ref, display_name_enc FROM patient_group_members "
            "WHERE group_id = %s ORDER BY added_at",
            (group_id,),
        )
        members = []
        for member_id, source, subject_ref, name_enc in cur.fetchall():
            members.append(
                {
                    "id": member_id,
                    "source": source,
                    "subject_ref": subject_ref,
                    "display_name": decrypt_field(name_enc) if name_enc else "",
                }
            )
        return members
    finally:
        con.close()


def add_group_member(group_id: int, source: str, subject_ref: str, display_name: str = ""):
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO patient_group_members (group_id, source, subject_ref, display_name_enc)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (group_id, source, subject_ref) DO NOTHING
            """,
            (group_id, source, str(subject_ref), encrypt_field(display_name) if display_name else None),
        )
        con.commit()
    finally:
        con.close()


def remove_group_member(member_id: int):
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM patient_group_members WHERE id = %s", (member_id,))
        con.commit()
    finally:
        con.close()