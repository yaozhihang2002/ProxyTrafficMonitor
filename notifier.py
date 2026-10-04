"""Fresh local tray notices, including the Tcl-free tray fallback."""
import json, sqlite3, threading, time
from contextlib import closing

def start(icon, path):
    stop=threading.Event()
    with closing(sqlite3.connect(str(path),timeout=2)) as db:
        cursor=db.execute('SELECT coalesce(max(id),0) FROM events').fetchone()[0]
    def run():
        nonlocal cursor
        while not stop.wait(3):
            try:
                with closing(sqlite3.connect(str(path),timeout=2)) as db:
                    notices=db.execute("SELECT id,ts,detail FROM events WHERE id>? AND kind='traffic_alert' ORDER BY id LIMIT 5",(cursor,)).fetchall()
                for ident,ts,raw in notices:
                    cursor=ident
                    if time.time()-ts>15:continue
                    detail=json.loads(raw)
                    icon.notify(f"{detail['program']}：约 {detail['minutes']} 分钟 {detail['bytes']/1024**2:.1f} MiB",'代理流量提醒')
            except Exception:
                # Shell notifications are best-effort; the event remains in SQLite.
                pass
    thread=threading.Thread(target=run,daemon=True);thread.start()
    def shutdown():
        stop.set();thread.join(timeout=3)
    return shutdown
