import http.server
import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ['AICC_DATA_DIR'] = tempfile.mkdtemp()
os.environ.pop('AICC_SCHEDULER', None)
import rss  # noqa: E402
import scheduler  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PUBLIC = '93.184.216.34'
FEED = b'<rss><channel><item><title>A</title><link>https://example.com/a</link></item></channel></rss>'


def resolver_for(mapping):
    def r(host, port, type=0):
        ips = mapping.get(host, [host])
        return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port)) for ip in ips]
    return r


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        status, headers, body = self.routes.get(self.path, (404, {}, b''))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class FetchSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.connected = []
        real = rss._open_socket

        def fake_open(ip, port, timeout):  # ghi lại IP đã ghim, nhưng nối tới server thử nghiệm cục bộ
            self.connected.append(ip)
            return real('127.0.0.1', self.port, timeout)
        p1 = patch.object(rss, '_open_socket', fake_open)
        p2 = patch.object(rss, 'ALLOWED_PORTS', {80, 443})
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.res = resolver_for({'feed.test': [PUBLIC], 'evil.test': ['10.0.0.5'], 'mixed.test': [PUBLIC, '127.0.0.1']})

    def test_fetch_ok_and_pins_ip(self):
        _Handler.routes = {'/f': (200, {}, FEED)}
        self.assertEqual(rss.fetch_feed('http://feed.test/f', self.res), FEED)
        self.assertEqual(self.connected, [PUBLIC])

    def test_blocks_bad_initial_urls(self):
        for u in ('http://127.0.0.1/f', 'http://[::1]/f', 'http://169.254.169.254/latest', 'http://10.1.2.3/', 'http://100.64.0.1/',
                  'http://[::ffff:127.0.0.1]/', 'http://evil.test/', 'http://mixed.test/', 'http://user:pw@feed.test/', 'ftp://feed.test/',
                  'http://feed.test:8080/'):
            with self.assertRaises(rss.FetchError, msg=u):
                rss.fetch_feed(u, self.res)
        self.assertEqual(self.connected, [])

    def test_redirect_to_metadata_ip_blocked(self):
        _Handler.routes = {'/r': (302, {'Location': 'http://169.254.169.254/latest/meta-data'}, b'')}
        with self.assertRaisesRegex(rss.FetchError, 'nội bộ'):
            rss.fetch_feed('http://feed.test/r', self.res)
        self.assertEqual(self.connected, [PUBLIC])  # không kết nối tới hop thứ hai

    def test_redirect_to_private_hostname_blocked(self):
        _Handler.routes = {'/r': (301, {'Location': 'http://evil.test/x'}, b'')}
        with self.assertRaises(rss.FetchError):
            rss.fetch_feed('http://feed.test/r', self.res)

    def test_relative_redirect_followed_and_loop_limited(self):
        _Handler.routes = {'/a': (302, {'Location': '/f'}, b''), '/f': (200, {}, FEED), '/loop': (302, {'Location': '/loop'}, b'')}
        self.assertEqual(rss.fetch_feed('http://feed.test/a', self.res), FEED)
        with self.assertRaisesRegex(rss.FetchError, 'redirect'):
            rss.fetch_feed('http://feed.test/loop', self.res)

    def test_dns_rebinding_uses_validated_ip(self):
        answers = iter([[PUBLIC], ['127.0.0.1']])  # lần phân giải thứ hai sẽ là loopback
        calls = []

        def rebinding(host, port, type=0):
            calls.append(host)
            return resolver_for({host: next(answers)})(host, port)
        _Handler.routes = {'/f': (200, {}, FEED)}
        rss.fetch_feed('http://feed.test/f', rebinding)
        self.assertEqual((len(calls), self.connected), (1, [PUBLIC]))  # chỉ phân giải 1 lần/hop, kết nối đúng IP đã kiểm tra

    def test_size_limit_and_http_error(self):
        _Handler.routes = {'/big': (200, {}, b'x' * 5000), '/e': (500, {}, b'')}
        with patch.object(rss, 'MAX_BYTES', 1000):
            with self.assertRaisesRegex(rss.FetchError, 'dung lượng'):
                rss.fetch_feed('http://feed.test/big', self.res)
        with self.assertRaisesRegex(rss.FetchError, 'HTTP 500'):
            rss.fetch_feed('http://feed.test/e', self.res)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        with server.db() as c:
            for t in ('news', 'sources', 'collect_runs', 'locks'):
                c.execute(f'DELETE FROM {t}')
            c.execute("INSERT INTO sources(id,name,url,created_at) VALUES('s1','Ok','https://ok.test/f','x')")
            c.execute("INSERT INTO sources(id,name,url,created_at) VALUES('s2','Bad','https://bad.test/f','x')")

    tearDown = setUp

    @staticmethod
    def fetcher(url):
        if 'bad.test' in url:
            raise rss.FetchError('HTTP 500')
        return FEED

    def test_scheduler_not_started_by_default(self):
        with TestClient(server.app) as client:
            st = client.get('/api/scheduler').json()
        self.assertFalse(st['enabled'])
        self.assertFalse(st['running'])
        self.assertIsNone(server.scheduler_obj)

    def test_run_records_partial_with_source_error(self):
        out = scheduler.run_collection(server.db, 'scheduled', self.fetcher)
        self.assertEqual((out['status'], out['added'], len(out['errors'])), ('partial', 1, 1))
        run = scheduler.recent_runs(server.db)[0]
        self.assertEqual((run['trigger'], run['status'], run['added'], run['error_count'], run['sources_total']), ('scheduled', 'partial', 1, 1, 2))
        self.assertIn('HTTP 500', run['errors'][0])
        self.assertTrue(run['finished_at'])

    def test_no_overlapping_runs(self):
        owner = 'other-worker'
        self.assertTrue(scheduler.acquire_lease(server.db, 'collect', owner))
        self.assertFalse(scheduler.acquire_lease(server.db, 'collect', 'x'))
        self.assertIsNone(scheduler.run_collection(server.db, 'manual', self.fetcher))
        self.assertEqual(TestClient(server.app).post('/api/news/collect').status_code, 409)
        scheduler.release_lease(server.db, 'collect', owner)
        self.assertIsNotNone(scheduler.run_collection(server.db, 'manual', self.fetcher))

    def test_expired_lease_can_be_taken(self):
        with server.db() as c:
            c.execute("INSERT INTO locks VALUES('collect','dead','2000-01-01T00:00:00+00:00')")
        self.assertTrue(scheduler.acquire_lease(server.db, 'collect', 'new'))

    def test_tick_runs_only_when_due(self):
        s = scheduler.Scheduler(server.db, 60, fetcher=self.fetcher)
        self.assertTrue(s.is_due())
        self.assertIsNotNone(s.tick())
        self.assertFalse(s.is_due())
        self.assertIsNone(s.tick())  # vừa quét xong -> không quét lại
        self.assertEqual(len(scheduler.recent_runs(server.db)), 1)
        with server.db() as c:
            c.execute("UPDATE collect_runs SET started_at='2000-01-01T00:00:00+00:00'")
        self.assertTrue(s.is_due())

    def test_scheduler_thread_start_stop(self):
        s = scheduler.Scheduler(server.db, 60, tick_seconds=0.05, fetcher=self.fetcher)
        s.start()
        self.assertTrue(s.running)
        s.stop()
        self.assertFalse(s.running)
        self.assertEqual(len(scheduler.recent_runs(server.db)), 1)

    def test_manual_collect_endpoint_records_run(self):
        with patch.object(rss, 'fetch_feed', self.fetcher):
            r = TestClient(server.app).post('/api/news/collect').json()
        self.assertEqual((r['status'], r['added']), ('partial', 1))
        self.assertEqual(scheduler.recent_runs(server.db)[0]['trigger'], 'manual')


if __name__ == '__main__':
    unittest.main()
