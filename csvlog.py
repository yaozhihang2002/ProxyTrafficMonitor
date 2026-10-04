"""Continuous 10-second delta CSV, recoverable append and bounded rotating files."""
import csv, hashlib, io, json, os, time
from pathlib import Path
from datetime import datetime

FIELDS=['record_id','interval_start','interval_end','source','process','path','parent','parent_path','pid','created','host','ip','port','network','chains','binding','upload_bytes','download_bytes']
CHUNK_BYTES=32*1024**2
PENDING_LIMIT=100000

def backlog(db):
    return db.execute('SELECT count(*),coalesce(sum(length(info)),0) FROM csv_pending').fetchone()

def flush(connect,path,config,now=None,force=False):
    now=now or time.time()
    with connect(path) as db:
        values={r[0]:json.loads(r[1]) for r in db.execute("SELECT key,value FROM meta WHERE key IN ('csv_blocked','csv_retry_at')")}
    if values.get('csv_blocked') and now<values.get('csv_retry_at',0) and not force:return False
    try:
        _flush(connect,path,config,now,force)
        with connect(path) as db:
            db.execute("INSERT INTO meta VALUES('csv_blocked','false') ON CONFLICT(key) DO UPDATE SET value='false'")
        return True
    except PermissionError:
        with connect(path) as db:
            db.execute("INSERT INTO meta VALUES('csv_blocked','true') ON CONFLICT(key) DO UPDATE SET value='true'")
            db.execute("INSERT INTO meta VALUES('csv_retry_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(now+5),))
        return False

def queue_delta(db,info,down,up,now):
    bucket=int(now//10)*10
    stable={k:info.get(k,'') for k in ('source','process','path','parent','parent_path','pid','created','host','ip','port','network','chains','binding')}
    key=hashlib.sha256((str(bucket)+json.dumps(stable,sort_keys=True)).encode()).hexdigest()
    db.execute('''INSERT INTO csv_pending VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      down=csv_pending.down+excluded.down,up=csv_pending.up+excluded.up''',(key,bucket,json.dumps(stable,ensure_ascii=False),down,up))

def cell(value):
    if isinstance(value,list):value=' <- '.join(value)
    if isinstance(value,str) and value[:1] in ('=','+','-','@'):return "'"+value
    return value

def _flush(connect,path,config,now=None,force=False):
    now=now or time.time();folder=Path(path).parent/'csv';folder.mkdir(exist_ok=True)
    # A journal marks the pre-append offset. After a crash truncate the incomplete
    # append before replay; once SQLite acknowledges, the append is never replayed.
    with connect(path) as db:
        pending=db.execute("SELECT value FROM meta WHERE key='csv_append'").fetchone()
        if pending:
            tx=json.loads(pending[0]);file=folder/tx['name']
            if file.exists():
                with file.open('r+b') as f:f.truncate(tx['offset']);f.flush();os.fsync(f.fileno())
            elif tx['offset']:
                raise RuntimeError('CSV 文件被外部移走，无法恢复追加，请恢复该文件')
            db.execute("DELETE FROM meta WHERE key='csv_append'")
        rows=db.execute('SELECT * FROM csv_pending WHERE bucket<? ORDER BY bucket,id LIMIT 1000',(now if force else int(now//10)*10,)).fetchall()
    if not rows:return
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=FIELDS,lineterminator='\n')
    for r in rows:
        info=json.loads(r['info']);writer.writerow({k:cell(v) for k,v in {**info,'record_id':r['id'],'interval_start':datetime.fromtimestamp(r['bucket']).isoformat(),
            'interval_end':datetime.fromtimestamp(r['bucket']+10).isoformat(),'upload_bytes':r['up'],'download_bytes':r['down']}.items()})
    data=stream.getvalue().encode('utf-8')
    header=io.StringIO();csv.writer(header,lineterminator='\n').writerow(FIELDS);header_data=b'\xef\xbb\xbf'+header.getvalue().encode('utf-8')
    prefix=datetime.fromtimestamp(now).strftime('proxy-%Y-%m-%d-')
    files=sorted(folder.glob(prefix+'*.csv'));file=files[-1] if files else folder/(prefix+'0001.csv')
    if file.exists() and file.stat().st_size+len(data)>CHUNK_BYTES:
        index=int(file.stem.rsplit('-',1)[1])+1;file=folder/(prefix+f'{index:04d}.csv')
    offset=file.stat().st_size if file.exists() else 0
    with connect(path) as db:
        db.execute("INSERT INTO meta VALUES('csv_append',?)",(json.dumps({'name':file.name,'offset':offset}),))
    with file.open('ab') as f:
        if not offset:f.write(header_data)
        f.write(data);f.flush();os.fsync(f.fileno())
    with connect(path) as db:
        db.executemany('DELETE FROM csv_pending WHERE id=?',[(r['id'],) for r in rows])
        db.execute("DELETE FROM meta WHERE key='csv_append'")
        db.execute("INSERT INTO meta VALUES('csv_last_write',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(now),))
    # Only generated files in this exact directory are subject to rotation.
    files=sorted(folder.glob('proxy-????-??-??-????.csv'))
    total=sum(f.stat().st_size for f in files);deleted=0
    for old in files:
        if old==file:continue
        if old.stat().st_mtime<now-config['csv_days']*86400 or total>config['csv_budget_mb']*1024**2:
            size=old.stat().st_size
            try:old.unlink()
            except PermissionError:continue
            total-=size;deleted+=1
    if deleted:
        with connect(path) as db:db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(now,'csv_rotation',f'已按保留策略删除 {deleted} 个旧 CSV 分卷；累计量保留'))

def usage(path):
    folder=Path(path).parent/'csv';files=list(folder.glob('proxy-????-??-??-????.csv'))
    return {'folder':str(folder),'files':len(files),'bytes':sum(p.stat().st_size for p in files)}
