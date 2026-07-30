"""
ერთჯერადი CLI სკრიპტი ადმინის მომხმარებლის შესაქმნელად ან პაროლის
შესაცვლელად. საჭირო environment ცვლადები იგივეა, რაც API-ს (PORTAL_DB_DSN).

გამოყენება (container-ის შიგნიდან):
    docker compose exec api python3 create_admin.py <username>
    # მოგთხოვთ პაროლს ინტერაქტიულად (getpass, ტერმინალში არ ჩანს)
"""
import sys
import getpass
import psycopg2

from app.admin_auth import hash_password
import os

PG_DSN = os.environ["PORTAL_DB_DSN"]


def main():
    if len(sys.argv) not in (2, 3):
        print("გამოყენება: python3 create_admin.py <username> [role: superadmin|manager|viewer]")
        sys.exit(1)

    username = sys.argv[1].strip()
    role = sys.argv[2].strip() if len(sys.argv) == 3 else "superadmin"
    if role not in ("superadmin", "manager", "viewer"):
        print("role უნდა იყოს: superadmin, manager ან viewer")
        sys.exit(1)

    password = getpass.getpass("პაროლი: ")
    password_confirm = getpass.getpass("გაიმეორეთ პაროლი: ")
    if password != password_confirm:
        print("პაროლები არ ემთხვევა.")
        sys.exit(1)
    if len(password) < 8:
        print("პაროლი უნდა იყოს მინიმუმ 8 სიმბოლო.")
        sys.exit(1)

    password_hash = hash_password(password)

    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO admin_users (username, password_hash, role) VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role
            """,
            (username, password_hash, role),
        )
        con.commit()
        print(f"ადმინის მომხმარებელი '{username}' ({role}) მზადაა.")
    finally:
        con.close()


if __name__ == "__main__":
    main()