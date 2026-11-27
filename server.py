"""AI Command Center local single-user server. No mock jobs or credentials collection."""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from html import escape
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
import rss as rsslib
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('AICC_DATA_DIR', str(ROOT / 'data'))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / 'newsroom.sqlite3'
MAX_PROMPT = 12000
app = FastAPI(title='AI Command Center', docs_url=None, redoc_url=None)
lock = threading.RLock()

RSS = [
 ('OpenAI News','https://openai.com/news/rss.xml'),
 ('Google Blog','https://blog.google/rss/'),
 ('GitHub Blog','https://github.blog/feed/'),
 ('TechCrunch','https://techcrunch.com/feed/'),
 ('The Verge','https://www.theverge.com/rss/index.xml'),
 ('Ars Technica','https://feeds.arstechnica.com/arstechnica/index'),
]

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
@contextmanager
def db():
    with lock:
        conn = sqlite3.connect(DB, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally: conn.close()

def init():
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, job_id TEXT, title TEXT NOT NULL, body TEXT NOT NULL, sources TEXT NOT NULL, seo_title TEXT NOT NULL DEFAULT '', meta_description TEXT NOT NULL DEFAULT '', slug TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'review', wp_id INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL UNIQUE, summary TEXT, published TEXT, created_at TEXT NOT NULL);
        ''')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_fetched_at TEXT, last_status TEXT, last_error TEXT, last_added INTEGER);
        ''')
        cols={r['name'] for r in c.execute('PRAGMA table_info(news)')}
        for col,ddl in (('url_norm','TEXT'),('source_id','TEXT'),('status',"TEXT NOT NULL DEFAULT 'new'")):
            if col not in cols:c.execute(f'ALTER TABLE news ADD COLUMN {col} {ddl}')
        for r in c.execute('SELECT id,url FROM news WHERE url_norm IS NULL').fetchall():
            n=rsslib.normalize_url(r['url'])
            if n and not c.execute('SELECT 1 FROM news WHERE url_norm=?',(n,)).fetchone():c.execute('UPDATE news SET url_norm=? WHERE id=?',(n,r['id']))
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_news_url_norm ON news(url_norm)')
        if not c.execute('SELECT 1 FROM sources LIMIT 1').fetchone():
            for name,url in RSS:c.execute('INSERT INTO sources(id,name,url,created_at) VALUES(?,?,?,?)',(str(uuid.uuid4()),name,url,now()))
        c.execute("UPDATE jobs SET status='failed', stage='interrupted', error='Worker stopped; start a new job' WHERE status IN ('running','pending')")
init()

def rows(table: str, limit=100):
    with db() as c:
        return [dict(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY created_at DESC LIMIT ?', (limit,))]

def log(jid: str, actor: str, message: str):
    with db() as c:
        c.execute('INSERT INTO events(job_id,created_at,actor,message) VALUES (?,?,?,?)',(jid,now(),actor,message[:1800]))
        c.execute('UPDATE jobs SET updated_at=?,stage=? WHERE id=?',(now(),actor,jid))

def patch_job(jid,status,error=None,result=None):
    with db() as c:
        c.execute('UPDATE jobs SET status=?,error=?,result=?,updated_at=? WHERE id=?',(status,error,result,now(),jid))

def run_cli(kind:Literal['gemini','codex'],prompt:str,timeout:int=200):
    path=shutil.which(kind)
    if not path: raise RuntimeError(f'{kind} CLI chưa được cài vào PATH. Hãy cài và đăng nhập bằng công cụ chính thức.')
    env=os.environ.copy()
    # No shell interpolation, no --dangerously-bypass-approvals, no remote execution rights.
    cmd=[path,'-p',prompt] if kind=='gemini' else [path,'exec','--skip-git-repo-check','--sandbox','read-only','-']
    try:
        process=subprocess.run(cmd,input=prompt if kind=='codex' else None,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,cwd=DATA_DIR,env=env)
    except subprocess.TimeoutExpired: raise RuntimeError(f'{kind} hết thời gian sau {timeout} giây')
    if process.returncode != 0:
        raise RuntimeError(f'{kind} trả lỗi (mã {process.returncode}): {(process.stderr or process.stdout)[-900:]}')
    text=(process.stdout or '').strip()
    if not text: raise RuntimeError(f'{kind} không trả về nội dung')
    return text[:80000]

def extract_json(text):
    cleaned=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip(),flags=re.I)
    try: return json.loads(cleaned)
    except json.JSONDecodeError:
        start=cleaned.find('{'); end=cleaned.rfind('}')
        if start>=0 and end>start: return json.loads(cleaned[start:end+1])
    raise ValueError('AI không trả JSON hợp lệ; đã lưu lỗi và không tạo bản nháp giả.')

def workflow(jid:str,prompt:str):
    try:
        patch_job(jid,'running')
        log(jid,'MASTER','Bắt đầu: thực thi nghiên cứu → viết → kiểm tra, chỉ tạo bản nháp khi đủ điều kiện.')
        research_prompt=(
          'Bạn là Gemini Researcher. Nghiên cứu yêu cầu bên dưới. Không bịa sự kiện, URL hoặc số liệu; nếu không có quyền tra cứu nguồn thì ghi rõ, không đưa tin chưa xác minh. '
          'Trả về JSON thuần gồm summary (string), facts (list of strings), sources (list of objects {title,url}), gaps (list of strings). '
          'Chỉ đưa URL mà bạn đã xác minh thực sự. Yêu cầu: '+prompt)
        log(jid,'GEMINI','Đang nghiên cứu bằng Gemini CLI từ tài khoản đã đăng nhập.')
        research=extract_json(run_cli('gemini',research_prompt))
        sources=research.get('sources',[])
        verified=[s for s in sources if isinstance(s,dict) and str(s.get('url','')).startswith(('https://','http://'))]
        if not verified: raise RuntimeError('Nghiên cứu không có URL nguồn; từ chối tạo bài đăng.')
        log(jid,'GEMINI',f'Nhận {len(verified)} URL nguồn. Chuyển sang OpenAI Writer.')
        write_prompt=(
           'Bạn là biên tập viên SEO tiếng Việt. Chỉ sử dụng tài liệu nghiên cứu dưới đây, không bịa thêm dữ kiện. '
           'Tạo một bài viết hữu ích, Search Intent rõ, có Information Gain, không nhồi từ khóa. '
           'Trả JSON thuần gồm title, seo_title, meta_description, slug, body (Markdown), sources (mảng URL từ nghiên cứu). '
           'Không đưa URL chưa có trong nghiên cứu. Yêu cầu: '+prompt+'\nNGHIÊN CỨU:\n'+json.dumps(research,ensure_ascii=False)[:19000])
        draft=extract_json(run_cli('codex',write_prompt))
        for field in ('title','body','seo_title','meta_description'):
            if not isinstance(draft.get(field),str) or not draft[field].strip(): raise RuntimeError(f'Writer thiếu trường {field}')
        log(jid,'CODEX','Đã viết bản thảo. Đang kiểm tra chéo nguồn bằng Gemini CLI.')
        check_prompt=(
           'Bạn là QA SEO và fact checker độc lập. Kiểm tra bài chỉ dựa trên nghiên cứu đã cấp, không tự khẳng định đã mở URL. '
           'Nếu có claim chưa chứng minh, không có trích dẫn, thiếu nguồn, hoặc bài không đúng yêu cầu, pass=false. '
           'Trả JSON thuần {"pass":boolean,"notes":[string]}. '
           'RESEARCH:\n'+json.dumps(research,ensure_ascii=False)[:15000]+'\nDRAFT:\n'+json.dumps(draft,ensure_ascii=False)[:22000])
        verdict=extract_json(run_cli('gemini',check_prompt))
        if verdict.get('pass') is not True:
            notes=verdict.get('notes',[])
            log(jid,'CHECKER','Không đạt: '+', '.join(map(str,notes))[:800])
            patch_job(jid,'needs_revision',result=json.dumps({'research':research,'draft':draft,'verdict':verdict},ensure_ascii=False))
            return
        article_id=str(uuid.uuid4())
        slug=re.sub(r'[^a-z0-9-]','-',str(draft.get('slug','')).lower()).strip('-')[:150]
        with db() as c:
            c.execute('INSERT INTO articles(id,job_id,title,body,sources,seo_title,meta_description,slug,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
             (article_id,jid,draft['title'][:300],draft['body'][:60000],json.dumps(verified,ensure_ascii=False),draft['seo_title'][:300],draft['meta_description'][:500],slug,'review',now()))
        log(jid,'CHECKER','Đã qua bước kiểm tra sơ bộ; lưu bài ở trạng thái chờ người duyệt.')
        patch_job(jid,'completed',result=json.dumps({'article_id':article_id,'verdict':verdict},ensure_ascii=False))
    except Exception as ex:
        log(jid,'ERROR',str(ex)[:900]);patch_job(jid,'failed',error=str(ex)[:1500])

class TaskRequest(BaseModel):
    prompt: str = Field(min_length=10,max_length=MAX_PROMPT)
class ArticleUpdate(BaseModel):
    title:str = Field(min_length=3,max_length=300)
    body:str = Field(min_length=5,max_length=60000)
    seo_title:str = Field(default='',max_length=300)
    meta_description:str = Field(default='',max_length=500)
    slug:str = Field(default='',max_length=150)
class Approval(BaseModel):
    approved:bool

@app.get('/')
def home(): return FileResponse(ROOT/'static'/'index.html')
@app.get('/app.css')
def css(): return FileResponse(ROOT/'static'/'app.css',media_type='text/css')
@app.get('/app.js')
def js(): return FileResponse(ROOT/'static'/'app.js',media_type='text/javascript')
@app.get('/api/health')
def health(): return {'ok':True,'mode':'real','time':now()}
@app.get('/api/providers')
def providers():
    return {'gemini':{'installed':bool(shutil.which('gemini')),'auth':'Kiểm tra bằng lệnh gemini trên máy chủ'},
            'codex':{'installed':bool(shutil.which('codex')),'auth':'Kiểm tra bằng lệnh codex login status trên máy chủ'},
            'claude':{'installed':bool(shutil.which('claude')),'auth':'Chưa tích hợp Claude cá nhân vào web; cần phương thức hợp lệ'},
            'wordpress':{'configured':all(os.environ.get(x) for x in ('AICC_WP_URL','AICC_WP_USER','AICC_WP_APP_PASSWORD'))}}
@app.get('/api/jobs')
def jobs(): return rows('jobs')
@app.get('/api/jobs/{jid}')
def job(jid:str):
    with db() as c:
        r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r: raise HTTPException(404,'Không tìm thấy nhiệm vụ')
        events=[dict(x) for x in c.execute('SELECT * FROM events WHERE job_id=? ORDER BY id',(jid,))]
    return {'job':dict(r),'events':events}
@app.post('/api/jobs',status_code=201)
def create_job(req:TaskRequest):
    jid=str(uuid.uuid4())
    with db() as c:
        c.execute('INSERT INTO jobs(id,prompt,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)',(jid,req.prompt,'pending','queued',now(),now()))
    threading.Thread(target=workflow,args=(jid,req.prompt),daemon=True).start()
    return {'id':jid,'status':'pending'}
@app.get('/api/articles')
def articles():return rows('articles')
@app.put('/api/articles/{aid}')
def edit_article(aid:str,req:ArticleUpdate):
    with db() as c:
        found=c.execute('SELECT status FROM articles WHERE id=?',(aid,)).fetchone()
        if not found:raise HTTPException(404,'Không tìm thấy bài')
        if found['status']=='sent_to_wp':raise HTTPException(409,'Bài đã gửi WordPress; không sửa bản ghi này')
        c.execute('UPDATE articles SET title=?,body=?,seo_title=?,meta_description=?,slug=?,status=? WHERE id=?',
                 (req.title,req.body,req.seo_title,req.meta_description,req.slug,'review',aid))
    return {'ok':True}
@app.post('/api/articles/{aid}/approval')
def approve(aid:str,req:Approval):
    with db() as c:
        found=c.execute('SELECT id,status FROM articles WHERE id=?',(aid,)).fetchone()
        if not found:raise HTTPException(404,'Không tìm thấy bài')
        if found['status']=='sent_to_wp':raise HTTPException(409,'Đã gửi WordPress')
        c.execute('UPDATE articles SET status=? WHERE id=?',('approved' if req.approved else 'needs_revision',aid))
    return {'ok':True}
@app.post('/api/articles/{aid}/wordpress')
def wordpress(aid:str):
    url=os.environ.get('AICC_WP_URL','').strip().rstrip('/')
    user=os.environ.get('AICC_WP_USER','')
    pwd=os.environ.get('AICC_WP_APP_PASSWORD','')
    if not (url and user and pwd):raise HTTPException(409,'Chưa cấu hình WordPress trong môi trường server')
    if not url.startswith('https://'):raise HTTPException(400,'WordPress URL phải sử dụng HTTPS')
    with db() as c:
        article=c.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone()
        if not article:raise HTTPException(404,'Không tìm thấy bài')
        if article['status']!='approved':raise HTTPException(409,'Cần người duyệt bài trước khi gửi WordPress')
        article=dict(article)
    import base64
    token=base64.b64encode(f'{user}:{pwd}'.encode()).decode()
    # Stored body is Markdown; submit plain text with newlines rather than untrusted HTML.
    content='<p>'+escape(article['body']).replace('\n\n','</p><p>').replace('\n','<br>')+'</p>'
    payload=json.dumps({'title':article['title'],'content':content,'status':'draft','slug':article['slug'],'excerpt':article['meta_description']}).encode()
    request=urllib.request.Request(url+'/wp-json/wp/v2/posts',data=payload,headers={'Authorization':'Basic '+token,'Content-Type':'application/json','User-Agent':'AICommandCenter/1.0'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=25) as response: obj=json.loads(response.read(500000))
    except Exception as ex:raise HTTPException(502,f'WordPress từ chối hoặc không phản hồi: {str(ex)[:300]}')
    wp_id=obj.get('id')
    if not wp_id:raise HTTPException(502,'WordPress chưa xác nhận ID bài viết')
    with db() as c:c.execute("UPDATE articles SET status='sent_to_wp',wp_id=? WHERE id=?",(wp_id,aid))
    return {'wp_id':wp_id,'status':'draft','link':obj.get('link')}
class SourceIn(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    url:str=Field(min_length=8,max_length=2000)
class SourcePatch(BaseModel):
    enabled:bool
@app.get('/api/sources')
def sources():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT s.*,(SELECT COUNT(*) FROM news n WHERE n.source_id=s.id) AS news_count FROM sources s ORDER BY name')]
@app.post('/api/sources',status_code=201)
def add_source(req:SourceIn):
    url=req.url.strip()
    try:rsslib.check_feed_url(url)
    except ValueError as ex:raise HTTPException(422,str(ex))
    sid=str(uuid.uuid4())
    with db() as c:
        if c.execute('SELECT 1 FROM sources WHERE url=?',(url,)).fetchone():raise HTTPException(409,'Nguồn đã tồn tại')
        c.execute('INSERT INTO sources(id,name,url,created_at) VALUES(?,?,?,?)',(sid,req.name.strip(),url,now()))
    return {'id':sid}
@app.patch('/api/sources/{sid}')
def patch_source(sid:str,req:SourcePatch):
    with db() as c:
        if not c.execute('UPDATE sources SET enabled=? WHERE id=?',(int(req.enabled),sid)).rowcount:raise HTTPException(404,'Không có nguồn')
    return {'ok':True}
@app.delete('/api/sources/{sid}')
def delete_source(sid:str):
    with db() as c:
        if not c.execute('DELETE FROM sources WHERE id=?',(sid,)).rowcount:raise HTTPException(404,'Không có nguồn')
    return {'ok':True}
_collecting=threading.Lock()
@app.post('/api/news/collect')
def collect():
    if not _collecting.acquire(blocking=False):raise HTTPException(409,'Đang thu thập, vui lòng chờ')
    try:
        with db() as c:srcs=[dict(r) for r in c.execute('SELECT * FROM sources WHERE enabled=1')]
        results=[rsslib.collect_source(db,x) for x in srcs]
    finally:_collecting.release()
    return {'added':sum(r['added'] for r in results),'skipped':sum(r['skipped'] for r in results),
            'errors':[f"{r['source']}: {r['error']}" for r in results if not r['ok']],'results':results}
@app.get('/api/news')
def news():return rows('news',200)
