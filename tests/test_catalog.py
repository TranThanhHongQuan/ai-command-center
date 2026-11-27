"""Danh mục nguồn: không tạo trùng, không quét các trang HTML và không xóa nguồn cá nhân."""
import os
import sqlite3
import tempfile
import unittest
import uuid

os.environ['AICC_DATA_DIR'] = tempfile.mkdtemp()
os.environ['AICC_ADMIN_PASSWORD'] = 'test-password-12345'
import server
import source_catalog


class CatalogTests(unittest.TestCase):
    def test_catalog_has_25_unique_urls(self):
        self.assertEqual(len(source_catalog.CATALOG), 25)
        self.assertEqual(len({x[2] for x in source_catalog.CATALOG}), 25)
        self.assertEqual(sum(x[3] == 'rss' for x in source_catalog.CATALOG), 12)
        self.assertEqual(sum(x[3] == 'website' for x in source_catalog.CATALOG), 13)

    def test_migration_preserves_existing_sources(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.executescript('''
          CREATE TABLE sources(
            id TEXT PRIMARY KEY, name TEXT, url TEXT UNIQUE, enabled INTEGER,
            created_at TEXT, source_type TEXT NOT NULL DEFAULT 'rss',
            category TEXT NOT NULL DEFAULT 'other'
          );
        ''')
        url = 'https://github.blog/feed/'
        conn.execute('INSERT INTO sources(id,name,url,enabled,created_at) VALUES(?,?,?,?,?)',
                     ('mine','My GitHub',url,0,'now'))
        conn.execute('INSERT INTO sources(id,name,url,enabled,created_at) VALUES(?,?,?,?,?)',
                     ('custom','My Own','https://example.org/rss.xml',1,'now'))
        count = source_catalog.install_catalog(conn,lambda: str(uuid.uuid4()),lambda:'now')
        self.assertEqual(count,24)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0],26)
        self.assertEqual(conn.execute('SELECT enabled FROM sources WHERE id="mine"').fetchone()[0],0)
        self.assertEqual(conn.execute('SELECT category FROM sources WHERE id="mine"').fetchone()[0],'official')
        self.assertEqual(conn.execute('SELECT enabled FROM sources WHERE id="custom"').fetchone()[0],1)
        self.assertEqual(source_catalog.install_catalog(conn,lambda:'new',lambda:'later'),0)

    def test_html_sources_are_disabled(self):
        with server.db() as c:
            rows=c.execute("SELECT source_type,enabled FROM sources WHERE url IN (?,?)",
                ('https://cafef.vn/cong-nghe.html','https://vnexpress.net/khoa-hoc-cong-nghe')).fetchall()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['source_type']=='website' and not r['enabled'] for r in rows))

if __name__=='__main__':unittest.main()
