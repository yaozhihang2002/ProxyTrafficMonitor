import tempfile,threading,unittest,subprocess,sys,time,json
from pathlib import Path
from unittest.mock import Mock
import lifecycle

class RecoveryTests(unittest.TestCase):
    def test_abnormal_exit_restarts_but_normal_exit_stays_stopped(self):
        processes=[Mock(),Mock(),Mock()]
        for p,c in zip(processes,[70,-9,0]):p.wait.return_value=c
        factory=Mock(side_effect=processes);sleep=Mock()
        lifecycle.supervise(['example'],popen=factory,clock=lambda:0,sleep=sleep)
        self.assertEqual(factory.call_count,3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[2,4])

    def test_crash_loop_has_bounded_backoff(self):
        processes=[Mock() for _ in range(10)]
        for p in processes:p.wait.return_value=70
        processes[-1].wait.return_value=0
        sleep=Mock();lifecycle.supervise(['example'],popen=Mock(side_effect=processes),clock=lambda:0,sleep=sleep)
        self.assertEqual(max(c.args[0] for c in sleep.call_args_list),60)

    def test_dead_thread_recovers_and_stop_is_respected(self):
        stop=threading.Event();calls=[]
        def target():
            calls.append(1)
            if len(calls)==1:raise RuntimeError('Injected failure')
            stop.set()
        lifecycle.resilient(stop,target,retry=.01)
        self.assertEqual(len(calls),2)

    def test_real_child_crash_then_exit_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'state.json'
            marker.write_text(json.dumps({'history':123,'starts':0}))
            code="import json,pathlib,sys; p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()); d['starts']+=1; p.write_text(json.dumps(d)); sys.exit(70 if d['starts']==1 else 0)"
            lifecycle.supervise([sys.executable,'-c',code,str(marker)],sleep=lambda _:None)
            self.assertEqual(json.loads(marker.read_text()),{'history':123,'starts':2})

if __name__=='__main__':unittest.main()
