"""
Terra Lab (Firebird/Interbase) მონაცემთა ბაზასთან დაკავშირების მაგალითი.

გაუშვით ამ სკრიპტს იმ მანქანიდან/ქსელიდან, სადაც db=terra ხელმისაწვდომია
(პორტი 3050 ღია უნდა იყოს firewall-ში).
"""

from firebird.driver import connect, DatabaseError

# --- დაკავშირების პარამეტრები ---
# რეკომენდაცია: password გაიტანეთ environment variable-ში, არა კოდში პირდაპირ
DSN = "10.10.5.249/3050:terra25"   # host/port:database
USER = "u_sysconnect"
PASSWORD = "1234"            # !!! აუცილებლად შეცვალეთ რეალურ პაროლზე

def get_connection():
    try:
        con = connect(DSN, user=USER, password=PASSWORD)
        print("წარმატებით დავუკავშირდით ბაზას:", DSN)
        return con
    except DatabaseError as e:
        print("დაკავშირების შეცდომა:", e)
        raise


def list_tables(con):
    """ცხრილების სია (system table-ების გარეშე)."""
    cur = con.cursor()
    cur.execute("""
        SELECT TRIM(RDB$RELATION_NAME)
        FROM RDB$RELATIONS
        WHERE RDB$SYSTEM_FLAG = 0
        ORDER BY RDB$RELATION_NAME
    """)
    return [row[0] for row in cur.fetchall()]


def run_query(con, sql, params=None):
    """SELECT query-ის შესრულება, აბრუნებს (columns, rows)."""
    cur = con.cursor()
    cur.execute(sql, params or [])
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    return columns, rows


if __name__ == "__main__":
    con = get_connection()
    try:
        print("\n--- ცხრილების სია ---")
        for t in list_tables(con):
            print(" -", t)

        # მაგალითი: კონკრეტული ცხრილიდან პირველი 20 row-ის ამოღება
        # columns, rows = run_query(con, "SELECT FIRST 20 * FROM TABLE_NAME")
        # print(columns)
        # for r in rows:
        #     print(r)

    finally:
        con.close()
        print("\nკავშირი დაიხურა.")