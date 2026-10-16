import time
from datetime import timedelta
from .db import connect, enqueue, event, heartbeat, init_db


def tick():
    with connect() as conn:
        rows=conn.execute('SELECT * FROM schedules WHERE enabled AND next_run_at<=now() ORDER BY next_run_at FOR UPDATE SKIP LOCKED LIMIT 20').fetchall()
        for schedule in rows:
            request=dict(schedule['request'])
            request['idempotency_key']=f'schedule:{schedule["id"]}:{int(schedule["next_run_at"].timestamp())}'
            enqueue(conn,request,origin='schedule')
            # Missed intervals coalesce into one execution. No historical replay storm.
            conn.execute('UPDATE schedules SET next_run_at=now()+%s WHERE id=%s',
                         (timedelta(seconds=schedule['interval_seconds']),schedule['id']))


def main():
    init_db()
    while True:
        try:heartbeat('scheduler');tick()
        except Exception as exc:print('Scheduler error:',type(exc).__name__,flush=True)
        time.sleep(5)


if __name__=='__main__':main()
