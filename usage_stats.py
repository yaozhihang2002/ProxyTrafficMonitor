"""Durable local-calendar totals, independent of retained connection detail."""
import calendar, re
from datetime import datetime


_PROGRAM = "coalesce(json_extract(info,'$.process'),'未知程序')"
_PROGRAM_INDEX = 'ix_totals_day_program_bytes'


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS usage_daily(
        day TEXT PRIMARY KEY, down INTEGER NOT NULL, up INTEGER NOT NULL,
        samples INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0,
        imported INTEGER NOT NULL DEFAULT 0)''')
    # Browse the narrow index rather than fetching a connection JSON record for each
    # retained target in the selected day or month. SQLite maintains it when detail expires.
    db.execute(f'''CREATE INDEX IF NOT EXISTS {_PROGRAM_INDEX}
        ON totals(day,{_PROGRAM},down,up)''')
    if not db.execute("SELECT 1 FROM meta WHERE key='usage_migrated'").fetchone():
        # Retained detail can already be pruned: never call this complete history.
        db.execute('''INSERT OR IGNORE INTO usage_daily(day,down,up,imported)
            SELECT day,sum(down),sum(up),1 FROM totals GROUP BY day''')
        db.execute("INSERT INTO meta VALUES('usage_migrated','true')")


def record(db, now, down, up, last, gap):
    current = datetime.fromtimestamp(now)
    day = current.strftime('%Y-%m-%d')
    seconds = max(0, min(now-last, now-current.replace(hour=0,minute=0,second=0,microsecond=0).timestamp())) if last and not gap else 0
    # As with connection accounting, a recovered delta belongs to its sample day.
    db.execute('''INSERT INTO usage_daily(day,down,up,samples,seconds) VALUES(?,?,?,1,?)
        ON CONFLICT(day) DO UPDATE SET down=down+excluded.down,up=up+excluded.up,
        samples=samples+1,seconds=seconds+excluded.seconds''',(day,down,up,seconds))


def report(db, month='', now=None):
    current = datetime.fromtimestamp(now) if now is not None else datetime.now()
    try:
        selected = datetime.strptime(month,'%Y-%m')
    except (ValueError,TypeError):
        selected = current.replace(day=1)
    month = selected.strftime('%Y-%m')
    year = selected.strftime('%Y')
    def aggregate(prefix):
        row = db.execute('''SELECT sum(down) down,sum(up) up,sum(samples) samples,
            sum(seconds) seconds,max(imported) imported FROM usage_daily WHERE day LIKE ?''',(prefix+'%',)).fetchone()
        return {k: (row[k] or 0) for k in row.keys()} | {'recorded':row['down'] is not None}
    daily = {r['day']:dict(r) for r in db.execute('SELECT * FROM usage_daily WHERE day LIKE ?',(month+'%',))}
    days=[]
    for n in range(1,calendar.monthrange(selected.year,selected.month)[1]+1):
        day=f'{month}-{n:02d}'
        days.append({'period':day,'recorded':day in daily,**daily.get(day,{'down':0,'up':0,'samples':0,'seconds':0,'imported':0})})
    return {'month':month,'year':year,'today':aggregate(current.strftime('%Y-%m-%d')),
            'this_month':aggregate(current.strftime('%Y-%m')),'days':days,
            'months':[{'period':f'{year}-{n:02d}',**aggregate(f'{year}-{n:02d}')} for n in range(1,13)]}


def programs(db, period):
    if not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?',period):
        return []
    # Detail retention applies; this is never presented as a full breakdown.
    # Keep the scan separate from GROUP BY: flattening would make SQLite read
    # the original info JSON into its grouping records despite the index.
    rows=db.execute(f'''WITH period_rows AS MATERIALIZED (
            SELECT {_PROGRAM} process, down, up FROM totals INDEXED BY {_PROGRAM_INDEX}
            WHERE day>=? AND day<?
        )
        SELECT process, sum(down) down, sum(up) up FROM period_rows
        GROUP BY process ORDER BY sum(down)+sum(up) DESC LIMIT 100''',
        (period,period+'~'))
    return [dict(r) for r in rows]
