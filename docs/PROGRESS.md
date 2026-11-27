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

## Bước 2 — Editor Agent (xong, 65 test pass)
- `editor.py` + bảng `editor_items`/`editor_groups`; API `/api/editor*`; chạy sau mỗi lượt quét RSS; UI "Editor Agent — Tin đề xuất"; trọng số/ngưỡng cấu hình qua env.
- Đã thử UI bằng trình duyệt headless trên DB mẫu riêng (8 tin: gom 3 nguồn RTX 5090, tách RTX 5080, lọc tin ngoài ngành và tin cũ).
- **Giới hạn:** gom nhóm theo token tiêu đề (không embedding) nên có thể sót hoặc tách nhầm; chưa kiểm chứng trên dữ liệu RSS thật nhiều ngày; chưa có UI chỉnh trọng số.

## Chưa làm
- Chống trùng theo nội dung/tiêu đề, đa site (`site_id`), đăng nhập.
- Giai đoạn 3: Master/Scout/Researcher/Writer/Checker, usage. Giai đoạn 4: mở rộng đa website.
- Chưa chuyển sang Next.js; chưa hỗ trợ nhiều instance dùng DB tách biệt.

## Giai đoạn 2.5 — đề xuất bảo mật (chờ PR review)
- Thêm `auth.py`: tài khoản quản trị một người, scrypt hash, cookie phiên HttpOnly/SameSite, CSRF, giới hạn đăng nhập sai, allowlist Host và Origin.
- Bảo vệ dashboard và API; thêm giao diện đăng nhập/đăng xuất; giữ bind localhost.
- Kiểm thử regression cần chạy trong môi trường clone nhánh PR, đặc biệt khi frontend có session và request CSRF.
- Chưa triển khai public; chưa có quản trị nhiều tài khoản hoặc khôi phục mật khẩu.


## Danh mục nguồn tin (nhánh feature/tech-news-sources-25)
- Thêm 25 nguồn do người dùng cung cấp, phân loại 4 nhóm: 12 RSS ứng viên và 13 website tham khảo.
- `source_catalog.py` nhập một lần và tránh trùng URL, giữ nguyên dữ liệu cũ. `server.py` thêm `category` và `source_type` cho SQLite, `scheduler.py` chỉ lấy nguồn `rss` có `enabled=1`.
- News Center nhóm nguồn, phân biệt mục `tham khảo` không có nút Bật/Tắt RSS. Có test danh mục, migrate và tách website.
- Chưa chạy bộ test tích hợp và kiểm chứng đủ các nguồn feed thực tế trên MacBook; không được coi các URL HTML là RSS hoặc giả định nguồn RSS luôn hoạt động.
