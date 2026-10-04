"""Bound retained detail without resetting lifetime counters."""
import json, sqlite3, time
from datetime import datetime
from pathlib import Path

CONNECTION_LIMIT=50000
TOTAL_LIMIT=100000
EVENT_LIMIT=2000

def usage(path):
    path=Path(path)
    return {name:Path(str(path)+suffix).stat().st_size if Path(str(path)+suffix).exists() else 0
            for name,suffix in [('database_bytes',''),('wal_bytes','-wal'),('shm_bytes','-shm')]}

def maintain(db, now, config, connection_limit=CONNECTION_LIMIT, total_limit=TOTAL_LIMIT):
    # Lifetime totals are stored independently in meta, so pruning never resets them.
    db.execute('DELETE FROM connections WHERE last_seen<?',(now-config['connection_days']*86400,))
    cutoff=datetime.fromtimestamp(now-config['total_days']*86400).strftime('%Y-%m-%d')
    db.execute('DELETE FROM totals WHERE day<?',(cutoff,))
    db.execute('DELETE FROM view_totals WHERE day<?',(cutoff,))
    db.execute('DELETE FROM events WHERE ts<?',(now-90*86400,))
    for table,limit,order in [('connections',connection_limit,'last_seen DESC'),('totals',total_limit,'last_seen DESC'),('view_totals',total_limit,'last_seen DESC'),('events',EVENT_LIMIT,'id DESC')]:
        db.execute(f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} ORDER BY {order} LIMIT -1 OFFSET ?)',(limit,))
    # If high-cardinality metadata consumes the target, discard oldest detail earlier.
    page=db.execute('PRAGMA page_size').fetchone()[0]
    used=(db.execute('PRAGMA page_count').fetchone()[0]-db.execute('PRAGMA freelist_count').fetchone()[0])*page
    pressure=used>config['target_mb']*1024**2*.8
    if pressure:
        for table in ('connections','totals','view_totals','activity','alert_activity'):
            count=db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            db.execute(f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} ORDER BY rowid LIMIT ?)',(max(1,count//4),))
    return pressure

def compact(path,target_mb):
    # No long reader or writer transaction survives between samples. VACUUM is only
    # attempted when at least 16 MiB can be reclaimed; status exposes actual files.
    db=sqlite3.connect(str(path),timeout=2)
    try:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        page=db.execute('PRAGMA page_size').fetchone()[0]
        free=db.execute('PRAGMA freelist_count').fetchone()[0]*page
        size=db.execute('PRAGMA page_count').fetchone()[0]*page
        if free>=16*1024**2 and (free>size*.25 or size>target_mb*1024**2):
            db.execute('VACUUM')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:db.close()
