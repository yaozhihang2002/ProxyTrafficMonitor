import test_monitor as accounting
import monitor,unittest
class ViewTests(unittest.TestCase):
    setUp=accounting.AccountingTests.setUp
    tearDown=accounting.AccountingTests.tearDown
    sample=accounting.AccountingTests.sample
    total=accounting.AccountingTests.total
    def test_start_baseline_restore_restart(self):
        self.sample([self.row]);self.row['up']+=500;self.sample([self.row])
        monitor.set_view('start',self.db)
        self.assertEqual(self.total(),(0,0))
        self.row['up']+=50;self.sample([self.row])
        self.assertEqual(self.total(),(0,0))
        self.row['up']+=20;self.sample([self.row])
        self.assertEqual(self.total(),(0,20))
        monitor.initialize(self.db)
        r=monitor.report(self.db);self.assertEqual(r['rows'][0]['up'],20)
        monitor.set_view('all',self.db);self.assertEqual(self.total(),(0,570))
        monitor.set_view('start',self.db);self.assertEqual(self.total(),(0,0))
    def test_old_connection_reappears_not_backfilled(self):
        self.sample([self.row]);monitor.set_view('start',self.db);self.sample([])
        self.row['up']+=200;self.sample([self.row]);self.assertEqual(self.total(),(0,0))
        self.row['up']+=30;self.sample([self.row]);self.assertEqual(self.total(),(0,30))
