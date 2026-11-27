import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('AICC_DATA_DIR', tempfile.mkdtemp())
os.environ.setdefault('AICC_ADMIN_PASSWORD', 'test-password-12345')
import scoring  # noqa: E402
import verify  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

NOW = dt.datetime(2026, 1, 10, 12, tzinfo=dt.timezone.utc)
PAGE = 'Nội dung gốc: OpenAI công bố mô hình mới cho nhà phát triển Việt Nam'.encode()
RESEARCH = '{"summary":"s","facts":["f"],"sources":[{"title":"A","url":"https://good.test/a","quote":"OpenAI công bố mô hình mới cho nhà phát triển"},{"title":"B","url":"https://dead.test/b"},{"title":"C","url":"https://good.test/c","quote":"câu trích dẫn hoàn toàn bịa đặt không có"}],"gaps":[]}'
DRAFT = '{"title":"Bài","seo_title":"S","meta_description":"M","slug":"bai","body":"Nội dung"}'


def fake_fetch(url, **k):
    if 'dead.test' in url:
        raise OSError('HTTP 404')
    return PAGE


class ScoringTests(unittest.TestCase):
    def s(self, title, summary='', hours=2):
        return scoring.score_item(title, summary, (NOW - dt.timedelta(hours=hours)).isoformat(), NOW)

    def test_fresh_tech_vn_beats_old_generic(self):
        hi = self.s('OpenAI ra mắt ChatGPT mới cho người dùng Việt Nam', hours=1)
        lo = self.s('Local bakery opens new store', hours=500)
        self.assertGreater(hi['score'], 70)
        self.assertEqual(lo['score'], 0)

    def test_freshness_and_unknown_date(self):
        self.assertGreater(self.s('x', hours=3)['score'], self.s('x', hours=50)['score'])
        self.assertEqual(scoring.score_item('x', '', '', NOW)['score'], 5)

    def test_word_boundary(self):
        self.assertEqual(self.s('said paid maintain', hours=900)['score'], 0)  # 'ai' không khớp trong từ khác


class VerifyTests(unittest.TestCase):
    def test_only_fetchable_sources_verified_and_quotes_checked(self):
        src = [{'url': 'https://good.test/a', 'quote': 'OpenAI công bố mô hình mới cho nhà phát triển'}, {'url': 'https://dead.test/b'},
               {'url': 'https://good.test/c', 'quote': 'câu trích dẫn hoàn toàn bịa đặt không có'}, {'url': 'javascript:x'}, 'rác']
        out = verify.verify_sources(src, ['https://good.test/a/?utm_source=z'], fetcher=fake_fetch)
        by = {o['url']: o for o in out}
        self.assertEqual(len(out), 3)  # URL trùng và URL không hợp lệ bị loại
        self.assertTrue(by['https://good.test/a']['verified'] and by['https://good.test/a']['quote_found'])
        self.assertFalse(by['https://dead.test/b']['verified'])
        self.assertTrue(by['https://good.test/c']['verified'] and by['https://good.test/c']['quote_found'] is False)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)
        cls.client.post('/api/auth/login', json={'username': 'admin', 'password': os.environ['AICC_ADMIN_PASSWORD']})
        cls.client.headers.update({'X-CSRF-Token': cls.client.get('/api/auth/me').json()['csrf_token']})

    def setUp(self):
        with server.db() as c:
            for t in ('jobs', 'events', 'articles', 'news'):
                c.execute(f'DELETE FROM {t}')
            c.execute("INSERT INTO news(id,title,source,url,url_norm,summary,published,created_at) VALUES('n1','OpenAI ra mắt mô hình mới','T','https://good.test/a','https://good.test/a','Tin AI','"
                      + dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds') + "','x')")
        p = patch.object(server.shutil, 'which', lambda k: '/bin/' + k)
        p.start(); self.addCleanup(p.stop)
        t = patch.object(server.threading, 'Thread', lambda target, args, daemon: type('T', (), {'start': lambda s: None})())
        t.start(); self.addCleanup(t.stop)  # không tự chạy nền; tự gọi workflow

    def run_wf(self, jid, outputs):
        with server.db() as c:
            prompt, url = c.execute('SELECT prompt FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'https://good.test/a'
        with patch.object(server, 'run_cli', side_effect=outputs), patch.object(server.rsslib, 'fetch_feed', fake_fetch):
            server.workflow(jid, prompt, url)
        return self.client.get('/api/jobs/' + jid).json()

    def test_news_list_has_score_sorted(self):
        items = self.client.get('/api/news').json()
        self.assertGreater(items[0]['score'], 0)
        self.assertIsNone(items[0]['job_id'])

    def test_missing_cli_clear_error(self):
        with patch.object(server.shutil, 'which', lambda k: None):
            r = self.client.post('/api/news/n1/research')
        self.assertEqual(r.status_code, 409)
        self.assertIn('CLI chưa sẵn sàng', r.json()['detail'])
        self.assertEqual(self.client.get('/api/jobs').json(), [])

    def test_unknown_news_404(self):
        self.assertEqual(self.client.post('/api/news/nope/research').status_code, 404)

    def test_duplicate_and_concurrent_blocked(self):
        r = self.client.post('/api/news/n1/research')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.client.post('/api/news/n1/research').status_code, 409)  # cùng tin
        self.assertEqual(self.client.post('/api/jobs', json={'prompt': 'một nhiệm vụ khác dài đủ'}).status_code, 409)  # đang có pipeline chạy
        self.assertEqual(self.client.get('/api/news').json()[0]['job_status'], 'pending')

    def test_success_saves_verified_sources_to_review(self):
        jid = self.client.post('/api/news/n1/research').json()['id']
        out = self.run_wf(jid, [RESEARCH, DRAFT, '{"pass":true,"notes":[]}'])
        self.assertEqual(out['job']['status'], 'completed')
        self.assertTrue(any(e['actor'] == 'VERIFY' and '2/3' in e['message'] for e in out['events']))
        art = self.client.get('/api/articles').json()[0]
        self.assertEqual(art['status'], 'review')
        import json
        srcs = json.loads(art['sources'])
        self.assertTrue(all(s['verified'] for s in srcs))
        self.assertNotIn('https://dead.test/b', [s['url'] for s in srcs])
        self.assertEqual(self.client.post('/api/news/n1/research').status_code, 409)  # hoàn thành vẫn chặn trùng

    def test_checker_fail_needs_revision_no_article(self):
        jid = self.client.post('/api/news/n1/research').json()['id']
        out = self.run_wf(jid, [RESEARCH, DRAFT, '{"pass":false,"notes":["thiếu nguồn"]}'])
        self.assertEqual(out['job']['status'], 'needs_revision')
        self.assertEqual(self.client.get('/api/articles').json(), [])

    def test_no_verifiable_source_fails_and_allows_retry(self):
        jid = self.client.post('/api/news/n1/research').json()['id']
        with patch.object(server, 'run_cli', return_value='{"summary":"s","facts":[],"sources":[{"url":"https://dead.test/b"}],"gaps":[]}'), \
             patch.object(server.rsslib, 'fetch_feed', fake_fetch):
            server.workflow(jid, 'p', None)
        self.assertEqual(self.client.get('/api/jobs/' + jid).json()['job']['status'], 'failed')
        self.assertEqual(self.client.get('/api/articles').json(), [])
        self.assertEqual(self.client.post('/api/news/n1/research').status_code, 201)  # lỗi thì cho thử lại thủ công


if __name__ == '__main__':
    unittest.main()
