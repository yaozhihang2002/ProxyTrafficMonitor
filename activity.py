"""Bounded, durable proxy-only activity; consumes the accounting deltas once."""
import hashlib, json

def key(info):
    return hashlib.sha256(json.dumps([info.get('path') or info.get('process') or '未知程序', info.get('parent_path') or info.get('parent') or ''],ensure_ascii=False).encode()).hexdigest()

def initialize(db, now):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS activity(bucket INTEGER,program TEXT,info TEXT,down INTEGER,up INTEGER,PRIMARY KEY(bucket,program));
    CREATE TABLE IF NOT EXISTS traffic_minutes(bucket INTEGER PRIMARY KEY,down INTEGER,up INTEGER,samples INTEGER);
    CREATE TABLE IF NOT EXISTS program_rates(program TEXT PRIMARY KEY,info TEXT,down REAL,up REAL,ts REAL);
    CREATE TABLE IF NOT EXISTS alert_activity(bucket INTEGER,program TEXT,down INTEGER,up INTEGER,PRIMARY KEY(bucket,program));
    CREATE TABLE IF NOT EXISTS alert_state(program TEXT PRIMARY KEY,latched INTEGER,last_alert REAL);
    ''')
    db.execute("INSERT OR IGNORE INTO meta VALUES('activity_started',?)",(json.dumps(now),))

def record(db, info, down, up, now):
    if not down+up:return
    db.execute('''INSERT INTO activity VALUES(?,?,?,?,?) ON CONFLICT(bucket,program) DO UPDATE SET
    down=activity.down+excluded.down,up=activity.up+excluded.up,info=excluded.info''',
    (int(now//10)*10,key(info),json.dumps(info,ensure_ascii=False),down,up))

def finish(db, programs, down, up, elapsed, now, gap, config, put, meta, maintenance=True):
    db.execute('''INSERT INTO traffic_minutes VALUES(?,?,?,1) ON CONFLICT(bucket) DO UPDATE SET
    down=traffic_minutes.down+excluded.down,up=traffic_minutes.up+excluded.up,samples=samples+1''',(int(now//60)*60,down,up))
    db.execute('DELETE FROM program_rates')
    for program,(info,dd,du) in programs.items():
        db.execute('INSERT INTO program_rates VALUES(?,?,?,?,?)',(program,json.dumps(info,ensure_ascii=False),0 if gap else dd/elapsed,0 if gap else du/elapsed,now))
    enabled=config.get('alert_enabled',False)
    signature=[enabled,config.get('alert_window',10),config.get('alert_mb',100)]
    if signature!=meta.get('alert_config'):
        put(db,'alert_config',signature);put(db,'alert_started',now)
        db.execute('DELETE FROM alert_state');db.execute('DELETE FROM alert_activity')
    elif enabled:
        for program,(info,dd,du) in programs.items():
            if dd+du:db.execute('INSERT INTO alert_activity VALUES(?,?,?,?) ON CONFLICT(bucket,program) DO UPDATE SET down=alert_activity.down+excluded.down,up=alert_activity.up+excluded.up',(int(now//10)*10,program,dd,du))
        since=int((now-config['alert_window']*60)//10)*10
        threshold=config['alert_mb']*1024**2
        amounts={r['program']:dict(r) for r in db.execute('SELECT program,sum(down+up) amount,max(bucket) latest FROM alert_activity WHERE bucket>=? GROUP BY program',(since,))}
        # Clear inactive latches; each crossing is suppressed until below 80%,
        # and a program cannot alert more than once per ten minutes.
        for state in db.execute('SELECT * FROM alert_state').fetchall():
            if amounts.get(state['program'],{}).get('amount',0) < threshold*.8:
                db.execute('UPDATE alert_state SET latched=0 WHERE program=?',(state['program'],))
        for program,row in amounts.items():
            if row['amount']<threshold or row['latest']<int(now//10)*10:continue
            state=db.execute('SELECT * FROM alert_state WHERE program=?',(program,)).fetchone()
            if state and (state['latched'] or now-state['last_alert']<600):continue
            info=json.loads(db.execute('SELECT info FROM activity WHERE program=? ORDER BY bucket DESC LIMIT 1',(program,)).fetchone()[0])
            detail={'program':info.get('process') or '未知程序','parent':info.get('parent'),'path':info.get('path'),'bytes':row['amount'],'minutes':config['alert_window']}
            db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(now,'traffic_alert',json.dumps(detail,ensure_ascii=False)))
            db.execute('INSERT INTO alert_state VALUES(?,1,?) ON CONFLICT(program) DO UPDATE SET latched=1,last_alert=excluded.last_alert',(program,now))
    if maintenance:maintain(db,now,put,meta)

def maintain(db,now,put,meta):
    if now-meta.get('activity_maintenance',0)>=60:
        for table,column,cutoff,cap in [('activity','bucket',now-3600,50000),('alert_activity','bucket',now-3600,50000),('traffic_minutes','bucket',now-7*86400,10080),('alert_state','last_alert',now-86400,5000)]:
            db.execute(f'DELETE FROM {table} WHERE {column}<?',(cutoff,))
            db.execute(f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} ORDER BY {column} DESC LIMIT -1 OFFSET ?)',(cap,))
        put(db,'activity_maintenance',now)

def report(db, now, meta):
    cutoff=int((now-300)//10)*10
    grouped={}
    for row in db.execute('SELECT * FROM activity WHERE bucket>=? ORDER BY bucket',(cutoff,)):
        p=row['program'];info=json.loads(row['info'])
        x=grouped.setdefault(p,{'key':p,'down':0,'up':0,'rate_down':0,'rate_up':0})
        x.update(process=info.get('process') or '未知程序',path=info.get('path'),parent=info.get('parent'),parent_path=info.get('parent_path'),binding=info.get('binding'),last_seen=row['bucket'])
        x['down']+=row['down'];x['up']+=row['up']
    live=bool(meta.get('last_sample') and now-meta['last_sample']<max(8,meta.get('sample_interval',1)*2+5) and not meta.get('error'))
    for row in db.execute('SELECT * FROM program_rates'):
        p=row['program'];info=json.loads(row['info'])
        x=grouped.setdefault(p,{'key':p,'down':0,'up':0,'process':info.get('process') or '未知程序','path':info.get('path'),'parent':info.get('parent'),'binding':info.get('binding')})
        x.update(rate_down=row['down'] if live else 0,rate_up=row['up'] if live else 0)
    programs=sorted(grouped.values(),key=lambda x:x['down']+x['up'],reverse=True)[:2000]
    points={r['bucket']:dict(r) for r in db.execute('SELECT * FROM traffic_minutes WHERE bucket>=?',(int(now//60)*60-59*60,))}
    trend=[points.get(t,{'bucket':t,'down':None,'up':None,'samples':0}) for t in range(int(now//60)*60-59*60,int(now//60)*60+1,60)]
    alerts=[dict(r) for r in db.execute("SELECT id,ts,detail FROM events WHERE kind='traffic_alert' ORDER BY id DESC LIMIT 20")]
    return {'started':meta.get('activity_started'),'programs':programs,'trend':trend,'alerts':alerts,'bucket_seconds':10,'window_seconds':300,'retention_minutes':60,'global_trend_days':7,'rate_valid':live and meta.get('rate_valid',True)}

def diagnose(error):
    if not error:return None
    e=str(error).lower()
    if '401' in e or '403' in e:return {'title':'接口认证失败','help':'检查 Clash 控制接口 Secret；留空不会清除已保存的 Secret。'}
    if 'timeout' in e or '超时' in e or '没有响应' in e or 'canceled' in e or '取消' in e:return {'title':'控制接口请求超时','help':'确认代理核心正在运行；检查控制端口与连接方式，稍后自动重试。'}
    if '多个' in e:return {'title':'发现多个代理核心','help':'在连接设置中指定要监测的管道名或本机 HTTP 控制接口。'}
    if '该端口' in e or '404' in e or 'http 响应' in e:return {'title':'接口或端口不兼容','help':'填写 external-controller 控制端口，而不是 HTTP/SOCKS 代理端口。'}
    if 'refused' in e or '拒绝' in e or '发送请求' in e or 'connection' in e:return {'title':'无法连接代理控制接口','help':'确认 Clash 已启动且控制接口已启用；核对设置中的地址和端口。'}
    if 'csv' in e:return {'title':'CSV 暂存空间不足','help':'关闭占用 CSV 的软件并检查目录写入权限；释放后自动继续。'}
    return {'title':'采集暂时中断','help':'查看下方原始错误，并在连接设置中测试连接；后台自动重试。'}
