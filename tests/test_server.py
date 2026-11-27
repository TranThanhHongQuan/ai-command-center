import os
import tempfile
import unittest
from unittest.mock import patch

class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.setdefault('AICC_DATA_DIR', cls.tmp.name)
        os.environ.setdefault('AICC_ADMIN_PASSWORD', 'test-password-12345')
        import server
        cls.server = server
        from fastapi.testclient import TestClient
        cls.client = TestClient(server.app)
        assert cls.client.post('/api/auth/login', json={'username': 'admin', 'password': os.environ['AICC_ADMIN_PASSWORD']}).status_code == 200
        cls.client.headers.update({'X-CSRF-Token': cls.client.get('/api/auth/me').json()['csrf_token']})

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_health_and_no_fake_data(self):
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.get('/api/articles').json(), [])
        self.assertEqual(self.client.get('/api/news').json(), [])

    def test_invalid_job_input(self):
        self.assertEqual(self.client.post('/api/jobs',json={'prompt':'short'}).status_code,422)

    def test_approval_does_not_invent_articles(self):
        self.assertEqual(self.client.post('/api/articles/invalid/approval',json={'approved':True}).status_code,404)

    def test_wordpress_rejects_unconfigured(self):
        with patch.dict(os.environ,{'AICC_WP_URL':'','AICC_WP_USER':'','AICC_WP_APP_PASSWORD':''}):
            self.assertEqual(self.client.post('/api/articles/invalid/wordpress').status_code,409)

    def test_cli_unavailable_fails_without_fake_result(self):
        with patch.object(self.server.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'chưa được cài'):
                self.server.run_cli('gemini','Test')

    def test_valid_research_writer_checker_creates_real_review_record(self):
        research='{"summary":"Nguồn", "facts":["Một thông tin"],"sources":[{"title":"Docs","url":"https://example.com/docs"}],"gaps":[]}'
        draft='{"title":"Bài viết", "seo_title":"Bài viết SEO", "meta_description":"Mô tả", "slug":"bai-viet", "body":"Nội dung có dẫn nguồn https://example.com/docs"}'
        verdict='{"pass":true,"notes":[]}'
        page=b'<html>N\xe1\xbb\x99i dung ngu\xe1\xbb\x93n</html>'
        with patch.object(self.server,'run_cli',side_effect=[research,draft,verdict]),patch.object(self.server.rsslib,'fetch_feed',lambda u,**k:page):
            jid='test-workflow'
            with self.server.db() as c:
                t=self.server.now()
                c.execute('INSERT INTO jobs(id,prompt,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                     (jid,'Nghiên cứu có nguồn xác thực','pending','queued',t,t))
            self.server.workflow(jid,'Nghiên cứu có nguồn xác thực')
            obj=self.client.get('/api/jobs/'+jid).json()
            self.assertEqual(obj['job']['status'],'completed')
            articles=self.client.get('/api/articles').json()
            self.assertEqual(len(articles),1)
            self.assertEqual(articles[0]['status'],'review')
            self.assertEqual(articles[0]['title'],'Bài viết')
            article_id=articles[0]['id']
            self.assertEqual(self.client.post('/api/articles/'+article_id+'/wordpress').status_code,409)
            self.assertEqual(self.client.post('/api/articles/'+article_id+'/approval',json={'approved':True}).status_code,200)
            self.assertEqual(self.client.get('/api/articles').json()[0]['status'],'approved')

if __name__=='__main__':unittest.main()
