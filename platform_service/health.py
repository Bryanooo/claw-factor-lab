import sys
from .db import connect
with connect() as conn:
    row=conn.execute("SELECT updated_at>now()-interval '60 seconds' AS healthy FROM heartbeats WHERE name=%s",(sys.argv[1],)).fetchone()
sys.exit(0 if row and row['healthy'] else 1)
