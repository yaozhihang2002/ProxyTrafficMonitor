"""Local, read-only Mihomo traffic accounting. Python standard library only."""
import argparse, csv, hashlib, io, json, os, queue, sqlite3, subprocess, threading, time, tempfile, shutil, sys
import settings, storage, csvlog, activity, lifecycle
from version import VERSION
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from paths import ROOT, ASSETS
DB = ROOT / 'data' / 'traffic.sqlite3'
PORT = int(os.environ.get('PROXY_MONITOR_PORT','18791'))
PWSH = Path(shutil.which('pwsh') or shutil.which('powershell') or 'powershell.exe')
if getattr(sys,'frozen',False): PWSH=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
RUNTIME_ERROR=None
CSV_ERROR=None
PIPELINE={}

@contextmanager
def connect(path=DB):
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()

def initialize(path=DB):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS connections(
          id TEXT PRIMARY KEY, down INTEGER NOT NULL, up INTEGER NOT NULL,
          first_seen REAL NOT NULL, last_seen REAL NOT NULL, info TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS totals(
          day TEXT NOT NULL, dimension TEXT NOT NULL, info TEXT NOT NULL,
          down INTEGER NOT NULL DEFAULT 0, up INTEGER NOT NULL DEFAULT 0,
          first_seen REAL NOT NULL, last_seen REAL NOT NULL,
          PRIMARY KEY(day,dimension));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS csv_pending(id TEXT PRIMARY KEY,bucket INTEGER,info TEXT,down INTEGER,up INTEGER);
        CREATE INDEX IF NOT EXISTS ix_totals_seen ON totals(last_seen);
        CREATE INDEX IF NOT EXISTS ix_connections_target ON connections(
          json_extract(info,'$.host'),json_extract(info,'$.ip'),json_extract(info,'$.path'),last_seen);
        CREATE INDEX IF NOT EXISTS ix_connections_seen ON connections(last_seen);
        CREATE TABLE IF NOT EXISTS view_totals AS SELECT * FROM totals WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS ix_view_key ON view_totals(day,dimension);
        CREATE INDEX IF NOT EXISTS ix_view_seen ON view_totals(last_seen);
        ''')
        activity.initialize(db,time.time())
        db.execute("INSERT OR IGNORE INTO meta VALUES('started',?)", (str(time.time()),))
        existing=db.execute('SELECT coalesce(sum(down),0),coalesce(sum(up),0) FROM totals').fetchone()
        db.execute("INSERT OR IGNORE INTO meta VALUES('lifetime_down',?)",(str(existing[0]),))
        db.execute("INSERT OR IGNORE INTO meta VALUES('lifetime_up',?)",(str(existing[1]),))

def put(db, key, value):
    db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value, ensure_ascii=False)))

def getmeta(db):
    return {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM meta')}

def dimension(info):
    fields = ('source','host','ip','port','network','process','path','parent','parent_path','binding','chains','rule','rule_payload')
    return hashlib.sha256(json.dumps({k:info.get(k) for k in fields},sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def ingest(snapshot, path=DB, config=None, maintenance=True):
    config=config or settings.load()
    now = snapshot['time']
    day = datetime.fromtimestamp(now).strftime('%Y-%m-%d')
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        meta = getmeta(db)
        if snapshot.get('error'):
            error = snapshot['error'][:500]
            if error != meta.get('error'):
                db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(now,'collector_error',error))
            put(db,'error',error); put(db,'errors',meta.get('errors',0)+1)
            return
        pending_count,pending_bytes=csvlog.backlog(db)
        if pending_count>=csvlog.PENDING_LIMIT or pending_bytes>=128*1024**2:
            raise RuntimeError('CSV 暂存区达到 10 万条或 128 MiB，采集已暂停；释放 CSV 后自动继续，暂停期间可能漏记短连接')
        if meta.get('error'):
            db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(now,'recovered','采集恢复；中断期间短连接可能遗漏'))
        last = meta.get('last_sample')
        source=snapshot.get('source',meta.get('source',''))
        changed=bool(source and source!=meta.get('source'))
        if changed:
            put(db,'source',source)
            db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(now,'source_changed','控制接口切换；首次快照建立基线'))
        gap = bool(last and now-last > max(5,config['interval']*2+3))
        if gap:
            put(db,'gaps',meta.get('gaps',0)+1)
            db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(now,'gap',str(round(now-last,1))+' 秒无成功采样'))
        delta_down = delta_up = unknown = 0
        programs={}
        for row in snapshot.get('rows',[]):
            # Defence in depth: only resolved proxy chains enter persistent storage.
            chains = row.get('chains',[])
            if not chains or any(x in chains for x in ('DIRECT','REJECT','REJECT-DROP')):
                continue
            cid = hashlib.sha256((source+'|'+row['id']).encode()).hexdigest() if source else row['id']
            old = db.execute('SELECT * FROM connections WHERE id=?',(cid,)).fetchone()
            down, up = max(0,int(row['down'])), max(0,int(row['up']))
            info = dict(row)
            info['source']=source
            if old:
                old_info = json.loads(old['info'])
                # Capture socket identity while it exists; retain it after the process exits.
                if old_info.get('binding') == 'socket' and old_info.get('pid'):
                    for k in ('pid','process','path','created','parent','parent_path','binding'):
                        info[k] = old_info.get(k)
                dd, du = max(0, down-old['down']), max(0, up-old['up'])
                if down < old['down'] or up < old['up']:
                    # Counter reset: establish a baseline, never manufacture a huge delta.
                    dd = du = 0
                    db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(now,'counter_reset',cid))
            else:
                try: born = datetime.fromisoformat(row.get('start','').replace('Z','+00:00')).timestamp()
                except (ValueError,TypeError): born = 0
                # Existing connections at installation are baseline-only. A newly observed
                # connection born after monitoring began can contribute its initial bytes.
                eligible = bool(last and not changed and born >= last)
                dd, du = (down,up) if eligible else (0,0)
            if changed:dd=du=0
            if info.get('binding') in ('unknown','ambiguous','core-name-only'):
                unknown += dd+du
            packed = json.dumps(info,ensure_ascii=False)
            db.execute('''INSERT INTO connections VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              down=excluded.down,up=excluded.up,last_seen=excluded.last_seen,info=excluded.info''',
              (cid,down,up,now,now,packed))
            program=activity.key(info)
            if program not in programs:programs[program]=[info,0,0]
            programs[program][1]+=dd;programs[program][2]+=du
            if dd+du:
                activity.record(db,info,dd,du,now)
                csvlog.queue_delta(db,info,dd,du,now)
                db.execute('''INSERT INTO totals VALUES(?,?,?,?,?,?,?) ON CONFLICT(day,dimension) DO UPDATE SET
                  down=totals.down+excluded.down,up=totals.up+excluded.up,last_seen=excluded.last_seen,info=excluded.info''',
                  (day,dimension(info),packed,dd,du,now,now))
                since=meta.get('view_started')
                eligible_view=bool(since and ((old and old['last_seen']>=since) or (not old and born>=since)))
                if meta.get('view_active') and eligible_view:
                    db.execute('''INSERT INTO view_totals VALUES(?,?,?,?,?,?,?) ON CONFLICT(day,dimension) DO UPDATE SET
                      down=view_totals.down+excluded.down,up=view_totals.up+excluded.up,last_seen=excluded.last_seen,info=excluded.info''',
                      (day,dimension(info),packed,dd,du,now,now))
                    for key,amount in [('view_down',dd),('view_up',du),('view_unknown',dd+du if info.get('binding')!='socket' else 0)]:
                        meta[key]=meta.get(key,0)+amount;put(db,key,meta[key])
            delta_down += dd; delta_up += du
        elapsed = max(.1,now-last) if last else 1
        activity.finish(db,programs,delta_down,delta_up,elapsed,now,gap,config,put,meta,maintenance=maintenance)
        put(db,'sample_interval',config['interval'])
        put(db,'last_sample',now); put(db,'error',None)
        put(db,'rate_valid',not gap)
        put(db,'rate_down',0 if gap else delta_down/elapsed); put(db,'rate_up',0 if gap else delta_up/elapsed)
        put(db,'active',len(snapshot.get('rows',[])))
        put(db,'unknown_bytes',meta.get('unknown_bytes',0)+unknown)
        put(db,'lifetime_down',meta.get('lifetime_down',0)+delta_down)
        put(db,'lifetime_up',meta.get('lifetime_up',0)+delta_up)
        put(db,'samples',meta.get('samples',0)+1)
        if meta.get('view_active') and not meta.get('view_started') and now>=meta.get('view_requested',0):
            put(db,'view_started',now)
        put(db,'unclassified',snapshot.get('unclassified',0)); put(db,'sample_ms',snapshot.get('duration_ms',0))
        # Every minute, bound retained detail. Lifetime counters survive pruning.
        if maintenance and now-meta.get('last_maintenance',0)>=60:
            pressure=storage.maintain(db,now,config)
            put(db,'last_maintenance',now);put(db,'storage_pressure',pressure)

def set_view(action,path=DB):
    if action not in ('start','all'):raise ValueError('未知查看模式')
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if action=='start':
            db.execute('DELETE FROM view_totals')
            for k,v in {'view_requested':time.time(),'view_started':None,'view_down':0,'view_up':0,'view_unknown':0}.items():put(db,k,v)
        put(db,'view_active',action=='start')
    return {'ok':True}

def report(path=DB, query='', day='', limit=2000):
    with connect(path) as db:
        meta = getmeta(db)
        table='view_totals' if meta.get('view_active') else 'totals'
        sql = f'SELECT dimension,sum(down) down,sum(up) up,min(first_seen) first_seen,max(last_seen) last_seen FROM {table} WHERE 1=1'
        args = []
        if day: sql += ' AND day=?'; args.append(day)
        if query:sql+=' AND instr(lower(info),lower(?))>0';args.append(query)
        sql += ' GROUP BY dimension ORDER BY sum(down)+sum(up) DESC LIMIT ?';args.append(limit+1)
        results=[]
        for r in db.execute(sql,args):
            info = json.loads(db.execute(f'SELECT info FROM {table} WHERE dimension=? ORDER BY last_seen DESC LIMIT 1',(r['dimension'],)).fetchone()[0])
            item={**info,'down':r['down'],'up':r['up'],'first_seen':r['first_seen'],'last_seen':r['last_seen']}
            results.append(item)
        truncated=len(results)>limit;results=results[:limit]
        total=(meta.get('lifetime_down',0),meta.get('lifetime_up',0))
        if meta.get('view_active'):
            total=(meta.get('view_down',0),meta.get('view_up',0));meta['unknown_bytes']=meta.get('view_unknown',0)
        events=[dict(x) for x in db.execute('SELECT ts,kind,detail FROM events ORDER BY id DESC LIMIT 12')]
        pending_count,pending_bytes=csvlog.backlog(db)
        # PID history is explicitly retained in connection records; aggregate rows show latest PID.
        active=[]
        for r in db.execute('SELECT info FROM connections WHERE last_seen>=?',(meta.get('last_sample',0)-.01,)):
            active.append(json.loads(r['info']))
        return {'product':'proxy-traffic-monitor','pipeline':dict(PIPELINE),'version':VERSION,'meta':meta,'runtime_error':RUNTIME_ERROR,'total_down':total[0],'total_up':total[1], 'rows':results,'truncated':truncated,'row_limit':limit,
                'storage':storage.usage(path),'csv':{**csvlog.usage(path),'pending_records':pending_count,'pending_bytes':pending_bytes},'settings':settings.public(settings.load()),
                'activity':activity.report(db,time.time(),meta),'diagnosis':activity.diagnose(meta.get('error') or RUNTIME_ERROR),'active_connections':active,'events':events,'now':time.time(),'database':str(path)}

def background_io(stop, path, mode):
    """One CSV writer owns the recovery journal. File I/O never holds a DB transaction."""
    global CSV_ERROR
    while not stop.is_set():
        try:
            config=settings.load()
            if mode=='csv':
                csvlog.flush(connect,path,config)
                CSV_ERROR=None
            else:
                now=time.time()
                with connect(path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    meta=getmeta(db)
                    pressure=storage.maintain(db,now,config)
                    activity.maintain(db,now,put,meta)
                    put(db,'last_maintenance',now);put(db,'storage_pressure',pressure)
                    put(db,'maintenance_error',None)
                # PASSIVE checkpoint does not wait for readers; avoid online VACUUM.
                with connect(path) as db:db.execute('PRAGMA wal_checkpoint(PASSIVE)')
        except Exception as error:
            if mode=='csv':CSV_ERROR=str(error)
            else:
                try:
                    with connect(path) as db:put(db,'maintenance_error',str(error)[:500])
                except Exception:pass
        stop.wait(5 if mode=='csv' else 60)

def worker(stop, path=DB, collector_command=None):
    global RUNTIME_ERROR
    # Keep WAL open between short-lived connections. Otherwise SQLite checkpoints
    # and removes WAL whenever the final connection closes, on every sample.
    anchor=sqlite3.connect(str(path),timeout=15)
    anchor.execute('SELECT key FROM meta LIMIT 1').fetchone()
    io_stop=threading.Event()
    services=[threading.Thread(target=lifecycle.resilient,args=(io_stop,background_io,io_stop,path,mode),name=mode,daemon=True) for mode in ('csv','maintenance')]
    for service in services:service.start()
    try:
        while not stop.is_set():
            process=None;reader_stop=threading.Event();reader_thread=None
            try:
                command=collector_command or [str(PWSH),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ASSETS/'collector.ps1'),'-SettingsPath',str(settings.FILE)]
                process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=0x08000000)
                inbox=queue.Queue(maxsize=10)
                def reader(proc,destination,finished):
                    def deliver(line):
                        while not finished.is_set():
                            try:destination.put(line,timeout=.2);return
                            except queue.Full:continue
                    try:
                        for line in proc.stdout:
                            if finished.is_set():break
                            PIPELINE['last_received']=time.time();deliver(line)
                    finally:deliver(None)
                reader_thread=threading.Thread(target=reader,args=(process,inbox,reader_stop),daemon=True);reader_thread.start()
                last_line=time.monotonic()
                while not stop.is_set():
                    config=settings.load()
                    try:line=inbox.get(timeout=.5)
                    except queue.Empty:
                        if time.monotonic()-last_line>max(15,config['interval']*3+5):raise RuntimeError('采集接口超时，正在重新连接')
                        continue
                    if line is None:raise RuntimeError('采集子进程退出，正在重新连接')
                    last_line=time.monotonic()
                    if CSV_ERROR:raise RuntimeError(CSV_ERROR)
                    snapshot=json.loads(line.decode('utf-8-sig'));started=time.monotonic()
                    # Preserve every frame, including connections that ended while
                    # queued and counter resets; never replace FIFO with latest-only.
                    ingest(snapshot,path=path,config=config,maintenance=False)
                    PIPELINE.update(queue_depth=inbox.qsize(),processing_ms=round((time.monotonic()-started)*1000),processing_lag=max(0,time.time()-snapshot['time']))
                    RUNTIME_ERROR=None
            except Exception as error:
                RUNTIME_ERROR=str(error) or '采集没有响应'
                try:ingest({'time':time.time(),'error':RUNTIME_ERROR},path=path)
                except Exception:pass
                stop.wait(2)
            finally:
                reader_stop.set()
                if process and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                if reader_thread:reader_thread.join(timeout=1)
                if process and process.stdout:process.stdout.close()
    finally:
        io_stop.set()
        for service in services:service.join(timeout=20)
        anchor.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        host=self.headers.get('Host','')
        if host not in ('127.0.0.1:'+str(PORT),'localhost:'+str(PORT)) or self.headers.get('X-Local-Request')!='1' or self.headers.get('Origin') not in (None,'http://'+host):
            self.send_error(403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=16384:raise ValueError('设置请求长度无效')
            values=json.loads(self.rfile.read(length))
            config=settings.validate(values,settings.load())
            if self.path=='/api/view':
                result=set_view(values.get('action'))
            elif self.path=='/api/settings':
                settings.save(config);result={'ok':True,'settings':settings.public(config)}
            elif self.path=='/api/test-settings':
                fd,name=tempfile.mkstemp(prefix='probe-',suffix='.json',dir=ROOT/'data');os.close(fd)
                try:
                    settings.save(config,name)
                    result=subprocess.run([str(PWSH),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ASSETS/'collector.ps1'),'-SettingsPath',name,'-Once'],capture_output=True,timeout=15,creationflags=0x08000000)
                    snap=json.loads(result.stdout.decode('utf-8-sig').strip())
                    result={'ok':not bool(snap.get('error')),'message':snap.get('error') or '连接成功','source':snap.get('source'),'proxy_connections':len(snap.get('rows',[]))}
                finally:Path(name).unlink(missing_ok=True)
            else:self.send_error(404);return
            code=200
        except (ValueError,TypeError) as e:result={'ok':False,'message':str(e)};code=400
        except Exception:result={'ok':False,'message':'本地连接测试失败或超时，请检查接口设置'};code=400
        body=json.dumps(result,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        host=self.headers.get('Host','')
        if host not in ('127.0.0.1:'+str(PORT),'localhost:'+str(PORT)):
            self.send_error(403); return
        route=urlparse(self.path); params=parse_qs(route.query,keep_blank_values=True)
        try:
            if route.path=='/':
                body=(ASSETS/'index.html').read_bytes(); mime='text/html; charset=utf-8'
            elif route.path in ('/trend-chart.js','/ui.css'):
                body=(ASSETS/route.path[1:]).read_bytes();mime='text/javascript; charset=utf-8' if route.path.endswith('.js') else 'text/css; charset=utf-8'
            elif route.path=='/api/settings':
                body=json.dumps(settings.public(settings.load()),ensure_ascii=False).encode();mime='application/json; charset=utf-8'
            elif route.path=='/api/history':
                with connect() as db:
                    # Match only requested destination/program, cap response to latest 200.
                    history=[]
                    sql="SELECT info,first_seen,last_seen FROM connections WHERE coalesce(json_extract(info,'$.host'),'')=? AND coalesce(json_extract(info,'$.ip'),'')=? AND coalesce(json_extract(info,'$.path'),'')=?"
                    arguments=[params.get(k,[''])[0] for k in ('host','ip','path')]
                    for field in ('parent','parent_path','process'):
                        if field in params:
                            sql+=" AND coalesce(json_extract(info,'$."+field+"'),'')=?";arguments.append(params[field][0])
                    for entry in db.execute(sql+' ORDER BY last_seen DESC LIMIT 200',arguments):
                        info=json.loads(entry['info'])
                        if all((info.get(k) or '')==params.get(k,[''])[0] for k in ('host','ip','path')):
                            info['raw_connection_down']=info.pop('down',0)
                            info['raw_connection_up']=info.pop('up',0)
                            history.append({**info,'first_seen':entry['first_seen'],'last_seen':entry['last_seen']})
                            if len(history)>=200: break
                body=json.dumps(history,ensure_ascii=False).encode();mime='application/json; charset=utf-8'
            elif route.path in ('/api/status','/export.csv'):
                data=report(query=params.get('q',[''])[0],day=params.get('day',[''])[0],limit=5000 if route.path=='/export.csv' else 2000)
                if route.path=='/api/status': body=json.dumps(data,ensure_ascii=False).encode(); mime='application/json; charset=utf-8'
                else:
                    stream=io.StringIO(); fields=['process','path','parent','parent_path','pid','created','host','ip','port','network','chains','binding','down','up','first_seen','last_seen']
                    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
                    for row in data['rows']:
                        safe={k:("'"+v if isinstance(v,str) and v[:1] in '=+-@' else v) for k,v in row.items()}
                        writer.writerow(safe)
                    body=('\ufeff'+stream.getvalue()).encode(); mime='text/csv; charset=utf-8'
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            if route.path=='/export.csv': self.send_header('Content-Disposition','attachment; filename="proxy-traffic.csv"')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception: self.send_error(500,'Unable to read local database')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--report',action='store_true');args=parser.parse_args()
    initialize()
    if args.report: print(json.dumps(report(),ensure_ascii=False,indent=2));return
    # Bind before spawning the collector; an occupied port prevents duplicate accounting.
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    stop=threading.Event(); thread=threading.Thread(target=worker,args=(stop,),daemon=True)
    (ROOT/'data'/'monitor.pid').write_text(str(os.getpid()))
    thread.start()
    try: server.serve_forever(poll_interval=.5)
    finally:
        stop.set();thread.join(timeout=20);server.server_close()

if __name__=='__main__': main()
