import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('AICC_DATA_DIR', tempfile.mkdtemp())
os.environ.setdefault('AICC_ADMIN_PASSWORD', 'test-password-12345')
import editor  # noqa: E402
import scheduler  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

NOW = dt.datetime(2026, 1, 10, 12, tzinfo=dt.timezone.utc)
CFG = editor.load_config({})


def ago(h, base=NOW):
    return (base - dt.timedelta(hours=h)).isoformat(timespec='seconds')


class EditorBase(unittest.TestCase):
    def setUp(self):
        with server.db() as c:
            for t in ('news', 'sources', 'jobs', 'articles', 'editor_items', 'editor_groups', 'collect_runs', 'locks'):
                c.execute(f'DELETE FROM {t}')
            for sid, name, cat in (('so', 'OpenAI News', 'official'), ('stc', 'TechCrunch', 'international_press'), ('sv', 'The Verge', 'international_press'),
                                   ('sa', 'Ars Technica', 'international_press'), ('svn', 'GenK', 'vietnam_tech')):
                c.execute("INSERT INTO sources(id,name,url,created_at,category) VALUES(?,?,?,?,?)", (sid, name, f'https://{sid}.test/feed', 'x', cat))
        self.n = 0
        self.base = NOW

    tearDown = setUp

    def add(self, title, sid='stc', pub_h=2, url=None, summary='', created_h=1, norm=None):
        self.n += 1
        nid = f'n{self.n}'
        url = url or f'https://{sid}.test/p/{nid}'
        name = {'so': 'OpenAI News', 'stc': 'TechCrunch', 'sv': 'The Verge', 'sa': 'Ars Technica', 'svn': 'GenK'}[sid]
        with server.db() as c:
            c.execute('INSERT INTO news(id,title,source,source_id,url,url_norm,summary,published,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (nid, title, name, sid, url, norm if norm is not None else url, summary, ago(pub_h, self.base) if pub_h is not None else '', ago(created_h, self.base), 'new'))
        return nid

    def run_editor(self):
        editor.analyze(server.db, now=NOW, cfg=CFG)
        return editor.overview(server.db, CFG)

    def group_of(self, ov, nid):
        for g in ov['groups']:
            if any(u['news_id'] == nid for u in g['items']):
                return g

    def reason(self, nid):
        with server.db() as c:
            r = c.execute('SELECT status,filter_reason FROM editor_items WHERE news_id=?', (nid,)).fetchone()
        return tuple(r) if r else None


class FilterTests(EditorBase):
    def test_duplicate_url_flagged_without_deleting(self):
        a = self.add('OpenAI ChatGPT update', url='https://x.test/a?utm_source=z', norm='k1')
        b = self.add('OpenAI ChatGPT update again', url='http://www.x.test/a/', norm='k2')  # url_norm khác nhưng cùng URL chuẩn hóa
        self.run_editor()
        self.assertEqual(self.reason(a)[0], 'candidate')
        self.assertEqual(self.reason(b), ('filtered', 'duplicate_url'))
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM news').fetchone()[0], 2)

    def test_duplicate_title_same_source(self):
        a = self.add('Google Gemini ra mắt tính năng mới', url='https://stc.test/1')
        b = self.add('Google Gemini ra mắt tính năng mới!', url='https://stc.test/2')
        self.run_editor()
        self.assertEqual(self.reason(a)[0], 'candidate')
        self.assertEqual(self.reason(b), ('filtered', 'duplicate_title'))

    def test_stale_invalid_and_not_tech(self):
        old = self.add('Google Gemini big launch', pub_h=24 * 20)
        bad = self.add('   ', url='https://stc.test/bad')
        nourl = self.add('OpenAI ChatGPT thing', url='ftp://x')
        bake = self.add('Local bakery wins regional award')
        self.run_editor()
        self.assertEqual(self.reason(old), ('filtered', 'stale'))
        self.assertEqual(self.reason(bad), ('filtered', 'invalid'))
        self.assertEqual(self.reason(nourl), ('filtered', 'invalid'))
        self.assertEqual(self.reason(bake), ('filtered', 'not_tech'))
        outage = self.add('Cloudflare outage hits customers worldwide', sid='so', url='https://so.test/outage')
        self.run_editor()
        self.assertEqual(self.reason(outage)[0], 'candidate')  # nguồn chính thức không bị loại vì thiếu từ khóa

    def test_processed_event_is_excluded(self):
        a = self.add('Nvidia launches RTX 5090 graphics card')
        with server.db() as c:
            c.execute("INSERT INTO jobs(id,prompt,status,stage,created_at,updated_at,news_id) VALUES('j','p','completed','x','x','x',?)", (a,))
        b = self.add('Nvidia RTX 5090 GPU launched', sid='sv', pub_h=1)
        ov = self.run_editor()
        g = self.group_of(ov, a)
        self.assertEqual((g['status'], g['recommended']), ('processed', False))
        self.assertEqual(self.reason(b), ('filtered', 'processed'))


class GroupingTests(EditorBase):
    def test_same_event_across_sources_in_one_group(self):
        a = self.add('NVIDIA launches GeForce RTX 5090 graphics card', sid='stc', pub_h=5)
        b = self.add("Nvidia's new RTX 5090 GPU launched", sid='sv', pub_h=4)
        c = self.add('Nvidia RTX 5090 GPU launch: graphics card explained', sid='sa', pub_h=3)
        g = self.group_of(self.run_editor(), a)
        self.assertEqual((g['size'], g['source_count']), (3, 3))
        self.assertEqual({u['news_id'] for u in g['items']}, {a, b, c})
        self.assertEqual(len({u['url'] for u in g['items']}), 3)

    def test_vietnamese_and_english_same_event(self):
        a = self.add('Samsung unveils Galaxy S26 Ultra', sid='sv')
        b = self.add('Samsung ra mắt Galaxy S26 Ultra tại Việt Nam', sid='svn')
        ov = self.run_editor()
        self.assertEqual(self.group_of(ov, a)['group_id'], self.group_of(ov, b)['group_id'])

    def test_different_events_same_keywords_not_merged(self):
        ids = [self.add('Nvidia unveils RTX 5080 GPU'), self.add('Nvidia unveils RTX 5090 GPU', sid='sv'),
               self.add('Nvidia reports record earnings revenue on AI chip demand', sid='sa'),
               self.add('Apple fixes iPhone security flaw', sid='sv'), self.add('Apple unveils iPhone camera feature', sid='stc'),
               self.add('Apple iPhone 16 battery review', sid='sa'), self.add('Apple iPhone 17 battery review', sid='svn')]
        ov = self.run_editor()
        self.assertEqual(len({self.group_of(ov, i)['group_id'] for i in ids}), len(ids))

    def test_events_far_apart_in_time_not_merged(self):
        a = self.add('Nvidia launches RTX 5090 GPU graphics card', pub_h=2)
        b = self.add('Nvidia launches RTX 5090 GPU graphics card', sid='sv', pub_h=24 * 6)
        ov = self.run_editor()
        self.assertNotEqual(self.group_of(ov, a)['group_id'], self.group_of(ov, b)['group_id'])

    def test_representative_prefers_official_source(self):
        a = self.add('OpenAI launches GPT 5 model ChatGPT', sid='stc', pub_h=5, summary='longer text ' * 20)
        b = self.add('OpenAI launches GPT 5 model in ChatGPT', sid='so', pub_h=4)
        g = self.group_of(self.run_editor(), a)
        self.assertEqual(g['rep_news_id'], b)


class ScoringTests(EditorBase):
    def test_bounds_breakdown_and_grade(self):
        self.add('OpenAI ChatGPT Gemini security update tại Việt Nam', sid='so', pub_h=1)
        self.add('Tin lạ về bánh mì chip điện thoại', sid='stc', pub_h=100)
        ov = self.run_editor()
        for g in ov['groups']:
            self.assertTrue(0 <= g['score'] <= 100)
            self.assertAlmostEqual(sum(g['breakdown'].values()), g['score'], delta=1)
            self.assertTrue(g['reasons'])
            self.assertIn(g['grade'], editor.GRADE_LABELS)
        top = max(ov['groups'], key=lambda g: g['score'])
        self.assertGreaterEqual(top['score'], 80)
        self.assertEqual(top['grade'], 'very_high')

    def test_missing_date_scored_lower_with_reason(self):
        a = self.add('OpenAI ChatGPT Gemini launch', sid='so', pub_h=1)
        b = self.add('OpenAI ChatGPT Gemini hands-on review', sid='so', pub_h=None, url='https://so.test/other')
        ov = self.run_editor()
        ga, gb = self.group_of(ov, a), self.group_of(ov, b)
        self.assertNotEqual(ga['group_id'], gb['group_id'])
        self.assertGreater(ga['score'], gb['score'])
        self.assertTrue(any('không rõ ngày' in r for r in gb['reasons']))
        self.assertEqual(self.reason(b)[0], 'candidate')  # thiếu ngày không bị loại

    def test_vietnam_relevance_and_source_value(self):
        vn = self.add('Viettel ra mắt dịch vụ cloud 5G mới', sid='svn')
        us = self.add('Acme ships cloud 5G service', sid='stc', url='https://stc.test/zz')
        ov = self.run_editor()
        self.assertGreater(self.group_of(ov, vn)['breakdown']['vietnam'], self.group_of(ov, us)['breakdown']['vietnam'])
        multi = [self.add('Microsoft Windows security patch flaw fixed', sid=s, pub_h=3) for s in ('so', 'sv')]
        g = self.group_of(self.run_editor(), multi[0])
        self.assertEqual(g['source_count'], 2)
        self.assertGreater(g['breakdown']['source'], 9)

    def test_weights_are_configurable_and_normalized(self):
        cfg = editor.load_config({'AICC_EDITOR_WEIGHTS': 'freshness=60,tech=10,vietnam=10,source=10,novelty=10'})
        self.assertAlmostEqual(sum(cfg['weights'].values()), 100)
        self.assertEqual(cfg['weights']['freshness'], 60)
        self.assertEqual(editor.load_config({'AICC_EDITOR_WEIGHTS': 'freshness=abc'})['weights'], editor.DEFAULT_WEIGHTS)
        self.assertEqual(editor.load_config({'AICC_EDITOR_THRESHOLDS': 'very_high=10,high=50,watch=20'})['thresholds'], editor.DEFAULT_THRESHOLDS)
        self.assertEqual([editor.grade(s, CFG['thresholds']) for s in (95, 80, 79, 65, 64, 40, 39)],
                         ['very_high', 'very_high', 'high', 'high', 'watch', 'watch', 'low'])

    def test_daily_cap_and_manual_selection(self):
        ids = [self.add(f'OpenAI ChatGPT Gemini model v{i}00 launch', sid='so', pub_h=1) for i in range(1, 8)]
        ov = self.run_editor()
        self.assertEqual(len(ov['top']), 5)
        self.assertEqual(ov['stats']['passing'], 7)
        gid = self.group_of(ov, ids[0])['group_id']
        editor.select(server.db, gid, 0)  # loại khỏi đề xuất
        self.run_editor()  # lựa chọn được giữ sau khi phân tích lại
        ov = editor.overview(server.db, CFG)
        self.assertNotIn(gid, [g['group_id'] for g in ov['top']])
        low = self.add('Gadget chip update', sid='stc', pub_h=40, url='https://stc.test/low')
        ov = self.run_editor()
        lg = self.group_of(ov, low)
        editor.select(server.db, lg['group_id'], 1)  # chọn thủ công
        self.assertIn(lg['group_id'], [g['group_id'] for g in editor.overview(server.db, CFG)['top']])


class IdempotencyAndApiTests(EditorBase):
    def test_repeat_analysis_no_duplicates_and_news_untouched(self):
        for t in ('OpenAI ChatGPT Gemini launch', 'Nvidia launches RTX 5090 GPU', 'Nvidia RTX 5090 GPU launched', 'bakery news'):
            self.add(t)
        with server.db() as c:
            before = c.execute('SELECT id,title,url,url_norm,published FROM news ORDER BY id').fetchall()
        first = self.run_editor()
        for _ in range(3):
            again = self.run_editor()
        with server.db() as c:
            counts = [c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('editor_items', 'editor_groups')]
            self.assertEqual(c.execute('SELECT id,title,url,url_norm,published FROM news ORDER BY id').fetchall(), before)
        self.assertEqual(counts[0], 4)
        self.assertEqual(counts[1], first['stats']['events'])
        self.assertEqual([g['group_id'] for g in first['groups']], [g['group_id'] for g in again['groups']])

    def test_api_flow_and_research_uses_existing_pipeline(self):
        self.base = dt.datetime.now(dt.timezone.utc)  # API dùng thời gian thật
        a = self.add('OpenAI ChatGPT Gemini launch', sid='so')
        client = TestClient(server.app)
        self.assertEqual(client.get('/api/editor').status_code, 401)
        client.post('/api/auth/login', json={'username': 'admin', 'password': os.environ['AICC_ADMIN_PASSWORD']})
        client.headers.update({'X-CSRF-Token': client.get('/api/auth/me').json()['csrf_token']})
        r = client.post('/api/editor/analyze').json()
        self.assertEqual((r['analyzed'], r['events']), (1, 1))
        ov = client.get('/api/editor').json()
        gid = ov['groups'][0]['group_id']
        self.assertEqual(client.post(f'/api/editor/groups/{gid}/select', json={'choice': 1}).status_code, 200)
        self.assertEqual(client.post('/api/editor/groups/nope/select', json={'choice': 1}).status_code, 404)
        self.assertEqual(client.post('/api/editor/groups/nope/research').status_code, 404)
        with patch.object(server.shutil, 'which', lambda k: None):  # không có CLI: lỗi rõ ràng, không tạo job
            self.assertEqual(client.post(f'/api/editor/groups/{gid}/research').status_code, 409)
        with patch.object(server.shutil, 'which', lambda k: '/bin/' + k), patch.object(server, 'run_cli') as cli, \
             patch.object(server.threading, 'Thread', lambda target, args, daemon: type('T', (), {'start': lambda s: None})()):
            self.assertEqual(client.post(f'/api/editor/groups/{gid}/research').status_code, 201)
            cli.assert_not_called()
        with server.db() as c:
            self.assertEqual(c.execute('SELECT news_id FROM jobs').fetchone()[0], a)

    def test_runs_after_rss_collection_without_ai(self):
        feed = b'<rss><channel><item><title>OpenAI ChatGPT Gemini launch</title><link>https://so.test/a</link></item></channel></rss>'
        with server.db() as c:
            c.execute("UPDATE sources SET source_type='rss',enabled=1 WHERE id='so'")
        with patch.object(server, 'run_cli', side_effect=AssertionError('không được gọi AI')):
            out = scheduler.run_collection(server.db, 'manual', lambda u: feed)
        self.assertEqual(out['added'], 1)
        with server.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM editor_groups').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
