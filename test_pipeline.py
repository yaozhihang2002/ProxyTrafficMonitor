import json, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
import monitor, settings

class PipelineTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).parent/'work';root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.folder=Path(self.temp.name);self.db=self.folder/'traffic.sqlite3'
        monitor.initialize(self.db);monitor.CSV_ERROR=None;monitor.RUNTIME_ERROR=None
        self.stop=threading.Event();self.release=threading.Event();self.thread=None
    def tearDown(self):
        self.stop.set();self.release.set()
        if self.thread:self.thread.join(10)
        self.assertFalse(self.thread and self.thread.is_alive())
        self.temp.cleanup()
    def launch(self,code):
        script=self.folder/'fake_collector.py';script.write_text(code,encoding='utf-8')
        self.thread=threading.Thread(target=monitor.worker,args=(self.stop,self.db,[sys.executable,'-u',str(script)]))
        self.thread.start()
    def test_slow_csv_does_not_stall_accounting_or_lose_transient_connection(self):
        writing=threading.Event()
        def slow_flush(*args,**kwargs):
            writing.set();self.release.wait(30);return True
        code='''import json,time
from datetime import datetime,timezone
start=time.time()
def born(value):return datetime.fromtimestamp(value,timezone.utc).isoformat()
for i in range(16):
    rows=[dict(id='main',process='app.exe',chains=['proxy'],down=i*10,up=i*2,start=born(start))]
    if i==4:short_start=time.time()
    if 4<=i<=8:rows.append(dict(id='short',process='short.exe',chains=['proxy'],down=(i-3)*7,up=0,start=born(short_start)))
    print(json.dumps(dict(time=time.time(),rows=rows)),flush=True)
    time.sleep(.3)
time.sleep(20)
'''
        with patch.object(monitor.csvlog,'flush',side_effect=slow_flush),patch.object(monitor.settings,'load',return_value=dict(settings.DEFAULT)):
            self.launch(code);self.assertTrue(writing.wait(3))
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                data=monitor.report(self.db)
                if data['meta'].get('samples',0)>=16:break
                time.sleep(.15)
            self.assertEqual(data['meta']['samples'],16,monitor.PIPELINE)
            self.assertEqual((data['total_down'],data['total_up']),(185,30))
            self.assertLess(data['now']-data['meta']['last_sample'],8)
            self.assertLess(monitor.PIPELINE['processing_lag'],8)
            self.assertTrue(Path(str(self.db)+'-wal').exists())
            # CSV output is delayed, but its exact deltas remain durable.
            with monitor.connect(self.db) as db:
                self.assertEqual(tuple(db.execute('SELECT sum(down),sum(up) FROM csv_pending').fetchone()),(185,30))
            self.stop.set();self.release.set();self.thread.join(5)
    def test_stop_does_not_wait_for_fifteen_second_queue_timeout(self):
        with patch.object(monitor.settings,'load',return_value=dict(settings.DEFAULT)):
            self.launch('import time;time.sleep(30)')
            time.sleep(.2);started=time.monotonic();self.stop.set();self.thread.join(3)
            self.assertFalse(self.thread.is_alive());self.assertLess(time.monotonic()-started,3)

if __name__=='__main__':unittest.main()
