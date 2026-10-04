import copy,csv,ctypes,json,os,subprocess,tempfile,threading,time,unittest
from unittest.mock import patch
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from datetime import datetime,timezone
import monitor,settings,csvlog,storage

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parent/'work'
        self.tmp=tempfile.TemporaryDirectory(dir=scratch);self.db=Path(self.tmp.name)/'traffic.sqlite3'
        monitor.initialize(self.db);self.t=time.time();self.cfg=dict(settings.DEFAULT)
        self.row=dict(id='conn',start=datetime.fromtimestamp(self.t,timezone.utc).isoformat(),host='example.test',ip='1.2.3.4',port='443',network='tcp',pid=12,process='demo.exe',path='C:/demo.exe',parent='shell.exe',parent_path='C:/shell.exe',binding='socket',chains=['node'],down=0,up=0)
        self.sample()
    def tearDown(self):self.tmp.cleanup()
    def sample(self):
        self.t+=1;monitor.ingest({'time':self.t,'rows':[copy.deepcopy(self.row)]},self.db,config=self.cfg)
    def pending(self):
        with monitor.connect(self.db) as db:return csvlog.backlog(db)[0]
    def csvrows(self):
        rows=[]
        for f in (self.db.parent/'csv').glob('*.csv'):
            with f.open(encoding='utf-8-sig',newline='') as stream:rows.extend(csv.DictReader(stream))
        return rows
    def test_settings_validation_and_secret_protection(self):
        for url in ('https://example.com:443','http://127.0.0.1:9090/subscription','http://user:pass@localhost:9090'):
            with self.assertRaises(ValueError):settings.validate({'endpoint':url})
        c=settings.validate({'mode':'http','secret':'fixture-secret'})
        self.assertNotIn('fixture-secret',json.dumps(c));self.assertTrue(settings.public(c)['has_secret'])
        self.assertNotIn('secret_dpapi',settings.public(c))
        self.assertEqual(settings.validate({'secret':''},c)['secret_dpapi'],c['secret_dpapi'])
        self.assertFalse(settings.validate({'clear_secret':True},c)['secret_dpapi'])
    def test_delta_csv_no_repeat_or_direct(self):
        self.row.update(down=40,up=60);self.sample()
        csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+20,force=True)
        csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+21,force=True)
        rows=self.csvrows();self.assertEqual(sum(int(x['upload_bytes']) for x in rows),60)
        self.assertEqual(sum(int(x['download_bytes']) for x in rows),40);self.assertEqual(self.pending(),0)
        self.row.update(chains=['DIRECT'],up=10000);self.sample();self.assertEqual(self.pending(),0)
    def test_real_windows_file_lock_and_auto_replay(self):
        self.row.update(up=20);self.sample();csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+20,force=True)
        file=next((self.db.parent/'csv').glob('*.csv'))
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
        kernel.CreateFileW.restype=ctypes.c_void_p;kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.CreateFileW(str(file),0x80000000,1,None,3,0,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        try:
            self.row.update(up=50);self.sample()
            self.assertFalse(csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+21,force=True))
            self.row.update(up=90);self.sample();self.assertGreater(self.pending(),0)
            self.assertEqual(monitor.report(self.db)['total_up'],90)
        finally:kernel.CloseHandle(handle)
        self.assertTrue(csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+25,force=True))
        self.assertEqual(sum(int(x['upload_bytes']) for x in self.csvrows()),90)
        self.assertEqual(self.pending(),0)
        csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+26,force=True)
        self.assertEqual(sum(int(x['upload_bytes']) for x in self.csvrows()),90)
    def test_incomplete_append_crash_recovery(self):
        self.row.update(up=10);self.sample();csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+20,force=True)
        file=next((self.db.parent/'csv').glob('*.csv'));offset=file.stat().st_size
        self.t+=10;self.row.update(up=35);self.sample()
        with monitor.connect(self.db) as db:monitor.put(db,'csv_append',{'name':file.name,'offset':offset})
        with file.open('ab') as f:f.write(b'incomplete-append')
        csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+20,force=True)
        self.assertNotIn('incomplete-append',file.read_text(encoding='utf-8-sig'))
        self.assertEqual(sum(int(x['upload_bytes']) for x in self.csvrows()),35)
    def test_prune_keeps_lifetime_and_pending(self):
        for i in range(6):
            self.row.update(host=f'host{i}.test',up=(i+1)*10);self.sample()
        with monitor.connect(self.db) as db:
            storage.maintain(db,self.t,self.cfg,connection_limit=1,total_limit=2)
            self.assertEqual(db.execute('SELECT count(*) FROM totals').fetchone()[0],2)
        self.assertEqual(monitor.report(self.db)['total_up'],60);self.assertGreater(self.pending(),0)
    def test_source_change_baselines(self):
        for source,up in [('http:a',1000),('http:a',1100),('http:b',5000),('http:b',5020),('http:a',2000)]:
            self.t+=1;self.row['up']=up
            monitor.ingest({'time':self.t,'source':source,'rows':[copy.deepcopy(self.row)]},self.db,config=self.cfg)
        self.assertEqual(monitor.report(self.db)['total_up'],120)
    def test_csv_rotation_and_budget(self):
        with patch.object(csvlog,'CHUNK_BYTES',1):
            for amount in (10,30):
                self.row['up']=amount;self.sample()
                csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+20,force=True)
            self.assertEqual(len(list((self.db.parent/'csv').glob('*.csv'))),2)
            self.cfg['csv_budget_mb']=0.0001
            self.row['up']=60;self.sample()
            csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+20,force=True)
        self.assertEqual(len(list((self.db.parent/'csv').glob('*.csv'))),1)
        self.assertEqual(monitor.report(self.db)['total_up'],60)
    def test_backlog_limit_pauses_and_resumes(self):
        self.row['up']=10;self.sample()
        with patch.object(csvlog,'PENDING_LIMIT',1):
            self.row['up']=30
            with self.assertRaises(RuntimeError):self.sample()
            self.assertEqual(monitor.report(self.db)['total_up'],10)
            csvlog.flush(monitor.connect,self.db,self.cfg,now=self.t+20,force=True)
            self.sample()
        self.assertEqual(monitor.report(self.db)['total_up'],30)
    def test_classic_http_controller_auth_and_schema(self):
        class Fixture(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.headers.get('Authorization')!='Bearer fixture-secret':self.send_error(401);return
                body=json.dumps({'connections':[],'downloadTotal':10,'uploadTotal':20}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        config=settings.validate({'mode':'http','endpoint':f'http://127.0.0.1:{server.server_port}','secret':'fixture-secret'})
        file=self.db.parent/'settings.json';settings.save(config,file)
        try:
            def run():
                p=subprocess.run([str(monitor.PWSH),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(monitor.ROOT/'collector.ps1'),'-SettingsPath',str(file),'-Once'],capture_output=True,timeout=15,creationflags=0x08000000)
                return json.loads(p.stdout.decode('utf-8-sig'))
            response=run();self.assertNotIn('error',response);self.assertEqual(response['rows'],[])
            config['secret_dpapi']='';settings.save(config,file);self.assertIn('401',run()['error'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
