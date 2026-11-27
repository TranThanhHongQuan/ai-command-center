"""Single-admin authentication. Bootstrap once with AICC_ADMIN_PASSWORD (12+ chars)."""
import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from pydantic import BaseModel, Field

SESSION_AGE=12*3600
WINDOW=15*60

class LoginRequest(BaseModel):
    username: str=Field(min_length=1,max_length=100)
    password: str=Field(min_length=1,max_length=2048)

def hash_password(password,salt):
    return hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1,dklen=32)

def valid_host(raw,allowed):
    if not raw or any(c in raw for c in ('/','\\','@','?','#',' ','\r','\n')):
        return False
    try:
        host=urlsplit('http://'+raw)
        _=host.port
        return (host.hostname or '').lower().rstrip('.') in allowed
    except ValueError:
        return False

def install_security(app,db,static_root:Path):
    admin=os.getenv('AICC_ADMIN_USER','admin').strip() or 'admin'
    hosts={x.strip().lower().rstrip('.') for x in os.getenv('AICC_ALLOWED_HOSTS','localhost,127.0.0.1,testserver').split(',') if x.strip()}
    origins={x.strip().rstrip('/') for x in os.getenv('AICC_ALLOWED_ORIGINS','').split(',') if x.strip()}
    force_secure=os.getenv('AICC_COOKIE_SECURE','0').lower() in ('1','true','yes')

    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS admin_users(username TEXT PRIMARY KEY,salt TEXT NOT NULL,password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,csrf_token TEXT NOT NULL,expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_attempts(attempt_key TEXT PRIMARY KEY,failures INTEGER NOT NULL,window_start INTEGER NOT NULL,blocked_until INTEGER NOT NULL DEFAULT 0);
        """)
        if not c.execute('SELECT 1 FROM admin_users LIMIT 1').fetchone():
            pwd=os.getenv('AICC_ADMIN_PASSWORD','')
            if len(pwd)>=12:
                salt=secrets.token_bytes(16)
                c.execute('INSERT INTO admin_users VALUES(?,?,?)',(admin,base64.b64encode(salt).decode(),base64.b64encode(hash_password(pwd,salt)).decode()))

    def identity(request):
        token=request.cookies.get('aicc_session','')
        if not token or len(token)>150:return None
        digest=hashlib.sha256(token.encode()).hexdigest()
        with db() as c:
            r=c.execute('SELECT username,csrf_token,expires_at FROM auth_sessions WHERE token_hash=?',(digest,)).fetchone()
            if not r:return None
            if r['expires_at']<=int(time.time()):
                c.execute('DELETE FROM auth_sessions WHERE token_hash=?',(digest,))
                return None
            return {'username':r['username'],'csrf_token':r['csrf_token'],'token_hash':digest}

    @app.middleware('http')
    async def security_gate(request:Request,call_next):
        raw_host=request.headers.get('host','')
        if not valid_host(raw_host,hosts):
            return JSONResponse({'detail':'Host không được phép'},status_code=400)
        path,method=request.url.path,request.method.upper()
        unsafe=method in ('POST','PUT','PATCH','DELETE')
        origin=request.headers.get('origin')
        if unsafe and origin and origin.rstrip('/') not in ({f'{request.url.scheme}://{raw_host}'}|origins):
            return JSONResponse({'detail':'Origin không được phép'},status_code=403)
        public=path in ('/login','/login.js','/app.css','/api/health','/api/auth/login')
        if not public:
            user=identity(request)
            if not user:
                if path=='/' and method=='GET':return RedirectResponse('/login',status_code=303)
                return JSONResponse({'detail':'Cần đăng nhập'},status_code=401)
            if unsafe and not secrets.compare_digest(request.headers.get('x-csrf-token',''),user['csrf_token']):
                return JSONResponse({'detail':'CSRF token không hợp lệ'},status_code=403)
            request.state.admin=user
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        return response

    @app.get('/login')
    def login_page():return FileResponse(static_root/'login.html')
    @app.get('/login.js')
    def login_script():return FileResponse(static_root/'login.js',media_type='text/javascript')
    @app.post('/api/auth/login')
    def login(request:Request,body:LoginRequest):
        now=int(time.time())
        key=hashlib.sha256(f'{request.client.host if request.client else "unknown"}:{body.username}'.encode()).hexdigest()
        with db() as c:
            state=c.execute('SELECT * FROM auth_attempts WHERE attempt_key=?',(key,)).fetchone()
            if state and state['blocked_until']>now:
                return JSONResponse({'detail':'Quá nhiều lần đăng nhập; thử sau'},status_code=429)
            account=c.execute('SELECT salt,password_hash FROM admin_users WHERE username=?',(body.username,)).fetchone()
        salt=base64.b64decode(account['salt']) if account else b'\0'*16
        expected=base64.b64decode(account['password_hash']) if account else b'\0'*32
        valid=hmac.compare_digest(hash_password(body.password,salt),expected)
        if not (account and valid):
            failures=state['failures']+1 if state and now-state['window_start']<WINDOW else 1
            started=state['window_start'] if state and now-state['window_start']<WINDOW else now
            blocked=now+WINDOW if failures>=5 else 0
            with db() as c:
                c.execute("""INSERT INTO auth_attempts VALUES(?,?,?,?)
                ON CONFLICT(attempt_key) DO UPDATE SET failures=excluded.failures,window_start=excluded.window_start,blocked_until=excluded.blocked_until""",(key,failures,started,blocked))
            return JSONResponse({'detail':'Sai tài khoản hoặc mật khẩu'},status_code=401)
        token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        with db() as c:
            c.execute('DELETE FROM auth_attempts WHERE attempt_key=?',(key,))
            c.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(now,))
            c.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),body.username,csrf,now+SESSION_AGE))
        response=JSONResponse({'ok':True})
        response.set_cookie('aicc_session',token,max_age=SESSION_AGE,httponly=True,
                            secure=force_secure or request.url.scheme=='https',samesite='strict',path='/')
        return response
    @app.get('/api/auth/me')
    def me(request:Request):return {'user':request.state.admin['username'],'csrf_token':request.state.admin['csrf_token']}
    @app.post('/api/auth/logout')
    def logout(request:Request):
        with db() as c:
            c.execute('DELETE FROM auth_sessions WHERE token_hash=?',(request.state.admin['token_hash'],))
        response=JSONResponse({'ok':True})
        response.delete_cookie('aicc_session',path='/',samesite='strict')
        return response
