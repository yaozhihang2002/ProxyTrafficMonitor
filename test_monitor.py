import copy, tempfile, time, unittest
from pathlib import Path
from datetime import datetime, timezone
import monitor

class AccountingTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parent/'work';scratch.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=scratch);self.db=Path(self.tmp.name)/'test.db'
        monitor.initialize(self.db);self.t=time.time()+2
        self.row=dict(id='a',start=datetime.fromtimestamp(self.t,timezone.utc).isoformat(),
          host='example.test',ip='1.2.3.4',port='443',network='tcp',pid=123,process='app.exe',
          path='C:/app.exe',parent='launcher.exe',parent_path='C:/launcher.exe',binding='socket',
          chains=['node-A','group'],down=100,up=200)
    def tearDown(self):self.tmp.cleanup()
    def sample(self,rows):
        self.t+=1;monitor.ingest({'time':self.t,'rows':copy.deepcopy(rows)},self.db)
    def total(self):
        r=monitor.report(self.db);return r['total_down'],r['total_up']
    def test_initial_baseline_and_delta(self):
        self.sample([self.row]);self.assertEqual(self.total(),(0,0))
        self.row.update(down=130,up=240);self.sample([self.row]);self.assertEqual(self.total(),(30,40))
    def test_restart_and_reappearance_do_not_double_count(self):
        self.sample([self.row]);self.row.update(down=120,up=220);self.sample([self.row]);self.sample([])
        monitor.initialize(self.db);self.sample([self.row]);self.assertEqual(self.total(),(20,20))
    def test_new_connection_and_direct_reject_exclusion(self):
        self.sample([])
        self.row['start']=datetime.fromtimestamp(self.t+.5,timezone.utc).isoformat()
        rows=[copy.deepcopy(self.row) for _ in range(4)]
        for i,r in enumerate(rows):r['id']=str(i)
        rows[1]['chains']=['DIRECT'];rows[2]['chains']=['REJECT'];rows[3]['chains']=[]
        self.sample(rows);self.assertEqual(self.total(),(100,200))
        with monitor.connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM connections').fetchone()[0],1)
    def test_reset_baseline_and_process_identity_retained(self):
        self.sample([self.row]);self.row.update(down=10,up=20,pid=0,process='',binding='unknown')
        self.sample([self.row]);self.assertEqual(self.total(),(0,0))
        self.row.update(down=20,up=40);self.sample([self.row])
        r=monitor.report(self.db);self.assertEqual(r['rows'][0]['pid'],123);self.assertEqual(self.total(),(10,20))
    def test_old_unknown_connection_not_backfilled(self):
        self.sample([]);self.row['start']='2020-01-01T00:00:00+00:00';self.sample([self.row]);self.assertEqual(self.total(),(0,0))
    def test_reused_socket_does_not_reassign_existing_connection(self):
        self.sample([self.row]);self.row.update(down=200,pid=999,process='different.exe')
        self.sample([self.row]);self.assertEqual(monitor.report(self.db)['rows'][0]['pid'],123)
    def test_gap_and_error_are_visible(self):
        self.sample([]);monitor.ingest({'time':self.t+1,'error':'unavailable'},self.db)
        self.t+=20;self.sample([]);r=monitor.report(self.db)
        self.assertEqual(r['meta']['gaps'],1);self.assertIsNone(r['meta']['error']);self.assertEqual(r['meta']['errors'],1)
if __name__=='__main__':unittest.main()
