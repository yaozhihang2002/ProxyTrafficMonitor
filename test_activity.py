import copy, tempfile, time, unittest
from pathlib import Path
import activity, monitor, settings

class ActivityTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).parent/'work';scratch.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=scratch)
        self.db=Path(self.tmp.name)/'test.db';monitor.initialize(self.db)
        self.t=time.time();self.config=dict(settings.DEFAULT)
        self.row={'id':'x','process':'app.exe','path':'C:/app.exe','parent':'shell.exe','binding':'socket','chains':['node'],'down':100,'up':50}
        self.sample()
    def tearDown(self):self.tmp.cleanup()
    def sample(self,advance=1,rows=None):
        self.t+=advance;monitor.ingest({'time':self.t,'rows':copy.deepcopy([self.row] if rows is None else rows)},self.db,self.config)
    def recent(self,now=None):
        with monitor.connect(self.db) as db:return activity.report(db,now or self.t,monitor.getmeta(db))
    def test_deltas_restart_reset_direct_and_trend(self):
        self.row.update(down=300,up=100);self.sample()
        a=self.recent();self.assertEqual(sum(x['down'] for x in a['programs']),200)
        self.assertEqual(sum(x['up'] or 0 for x in a['trend']),50)
        monitor.initialize(self.db);self.sample();self.assertEqual(self.recent()['programs'][0]['down'],200)
        self.row.update(down=10,up=10);self.sample()
        direct={**self.row,'id':'direct','chains':['DIRECT'],'down':10000000};self.sample(rows=[self.row,direct])
        self.assertEqual(sum(x['down'] for x in self.recent()['programs']),200)
    def test_age_out_and_stale_rates(self):
        self.row['down']+=200;self.sample()
        self.assertGreater(self.recent()['programs'][0]['rate_down'],0)
        self.assertEqual(self.recent(self.t+9)['programs'][0]['rate_down'],0)
        self.assertEqual(sum(x['down'] for x in self.recent(self.t+320)['programs']),0)
    def test_gap_is_unknown_rate_not_false_zero_traffic(self):
        self.row['down']+=200;self.sample(advance=20)
        self.assertEqual(self.recent()['programs'][0]['rate_down'],0)
        self.assertEqual(self.recent()['programs'][0]['down'],200)
        points=self.recent()['trend'];self.assertTrue(any(p['down'] is None for p in points))
    def test_alert_default_off_no_backfill_and_dedupe(self):
        self.row['down']+=3*1024**2;self.sample();self.assertEqual(self.recent()['alerts'],[])
        self.config.update(alert_enabled=True,alert_window=10,alert_mb=1)
        self.sample();self.assertEqual(self.recent()['alerts'],[])
        # Next bucket is after the enable point, so old traffic cannot trigger.
        self.sample(advance=11);self.assertEqual(self.recent()['alerts'],[])
        self.row['down']+=2*1024**2;self.sample();self.assertEqual(len(self.recent()['alerts']),1)
        self.row['down']+=2*1024**2;self.sample();monitor.initialize(self.db);self.sample()
        self.assertEqual(len(self.recent()['alerts']),1)
        self.sample(advance=620);self.row['down']+=2*1024**2;self.sample()
        self.assertEqual(len(self.recent()['alerts']),2)
    def test_diagnostics_and_setting_validation(self):
        self.assertIn('认证',activity.diagnose('HTTP 401')['title'])
        self.assertIn('超时',activity.diagnose('HTTP 读取超时')['title'])
        self.assertIn('端口',activity.diagnose('该端口不是兼容的 Clash 接口')['title'])
        with self.assertRaises(ValueError):settings.validate({'alert_mb':0})
        with self.assertRaises(ValueError):settings.validate({'alert_enabled':'true'})

    def test_history_separates_parent_and_empty_identity(self):
        import json,threading,urllib.request,urllib.parse
        from unittest.mock import patch
        from http.server import ThreadingHTTPServer
        original=monitor.connect
        rows=[{**self.row,'id':str(i),'host':'test','ip':'1.2.3.4','parent':parent,'parent_path':parent} for i,parent in enumerate(('one','two',''))]
        self.sample(rows=rows)
        server=ThreadingHTTPServer(('127.0.0.1',0),monitor.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(monitor,'connect',lambda path=None:original(path or self.db)),patch.object(monitor,'PORT',server.server_port):
                for parent in ('one',''):
                    query=urllib.parse.urlencode({'host':'test','ip':'1.2.3.4','path':'C:/app.exe','parent':parent,'parent_path':parent,'process':'app.exe'})
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{server.server_port}/api/history?'+query) as response:
                        data=json.load(response)
                    self.assertEqual(len(data),1);self.assertEqual(data[0]['parent'],parent)
        finally:server.shutdown();server.server_close();thread.join()

    def test_notifier_only_new_fresh_events(self):
        import json, threading, notifier
        class Icon:
            def __init__(self):self.calls=[];self.event=threading.Event()
            def notify(self,message,title):self.calls.append((message,title));self.event.set()
        detail=json.dumps({'program':'fixture.exe','minutes':10,'bytes':2*1024**2})
        with monitor.connect(self.db) as db:db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(time.time(),'traffic_alert',detail))
        icon=Icon();stop=notifier.start(icon,self.db)
        try:
            with monitor.connect(self.db) as db:
                db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(time.time()-100,'traffic_alert',detail))
                db.execute('INSERT INTO events(ts,kind,detail) VALUES(?,?,?)',(time.time(),'traffic_alert',detail))
            self.assertTrue(icon.event.wait(5));self.assertEqual(len(icon.calls),1)
            icon.event.clear();self.assertFalse(icon.event.wait(3.2));self.assertEqual(len(icon.calls),1)
        finally:stop()

if __name__=='__main__':unittest.main()
