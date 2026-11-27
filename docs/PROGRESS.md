# Tiến độ

## Giai đoạn 1 — xong
- Bảng `sources`, cột `news.url_norm/source_id/status`, index UNIQUE chống trùng (migration tự động cho DB cũ).
- `rss.py`: chuẩn hóa URL, parse RSS/Atom, chặn SSRF cơ bản, lưu tin, ghi trạng thái nguồn.
- API nguồn + thu thập thủ công; News Center hiển thị nguồn, trạng thái, thời điểm, tin.
- Test: `tests/test_rss.py` (chuẩn hóa URL, chống trùng, lỗi nguồn, API).

## Giai đoạn 2 — xong (RSS tự động)
- `scheduler.py`: Scheduler thread (tắt mặc định, `AICC_SCHEDULER=1`), lease SQLite chống chạy trùng, bảng `collect_runs`, `GET /api/scheduler`, thẻ lịch quét trong News Center.
- `rss.fetch_feed`: SSRF an toàn theo từng redirect, ghim IP chống DNS rebinding, giới hạn timeout/dung lượng/redirect.
- Test: `tests/test_scheduler.py` (SSRF/redirect/rebinding, lease, scheduler, lỗi nguồn) — 29 test pass.
- **Chưa kiểm chứng:** tải RSS thật qua Internet và nhánh HTTPS/TLS (môi trường phát triển không có mạng ra ngoài; test dùng HTTP server cục bộ); nhiều worker thật chạy song song.

## Rà soát bảo mật / chạy local (nhánh security/giai-doan-2-5)
- Đã kiểm tra: đăng nhập, cookie HttpOnly+SameSite=Strict, CSRF, Host/Origin, khóa sau 5 lần sai (smoke test trên server thật + 34 test pass).
- Sửa test: `test_auth` đăng nhập hai lần làm lệch CSRF (lỗi test, không phải code); `test_server` chưa đăng nhập.
- Thêm `start-local.bat`. RSS thật qua Internet: chưa kiểm chứng (sandbox không có mạng).
- Chuẩn bị AI Agent: chưa thêm mã. Gemini/Codex CLI đã có trong `server.py`; Claude chỉ qua API key hợp lệ (trả phí) — chưa bật.

## Giai đoạn 3A — xong phần mã (45 test pass)
- `scoring.py`, `verify.py`, `POST /api/news/{id}/research`, nút trong News Center, chống trùng job theo tin, một pipeline tại một thời điểm, xác minh nguồn bằng fetch.
- **Chưa kiểm chứng:** chạy với `gemini`/`codex` CLI thật (sandbox không cài, không có mạng) — test dùng `run_cli` giả; chưa thử trên MacBook; tính hợp lệ điều khoản dùng CLI headless cần bạn tự xác nhận.

## Chưa làm
- Chống trùng theo nội dung/tiêu đề, đa site (`site_id`), đăng nhập.
- Giai đoạn 3: Master/Scout/Researcher/Writer/Checker, usage. Giai đoạn 4: mở rộng đa website.
- Chưa chuyển sang Next.js; chưa hỗ trợ nhiều instance dùng DB tách biệt.

## Giai đoạn 2.5 — đề xuất bảo mật (chờ PR review)
- Thêm `auth.py`: tài khoản quản trị một người, scrypt hash, cookie phiên HttpOnly/SameSite, CSRF, giới hạn đăng nhập sai, allowlist Host và Origin.
- Bảo vệ dashboard và API; thêm giao diện đăng nhập/đăng xuất; giữ bind localhost.
- Kiểm thử regression cần chạy trong môi trường clone nhánh PR, đặc biệt khi frontend có session và request CSRF.
- Chưa triển khai public; chưa có quản trị nhiều tài khoản hoặc khôi phục mật khẩu.
