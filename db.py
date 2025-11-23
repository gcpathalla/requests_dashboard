# db.py
import os
import ssl
import pymysql
from pymysql.cursors import DictCursor

# ---------------------------------------
# YOUR TiDB CONFIG (using your provided values)
# ---------------------------------------
DB_HOST = "gateway01.ap-southeast-1.prod.aws.tidbcloud.com"
DB_PORT = 4000
DB_USER = "BQwJzxp59xzGgtW.root"
DB_PASS = "reok1GLqNtfTk3TH"
DB_NAME = "test"

# ---------------------------------------------------------
# SSL CONFIG — NO CA DOWNLOAD REQUIRED
# ---------------------------------------------------------
# Try system default CA bundle. If your corporate system already
# trusts the TiDB CA, this works automatically.
try:
    SYSTEM_CA = ssl.get_default_verify_paths().cafile
except Exception:
    SYSTEM_CA = None

def get_conn():
    ssl_params = {}

    if SYSTEM_CA:
        # Use system CA bundle (recommended when PEM download is blocked)
        ssl_params = {"ca": SYSTEM_CA}
    else:
        # Last-resort fallback: disable verification (not recommended)
        # You will probably not need this.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ssl_params = {"ssl": ctx}

    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=DictCursor,
        autocommit=True,
        ssl=ssl_params
    )
