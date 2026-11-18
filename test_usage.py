import unittest
from datetime import datetime
import monitor, usage_stats, test_monitor, settings, storage


class UsageTests(unittest.TestCase):
    setUp=test_monitor.AccountingTests.setUp
    tearDown=test_monitor.AccountingTests.tearDown
    sample=test_monitor.AccountingTests.sample

    def test_delta_and_view_independence(self):
        self.sample([self.row])
        monitor.set_view('start',self.db)
        self.row['down']+=123;self.sample([self.row])
        monitor.initialize(self.db)
        self.sample([self.row])
        with monitor.connect(self.db) as db:
            result=usage_stats.report(db,now=self.t)
        self.assertEqual(result['today']['down'],123)
        self.assertEqual(result['today']['samples'],3)

    def test_one_time_migration_survives_detail_pruning(self):
        with monitor.connect(self.db) as db:
            db.execute("DELETE FROM meta WHERE key='usage_migrated'")
            db.execute("INSERT INTO totals VALUES('2024-02-29','x','{}',50,12,1,2)")
            usage_stats.initialize(db);usage_stats.initialize(db)
            storage.maintain(db,self.t,settings.load(),total_limit=0)
            result=usage_stats.report(db,'2024-02')
        self.assertEqual(len(result['days']),29)
        self.assertEqual(result['months'][1]['down'],50)
        self.assertEqual(result['days'][-1]['up'],12)
        self.assertTrue(result['days'][-1]['imported'])
        self.assertFalse(result['days'][0]['recorded'])

    def test_midnight_month_boundary_and_zero_vs_missing(self):
        a=datetime(2026,10,31,23,59,59).timestamp();b=a+2
        with monitor.connect(self.db) as db:
            usage_stats.record(db,a,100,20,a-1,False)
            usage_stats.record(db,b,200,40,a,False)
            usage_stats.record(db,b+86400,0,0,b,True)
            oct=usage_stats.report(db,'2026-10',now=b)
            nov=usage_stats.report(db,'2026-11',now=b)
        self.assertEqual(oct['days'][-1]['down'],100)
        self.assertEqual(nov['today']['down'],200)
        self.assertEqual(nov['this_month']['up'],40)
        self.assertEqual(nov['days'][0]['seconds'],1)
        self.assertTrue(nov['days'][1]['recorded'])
        self.assertFalse(nov['days'][2]['recorded'])

    def test_program_breakdown_uses_calendar_not_observation_view(self):
        self.sample([self.row]);self.row['up']+=20;self.sample([self.row])
        monitor.set_view('start',self.db)
        with monitor.connect(self.db) as db:
            period=datetime.fromtimestamp(self.t).strftime('%Y-%m')
            self.assertEqual(usage_stats.programs(db,period)[0]['up'],20)
            self.assertEqual(usage_stats.programs(db,'bad'),[])
            self.assertEqual(usage_stats.programs(db,'2000-01'),[])

    def test_program_ranking_follows_retained_totals_after_upgrade(self):
        with monitor.connect(self.db) as db:
            db.execute('DROP INDEX ix_totals_day_program_bytes')
            db.executemany('INSERT INTO totals VALUES(?,?,?,?,?,?,?)', [
                ('2024-02-28','a','{"process":"browser.exe"}',10,20,0,0),
                ('2024-02-29','b','{"process":"browser.exe"}',40,5,0,0),
                ('2024-02-29','c','{}',50,35,0,0),
                ('2024-03-01','d','{"process":"browser.exe"}',100,200,0,0),
            ])
            usage_stats.initialize(db)
            self.assertEqual(usage_stats.programs(db,'2024-02'), [
                {'process':'未知程序','down':50,'up':35},
                {'process':'browser.exe','down':50,'up':25},
            ])
            self.assertEqual(usage_stats.programs(db,'2024-02-29')[1]['up'],5)
            db.execute("DELETE FROM totals WHERE day='2024-02-28'")
            self.assertEqual(usage_stats.programs(db,'2024-02')[1]['down'],40)


if __name__=='__main__':unittest.main()
