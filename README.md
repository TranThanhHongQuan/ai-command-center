# AI Command Center — Real Local MVP

This is a **working backend**, not an offline UI simulation. The dashboard reads live SQLite records. Jobs attempt actual Gemini CLI and OpenAI Codex CLI processes; absent executables or credentials cause a visible failure. No fabricated success or activity.

## Windows setup

Requires Python 3.11+, Node.js 20+ for CLI installation. Open PowerShell:

```powershell
npm install -g @google/gemini-cli @openai/codex
# Authenticate once using the official CLIs:
gemini
codex login
codex login status
cd real
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8787
```

Open **http://127.0.0.1:8787**. Alternatively double-click `start.bat` after installing the CLI tools and signing in.

**Important account and legal limits:** This app invokes the official CLI binaries without collecting web passwords or cookies. Whether a subscription permits this particular automated or commercial usage depends on each provider's current terms; check before deployment. The app does **not** integrate Claude personal-account authentication because no supported third-party sign-in authorization has been established. A Claude SDK/API integration would need separately authorized credentials. The Master in this version is a deterministic coordinator, **not Claude**.

## Real flows

- RSS: click **Thu thập RSS**; downloads and parses RSS/Atom sources, ignores duplicate URLs. A source can fail and is reported.
- Jobs: submit a request. Server creates a persisted SQLite job, calls Gemini CLI to research, Codex CLI to write, Gemini CLI to cross-check, and writes an article only if checker passes. Failure states are saved and visible.
- Content Studio: edit a real article, approve or reject it. Explicitly send *approved* article to WordPress in `draft` status only.
- No API keys are required to authenticate Gemini CLI/Codex CLI. A WordPress **Application Password** is required to publish drafts; this is a WordPress credential, not an AI API key.

## WordPress setup

Set environment variables on the backend host:

```powershell
$env:AICC_WP_URL='https://your-wordpress-domain.example'
$env:AICC_WP_USER='dedicated_editor_user'
$env:AICC_WP_APP_PASSWORD='your WordPress Application Password'
```

Start the server in **that same shell**. Never paste secrets into chat, commit them or put them in the web UI. Account needs minimal permission to create drafts. This MVP submits Markdown as safe escaped HTML paragraphs and uses excerpt for the meta summary; Yoast/Rank Math fields, tags and categories need an explicit plugin integration.

## Security and limitations

- This is a **single-user local** prototype bound to `127.0.0.1`. It has no login and **must NOT be reverse-proxied or exposed to the internet** as-is. Protect it with authentication/HTTPS before any VPS deployment.
- Worker is an in-process thread (one job per request). It is not a durable queue; interrupted running jobs are marked failed at startup. For production use a persistent worker queue, concurrency cap, retry/idempotency safeguards and user authentication.
- Model outputs can be wrong. The checker only compares provided research, not actual independent live verification of every claim. **Human review is mandatory**.
- Gemini CLI/headless and Codex CLI/noninteractive behavior may change; verify current official documentation. The app does not bypass permissions, send browser cookies or scrape logged-in sessions.
- No automatic publish. WordPress API requests only use `status: draft`.
- The SEO & Marketing playbooks are guidance; no assertion of having installed every external skill or of achieving SEO rankings.
- The system should not be left unattended or used for bulk posting without further safeguards.

## Test

```powershell
python -m pip install httpx
python -m unittest discover -s tests -v
```

## Giai đoạn 1 — Thu thập RSS & chống trùng

- Nguồn RSS lưu trong bảng `sources` (lần đầu tự nạp 6 nguồn mặc định). Thêm/bật/tắt/xóa trong **News Center** hoặc qua `GET/POST/PATCH/DELETE /api/sources`. URL nội bộ/riêng tư bị chặn.
- `POST /api/news/collect` (nút **Thu thập RSS**): tải từng nguồn, lỗi một nguồn không chặn nguồn khác; trạng thái, thời điểm và số tin mới ghi vào `sources`.
- Chống trùng: URL được chuẩn hóa (`rss.py: normalize_url` — bỏ `utm_*`/`fbclid`, `www.`, `#fragment`, `/` cuối, http→https, sắp xếp query) và lưu duy nhất ở `news.url_norm`.
- Chạy: `pip install -r requirements.txt && python -m uvicorn server:app --host 127.0.0.1 --port 8787`, mở http://127.0.0.1:8787 → News Center.
- Test: `pip install httpx && python -m unittest discover -s tests`.

## Giai đoạn 2 — RSS tự động

- **Scheduler** chạy trong tiến trình backend (thread, không dịch vụ ngoài), quét mọi nguồn đang bật khi chưa có lượt quét nào trong chu kỳ. Chỉ hoạt động khi backend đang chạy.
- Biến môi trường: `AICC_SCHEDULER=1` để bật (mặc định **tắt**; test không tự khởi động); `AICC_SCHEDULER_INTERVAL_MIN=60` (tối thiểu 5).
- Mỗi lượt quét (tự động hoặc thủ công) ghi vào bảng `collect_runs`: thời gian, trạng thái (`ok`/`partial`/`error`), tin mới, trùng, lỗi. Dashboard (News Center) hiển thị trạng thái scheduler, lần quét tới, lần gần nhất; API: `GET /api/scheduler`.
- **Không chạy trùng:** khóa (lease) trong SQLite bảo vệ cả quét thủ công lẫn tự động; thời điểm quét kế tiếp đọc từ DB. Nhờ vậy nhiều worker dùng chung một file DB không quét chồng nhau. **Chưa hỗ trợ nhiều instance dùng DB riêng/mạng** — khuyến nghị chạy một worker (`uvicorn` mặc định).
- **Chống SSRF khi tải feed:** kiểm tra URL ban đầu và từng redirect (tối đa 3); chỉ http(s), cổng 80/443, không có user:pass; từ chối nếu bất kỳ IP nào là loopback/riêng tư/link-local/metadata/CGNAT/multicast (kể cả IPv4-mapped IPv6); DNS chỉ phân giải một lần mỗi hop và kết nối thẳng tới IP đã kiểm tra (chống DNS rebinding; TLS vẫn xác minh theo tên miền). Timeout 10s/thao tác, 25s tổng, tối đa 2MB, không giải nén (`Accept-Encoding: identity`). Không đi qua proxy hệ thống.
- Test: `python -m unittest discover -s tests` (29 test, dùng HTTP server cục bộ và resolver giả, không cần Internet).
