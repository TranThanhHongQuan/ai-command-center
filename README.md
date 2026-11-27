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

- This is a **single-user local** prototype bound to `127.0.0.1`. It now includes single-admin login, session cookies, CSRF and Host/Origin checking. **Do not expose publicly without an HTTPS reverse proxy, explicit allowlists and additional deployment review.** Protect it with authentication/HTTPS before any VPS deployment.
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


## Giai đoạn 2.5 — đăng nhập quản trị

Mặc định ứng dụng vẫn chỉ bind `127.0.0.1`. Khởi tạo admin lần đầu trên PowerShell trước khi chạy server:

```powershell
$env:AICC_ADMIN_USER='admin'
$env:AICC_ADMIN_PASSWORD='đặt-mật-khẩu-riêng-tối-thiểu-12-ký-tự'
python -m uvicorn server:app --host 127.0.0.1 --port 8787
```

Sau khi khởi tạo, xóa `AICC_ADMIN_PASSWORD` khỏi môi trường chạy hoặc cấu hình dịch vụ. Mật khẩu lưu dưới dạng scrypt hash trong SQLite, không được lưu trong Git. Vào `http://127.0.0.1:8787/login` và đăng nhập. Nếu database đã có admin, biến này không thay đổi mật khẩu (chưa có màn hình đổi mật khẩu); muốn đặt lại phải thực hiện quy trình quản trị có kiểm soát.

- `AICC_ALLOWED_HOSTS`: danh sách hostname chấp nhận, cách nhau bằng dấu phẩy, mặc định `localhost,127.0.0.1,testserver` (testserver chỉ để tương thích FastAPI TestClient). Khi triển khai thật, cấu hình danh sách đúng hostname và cân nhắc loại testserver.
- `AICC_ALLOWED_ORIGINS`: danh sách nguồn Origin được phép cho các POST/PUT/PATCH/DELETE; mặc định chỉ chấp nhận same-origin. Định cấu hình qua HTTPS reverse proxy cẩn thận.
- `AICC_COOKIE_SECURE=1`: bắt buộc khi phục vụ qua HTTPS; cookie có HttpOnly + SameSite=Strict và hết hạn sau 12 giờ. Đăng nhập sai 5 lần trong 15 phút sẽ khóa tạm thời theo IP + tên tài khoản.
- Các API quản trị yêu cầu cookie phiên; request thay đổi dữ liệu cần header `X-CSRF-Token` từ `GET /api/auth/me`. `/api/health` vẫn mở cho health check.
- Backend chưa hỗ trợ public production đầy đủ (rate limit tại reverse proxy, kiểm toán cấu hình, giám sát, mật khẩu reset/rotate). Không tự triển khai công khai.
- Khi thêm/bật RSS, URL vẫn được lọc SSRF như trước. Scheduler tiếp tục chạy độc lập với session trình duyệt.

## Chạy local trên Windows

Chạy `start-local.bat` (tạo `.venv`, cài dependency, chạy tại http://127.0.0.1:8787). Lần đầu script hỏi mật khẩu admin (≥ 12 ký tự, nhập ẩn, không ghi ra file); tài khoản mặc định `admin` (đổi bằng `AICC_ADMIN_USER`). Quét RSS tự động: đặt `AICC_SCHEDULER=1` trước khi chạy (mặc định tắt). Script chưa được kiểm chứng trên Windows thật; các bước tương đương đã chạy trên Linux.

## Giai đoạn 3A — Nghiên cứu & viết bài từ tin RSS (chạy local)

- News Center sắp xếp tin theo điểm 0–100 (`scoring.py`, thuần Python: độ mới 40 + từ khóa công nghệ 40 + độ phù hợp độc giả Việt Nam 20; lý do chấm hiển thị cùng tin). Nút **Nghiên cứu & Viết bài** (`POST /api/news/{id}/research`) chạy pipeline Gemini CLI → Codex CLI → Gemini CLI có sẵn, chỉ với một tin; không có chạy tự động theo lịch.
- Yêu cầu: cài `gemini` và `codex` vào PATH, đăng nhập bằng công cụ chính thức (`gemini`, `codex login`). Thiếu CLI → báo lỗi 409 rõ ràng, không tạo job. Hãy tự kiểm tra điều khoản của Google/OpenAI về việc dùng CLI không tương tác (`gemini -p`, `codex exec`) với gói của bạn.
- Nguồn do model đưa ra chỉ được tính là **đã xác minh** khi trang tải được (HTTP 200, qua fetch chống SSRF); nếu kèm `quote` thì kiểm tra trích dẫn có trong trang (`quote_found`). URL gốc của tin luôn được kiểm tra. Không nguồn nào xác minh được → job `failed`, không tạo bài. Chỉ nguồn đã xác minh được đưa cho Writer/Checker.
- Checker không đạt → job `needs_revision`; đạt → bài vào Content Studio (`review`) chờ duyệt. Không đăng WordPress.
- Chống trùng: mỗi tin tối đa một job chưa lỗi (unique index); tối đa một pipeline chạy tại một thời điểm. Job lỗi cho phép bấm "Thử lại" thủ công; không tự retry. Timeout 200s mỗi bước CLI.


## Danh mục 25 nguồn tin công nghệ (local)

- Danh mục nằm trong `source_catalog.py`, gồm 4 nhóm: 7 blog công nghệ chính thức, 7 trang công nghệ Việt Nam, 6 trang báo công nghệ Việt Nam, 5 kênh báo/tín hiệu quốc tế.
- **12 địa chỉ RSS/Atom được cung cấp** (7 blog chính thức và 5 nguồn quốc tế) được thêm vào mục quét; các địa chỉ chưa được thử trực tiếp trên máy người dùng có thể báo lỗi do feed thay đổi hoặc máy chủ từ chối.
- **13 website tham khảo** được hiển thị trong News Center với nhãn `tham khảo`, có nút mở trang, không được scheduler quét bằng trình phân tích RSS. Ví dụ `https://cafef.vn/cong-nghe.html` là trang HTML, không phải RSS.
- Khi nâng cấp từ bản cũ, nguồn mới được nhập **một lần** vào SQLite, không xóa tin cũ, không ghi đè trạng thái bật/tắt nguồn RSS đã có. Các URL HTML trong danh mục nếu trước đây được thêm như RSS sẽ tự chuyển sang mục tham khảo và tắt quét.
- Cơ chế nhập một lần tránh khôi phục nguồn mà người dùng xóa sau này. Nguồn do người dùng thêm thủ công vẫn giữ nguyên; chỉ thêm URL feed thực vào ô thêm nguồn RSS, không nhập URL danh mục HTML.
- Chỉ chạy local; nếu nguồn feed lỗi hãy tắt riêng nguồn đó, không tắt xác minh SSL hoặc bỏ bảo vệ SSRF.
- Kiểm tra: `python -m unittest discover -s tests -v`. Sau khi cập nhật nhánh, **khởi động lại backend** rồi mở News Center. Chưa kiểm thử live cả 12 địa chỉ trên MacBook.
