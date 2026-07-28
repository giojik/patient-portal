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
    if len(sys.argv) != 2:
        print("გამოყენება: python3 create_admin.py <username>")
        sys.exit(1)

    username = sys.argv[1].strip()
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
            INSERT INTO admin_users (username, password_hash) VALUES (%s, %s)
            ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash
            """,
            (username, password_hash),
        )
        con.commit()
        print(f"ადმინის მომხმარებელი '{username}' მზადაა.")
    finally:
        con.close()


if __name__ == "__main__":
    main()