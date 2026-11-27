import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['AICC_DATA_DIR'] = tempfile.mkdtemp()
os.environ['AICC_ADMIN_PASSWORD'] = 'test-password-12345'
import rss  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

RSS_XML = b'''<rss><channel>
<item><title>A</title><link>https://www.Example.com/post/1/?utm_source=x&amp;id=2#top</link><pubDate>Mon, 02 Jan 2023 10:00:00 GMT</pubDate></item>
<item><title>A dup</title><link>http://example.com/post/1?id=2</link></item>
<item><title>B</title><link>https://example.com/post/2</link></item>
<item><title></title><link>https://example.com/empty</link></item>
<item><title>Bad</title><link>javascript:alert(1)</link></item>
</channel></rss>'''


class NormalizeTests(unittest.TestCase):
    def test_equivalent_urls_match(self):
        base = rss.normalize_url('https://example.com/post/1?id=2')
        for u in ('http://www.EXAMPLE.com/post/1/?id=2&utm_campaign=z#frag', 'https://example.com:443/post//1?fbclid=abc&id=2'):
            self.assertEqual(rss.normalize_url(u), base)

    def test_different_urls_differ(self):
        self.assertNotEqual(rss.normalize_url('https://example.com/a?id=1'), rss.normalize_url('https://example.com/a?id=2'))
        self.assertNotEqual(rss.normalize_url('https://example.com:8080/a'), rss.normalize_url('https://example.com/a'))

    def test_query_order_ignored(self):
        self.assertEqual(rss.normalize_url('https://e.com/a?b=1&a=2'), rss.normalize_url('https://e.com/a?a=2&b=1'))

    def test_invalid(self):
        for u in ('', 'javascript:alert(1)', 'ftp://e.com/x', 'not a url', 'https://'):
            self.assertIsNone(rss.normalize_url(u))

    def test_blocks_private_hosts(self):
        for u in ('http://127.0.0.1/feed', 'http://169.254.169.254/', 'ftp://example.com'):
            with self.assertRaises(ValueError):
                rss.check_feed_url(u)


class DedupeTests(unittest.TestCase):
    def setUp(self):
        with server.db() as c:
            c.execute('DELETE FROM news')
            c.execute('DELETE FROM sources')
            c.execute("INSERT INTO sources(id,name,url,created_at) VALUES('s1','T','https://example.com/feed','x')")
        self.src = {'id': 's1', 'name': 'T', 'url': 'https://example.com/feed'}

    def tearDown(self):
        with server.db() as c:
            c.execute('DELETE FROM news')
            c.execute('DELETE FROM sources')

    def count(self):
        with server.db() as c:
            return c.execute('SELECT COUNT(*) FROM news').fetchone()[0]

    def test_parse_and_dedupe_within_feed(self):
        res = rss.collect_source(server.db, self.src, fetcher=lambda u: RSS_XML)
        self.assertTrue(res['ok'])
        self.assertEqual((res['added'], self.count()), (2, 2))

    def test_second_run_adds_nothing(self):
        rss.collect_source(server.db, self.src, fetcher=lambda u: RSS_XML)
        res = rss.collect_source(server.db, self.src, fetcher=lambda u: RSS_XML)
        self.assertEqual((res['added'], self.count()), (0, 2))

    def test_failure_is_recorded(self):
        def boom(u): raise OSError('down')
        res = rss.collect_source(server.db, self.src, fetcher=boom)
        self.assertFalse(res['ok'])
        with server.db() as c:
            self.assertEqual(c.execute("SELECT last_status FROM sources WHERE id='s1'").fetchone()[0], 'error')

    def test_api_collect_and_list(self):
        client = TestClient(server.app)
        login=client.post('/api/auth/login',json={'username':'admin','password':'test-password-12345'})
        self.assertEqual(login.status_code,200)
        client.headers.update({'X-CSRF-Token':client.get('/api/auth/me').json()['csrf_token']})
        with patch.object(rss, 'fetch_feed', lambda u: RSS_XML):
            r = client.post('/api/news/collect').json()
        self.assertEqual(r['added'], 2)
        news = client.get('/api/news').json()
        self.assertEqual(len(news), 2)
        self.assertEqual(news[0]['status'], 'new')
        self.assertEqual(client.get('/api/sources').json()[0]['last_status'], 'ok')


if __name__ == '__main__':
    unittest.main()
