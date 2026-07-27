import os
from firebird.driver import connect


def get_connection():
    return connect(
        os.environ["DB_DSN"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )