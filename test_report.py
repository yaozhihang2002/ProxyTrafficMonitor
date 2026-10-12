import unittest,json,contextlib,threading,urllib.request,urllib.error
from unittest.mock import patch
import monitor,test_monitor

class ReportTests(unittest.TestCase):
    setUp=test_monitor.AccountingTests.setUp
    tearDown=test_monitor.AccountingTests.tearDown
    sample=test_monitor.AccountingTests.sample
    def test_light_status_keeps_totals_and_skips_historical_queries(self):
        self.sample([self.row]);self.row['up']+=25;self.sample([self.row])
        queries=[];original=monitor.connect
        @contextlib.contextmanager
        def traced(path=monitor.DB):
            with original(path) as db:
                db.set_trace_callback(queries.append);yield db
        with patch.object(monitor,'connect',traced):result=monitor.report(self.db,details=False)
        self.assertEqual(result['rows'],[])
        self.assertEqual(result['total_up'],25)
        self.assertTrue(result['activity']['programs'])
        self.assertFalse(any('from totals' in q.lower() or 'from view_totals' in q.lower() for q in queries))

    def test_aggregation_returns_latest_identity_without_extra_row_selects(self):
        with monitor.connect(self.db) as db:
            db.execute('INSERT INTO totals VALUES(?,?,?,?,?,?,?)',('2026-10-01','x',json.dumps({'pid':1}),10,20,1,2))
            db.execute('INSERT INTO totals VALUES(?,?,?,?,?,?,?)',('2026-10-02','x',json.dumps({'pid':2}),30,40,3,4))
        result=monitor.report(self.db)
        self.assertEqual((result['rows'][0]['down'],result['rows'][0]['up'],result['rows'][0]['pid']),(40,60,2))
        self.assertEqual((result['rows'][0]['first_seen'],result['rows'][0]['last_seen']),(1,4))
        filtered=monitor.report(self.db,day='2026-10-01')
        self.assertEqual(filtered['rows'][0]['up'],20)
        with monitor.connect(self.db) as db:
            plan=db.execute('EXPLAIN QUERY PLAN SELECT info FROM totals WHERE dimension=? ORDER BY last_seen DESC LIMIT 1',('x',)).fetchall()
            self.assertTrue(any('ix_totals_dimension_seen' in str(tuple(r)) for r in plan))

    def test_http_light_request_and_explicit_service_error(self):
        server=monitor.ThreadingHTTPServer(('127.0.0.1',0),monitor.Handler)
        port=server.server_address[1];thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        original=monitor.report
        try:
            with patch.object(monitor,'PORT',port),patch.object(monitor,'report',side_effect=lambda **kw:original(self.db,**kw)) as report:
                with opener.open(f'http://127.0.0.1:{port}/api/status?details=0') as response:
                    self.assertEqual(json.load(response)['rows'],[])
                self.assertFalse(report.call_args.kwargs['details'])
            with patch.object(monitor,'PORT',port),patch.object(monitor,'report',side_effect=RuntimeError('Injected failure')):
                with self.assertRaises(urllib.error.HTTPError) as error:opener.open(f'http://127.0.0.1:{port}/api/status')
                self.assertEqual(error.exception.code,503)
                self.assertFalse(json.load(error.exception)['ok'])
                error.exception.close()
        finally:server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main()
