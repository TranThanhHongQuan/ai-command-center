"""Authentication behavior tests (no public deployment required)."""
import os
import tempfile
import unittest

os.environ['AICC_DATA_DIR']=tempfile.mkdtemp()
os.environ['AICC_ADMIN_PASSWORD']='test-password-12345'
os.environ.pop('AICC_COOKIE_SECURE',None)
from fastapi.testclient import TestClient
import server


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(server.app)
        with server.db() as c:
            c.execute('DELETE FROM auth_sessions')
            c.execute('DELETE FROM auth_attempts')

    def login(self):
        r=self.client.post('/api/auth/login',json={'username':'admin','password':'test-password-12345'})
        self.assertEqual(r.status_code,200)
        return self.client.get('/api/auth/me').json()['csrf_token']

    def test_requires_login(self):
        self.assertEqual(self.client.get('/api/news').status_code,401)
        self.assertEqual(self.client.get('/').history[0].status_code,303)
        self.assertEqual(self.client.get('/api/health').status_code,200)

    def test_login_session_csrf_logout(self):
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'admin','password':'incorrect'}).status_code,401)
        csrf=self.login()
        self.assertIn('HttpOnly',self.client.cookies.get('aicc_session') and
                      self.client.post('/api/auth/login',json={'username':'admin','password':'test-password-12345'}).headers['set-cookie'])
        self.assertEqual(self.client.get('/api/news').status_code,200)
        self.assertEqual(self.client.post('/api/news/collect').status_code,403)
        self.assertEqual(self.client.post('/api/auth/logout',headers={'X-CSRF-Token':csrf}).status_code,200)
        self.assertEqual(self.client.get('/api/news').status_code,401)

    def test_rejects_origin_and_bad_host(self):
        csrf=self.login()
        self.assertEqual(self.client.get('/api/news',headers={'Host':'attacker.example'}).status_code,400)
        r=self.client.post('/api/auth/logout',headers={'Origin':'https://attacker.example','X-CSRF-Token':csrf})
        self.assertEqual(r.status_code,403)

    def test_session_expiry(self):
        self.login()
        with server.db() as c:c.execute('UPDATE auth_sessions SET expires_at=0')
        self.assertEqual(self.client.get('/api/news').status_code,401)

    def test_rate_limit(self):
        body={'username':'admin','password':'wrong'}
        for _ in range(5):
            self.assertEqual(self.client.post('/api/auth/login',json=body).status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json=body).status_code,429)

if __name__=='__main__':unittest.main()
