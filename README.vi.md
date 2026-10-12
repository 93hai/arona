# Arona 3.0 — Hướng dẫn tiếng Việt

[← Giới thiệu ngắn](README.md) · [Trạng thái và kiểm thử](RELEASE_STATUS.md) · [Bảo mật và liên hệ](SECURITY.md)

Mã nguồn của **3.0.0a1 — Experimental Core** đã được công bố tại [93hai/arona](https://github.com/93hai/arona). Đây là bản thử nghiệm: **PRODUCTION: NOT READY**.

## Arona là gì?

Hãy hình dung một trợ lý AI có thể nhớ thông tin bạn cho phép nhớ, hỏi mô hình để xử lý yêu cầu và kiểm tra quyền trước khi làm việc. Arona 3.0 xây phần lõi cho những việc đó. Phần lõi có thể được đọc, kiểm thử và tích hợp bằng code; bản này chưa có cửa sổ chat, website hay bot Discord để người dùng trò chuyện ngay.

## Ý tưởng và mục tiêu

Ý tưởng và hướng phát triển do **93hai** đưa ra. Mục tiêu là một trợ lý có bộ nhớ nhưng không tự cấp quyền, không tự chạy code hoặc tự triển khai thay đổi. AI hỗ trợ viết và sửa phần triển khai dưới sự hướng dẫn của con người.

Arona 3.0 là dự án độc lập với AIRI. Tên gọi hoặc kế hoạch cho các phiên bản sau không có nghĩa bản 3.0 đã có những chức năng đó.

## Tính năng hiện có

| Thành phần | Vai trò |
| --- | --- |
| Core | Điều phối yêu cầu, hội thoại ngắn hạn và khởi động/tắt ứng dụng |
| Memory | Lưu và tìm dữ liệu bằng SQLite/FTS5, tách theo `owner_id` và `workspace_id` |
| Model Gateway | Kết nối adapter mô hình, xử lý fallback, cooldown và ngân sách token/chi phí |
| Policy | Quyết định công cụ có được phép chạy trong ngữ cảnh hiện tại hay không |
| Dispatcher | Gọi công cụ đã đăng ký sau khi được Policy cho phép |
| Audit | Ghi nhận quyết định và kết quả, che thông tin bí mật khi ghi |
| Migration và maintenance | Kiểm tra cấu hình, chuẩn bị database, backup và restore |
| Candidate | Lưu tệp đề xuất trong thư mục riêng, chặn vượt đường dẫn và dữ liệu bí mật |

Mã kết nối Gemini, Mistral và Codestral đã có trong dự án. Điều đó không có nghĩa các dịch vụ AI thật đã được nghiệm thu. Candidate chỉ là vùng chứa tệp đề xuất; nó không chạy code, chạy test hoặc triển khai ứng dụng.

## Những tính năng chưa có

Arona 3.0 chưa có chat UI, public API, WebSocket, Discord runtime transport, voice, AIRI integration hoặc Safe Runner. Bản này không triển khai Web Search, Self-Correction, tự chạy Candidate, Scheduler hoặc Plugin platform.

`InteractionGateway` là điểm tích hợp dành cho code cung cấp danh tính tin cậy; không phải máy chủ mạng hay giao diện người dùng có sẵn. Các tệp trong `old version/` không được dùng để chạy Arona 3.0.

## Cài đặt trên Windows

Cần Git, Python 3.11+ và SQLite có FTS5. `pyproject.toml` khai báo Python `>=3.11`; lượt cài sạch Windows gần đây dùng Python **3.14.6**. Chưa kiểm chứng mọi tổ hợp phiên bản Python và hệ điều hành. Cài thư viện cần kết nối tới kho gói Python; các thao tác vận hành bên dưới không cần gọi dịch vụ AI.

Mở PowerShell tại nơi bạn muốn lưu dự án:

```powershell
git clone https://github.com/93hai/arona.git
cd arona
py -3 -m venv .venv
$AronaPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $AronaPython -m pip install --require-hashes -r requirements.lock
& $AronaPython -m pip install --no-deps .
& $AronaPython -m pip check
& $AronaPython -I -c "import arona; print(arona.__version__)"
$AronaCommand = Join-Path (Get-Location) '.venv\Scripts\arona.exe'
& $AronaCommand --help
```

Không cần kích hoạt venv hoặc dùng Administrator. `.venv` chứa môi trường Python riêng cho dự án; các lệnh dùng đúng Python của môi trường đó. `-I` giúp kiểm tra bản đã cài thay vì vô tình import source từ thư mục hiện tại. CLI không hỗ trợ `--version`, nên xem version bằng lệnh Python trên.

Không cài một gói PyPI chỉ vì có tên giống dự án: các bước này cài từ mã nguồn của repository. Chạy `--help` chỉ xem trợ giúp; nó không mở chat hay khởi động dịch vụ AI.

## Thử vận hành không cần API key

Giữ nguyên cửa sổ PowerShell vừa cài đặt. Tạo một workspace thử mới ngoài source bằng các biến môi trường sau; phần đuôi ngẫu nhiên tránh dùng nhầm dữ liệu cũ:

```powershell
$AronaTrial = Join-Path $env:LOCALAPPDATA ('Arona3\trial-' + [guid]::NewGuid().ToString('N'))
$env:ARONA_ENV = 'development'
$env:ARONA_DATA_DIR = $AronaTrial
$env:ARONA_DB_PATH = Join-Path $AronaTrial 'memory\arona_memory.db'
$env:ARONA_BACKUP_DIR = Join-Path $AronaTrial 'backups'
$env:ARONA_CANDIDATE_DIR = Join-Path $AronaTrial 'candidate'
$env:ARONA_STATE_DIR = Join-Path $AronaTrial 'state'
$env:ARONA_LOG_DIR = Join-Path $AronaTrial 'logs'
$env:ARONA_RUNTIME_SOURCE_DIR = (Get-Location).Path

& $AronaCommand check-config
& $AronaCommand migrate --operator-id local-operator
& $AronaCommand backup --operator-id local-operator
```

`check-config` kiểm tra cấu hình; nó không chứng minh Core đã READY. `migrate` tạo/kiểm tra cấu trúc database SQLite. `backup` tạo bản sao, kiểm tra tính toàn vẹn và ghi kèm receipt để xác nhận thao tác. Đây là các việc bảo trì dữ liệu, không gọi mô hình AI hoặc khởi động service.

Các biến chỉ có hiệu lực trong phiên PowerShell hiện tại. Đừng xóa workspace cũ để làm kiểm tra vượt qua; dùng workspace thử mới nếu cần làm lại. Không copy nguyên `.env.example` để chạy Windows vì template dùng đường dẫn Linux.

### Thử restore

Trong workspace thử mới này, lấy tên bản backup vừa tạo và restore sang một database mới:

```powershell
$AronaBackup = Get-ChildItem -LiteralPath $env:ARONA_BACKUP_DIR -Filter 'arona_backup_*.db' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
& $AronaCommand restore --operator-id local-operator --backup-name $AronaBackup.Name --restore-name restored.db
```

Restore không ghi đè database đang dùng. Nếu `restored.db` đã tồn tại, lệnh phải từ chối; không xóa dữ liệu chỉ để ép lệnh thành công. Receipt và bản backup đều phải được giữ nguyên. Chi tiết và giới hạn khi tiến trình bị ngắt ở [backup/restore](docs/backup_restore.md).

### Core và lệnh serve

`serve` là lệnh khởi động Core. Nó cần ít nhất một dịch vụ AI được cấu hình key, model và cả hai mức giá token như `.env.example` mô tả. Nếu chưa cấu hình dịch vụ AI, lệnh thoát với exit 1 trước khi báo sẵn sàng; không thêm key giả vào mã nguồn để vượt kiểm tra.

Các bước maintenance phía trên không chạy `serve`. Có thể xem trợ giúp an toàn bằng:

```powershell
& $AronaCommand serve --help
```

Trong các lượt kiểm thử đã ghi nhận, Core READY được kiểm tra bằng dịch vụ mô phỏng/cấu hình giả và chặn mạng. Đó không phải bằng chứng AI thật hoạt động. READY của Core cũng không tự tạo cửa sổ chat, bot Discord hoặc API. Mã khởi tạo nằm ở [src/arona/app.py](src/arona/app.py).

### Cấu hình cần lưu ý

Không có việc tự động tải `.env`. Development có thể dùng `arona --env-file .env check-config` khi chọn file tường minh; production chỉ lấy biến môi trường và cấm dotenv. Nếu file development được chỉ định không tồn tại, loader có thể dùng environment/defaults. Vì vậy `check-config` PASS không chứng minh file đó đã được đọc.

Mọi đường dẫn dữ liệu phải nằm ngoài checkout và tách biệt theo cấu hình. Windows development cần đường dẫn tuyệt đối có drive; production yêu cầu Linux/non-root và kiểm chứng riêng. Không đặt key, log, database hoặc backup vào repository.

## Sơ đồ kiến trúc

```mermaid
flowchart LR
    I["Mã tích hợp tin cậy"] --> C["Core"]
    C <--> M["Memory / SQLite"]
    C <--> G["Model Gateway"]
    C --> P["Policy"]
    P --> D["Dispatcher"]
    D --> A["Audit"]
```

Model chỉ đưa ra đề xuất. Danh tính được cung cấp bởi phần tích hợp tin cậy; Policy quyết định quyền và Dispatcher mới gọi công cụ. Memory được gọi trong quá trình xử lý; Audit ghi nhận các bước liên quan. Xem [kiến trúc](docs/ARCHITECTURE.md) để đọc luồng đầy đủ.

## Bộ nhớ và bảo vệ dữ liệu

Memory dùng SQLite với FTS5 và các migration có version. Dữ liệu được tách theo `owner_id` và `workspace_id`; không cho model tự nhận mình là người dùng khác hoặc tự cấp quyền. Admission Policy kiểm tra trước khi ghi, và key/token/password không được lưu làm Memory.

Memory, preferences và ngân sách token/chi phí có lưu bền vững qua khởi động lại. Conversation history ngắn hạn chỉ nằm trong RAM. Backup vẫn chứa dữ liệu lịch sử; xóa một bản ghi hiện tại không tự xóa các backup trước đó.

Các lệnh `/memory view`, `export`, `update`, `delete`, `forget` và `disable_auto` là quy ước cho mã tích hợp qua `InteractionGateway` đã xác thực, không phải CLI hoặc giao diện chat có sẵn. Ví dụ: `/memory view {"limit":20,"offset":0}` và `/memory disable_auto {}`. `forget` xóa Memory của đúng người dùng/workspace hiện tại, kể cả bản xóa mềm, không tác động workspace khác. Những hành động nhạy cảm vẫn chịu kiểm tra quyền và yêu cầu xác nhận; model không tự được quyền gọi chúng.

Candidate chỉ nhận đường dẫn tương đối trong workspace được phép; chặn path traversal, symlink/junction và secret. Nó không chạm Runtime/Production hoặc thực thi code. Các ranh giới này được mô tả ở [security design](docs/SECURITY.md).

## Kiểm thử và giới hạn

| Bằng chứng đã có | Cách hiểu đúng |
| --- | --- |
| Cài wheel sạch Windows, import/CLI, maintenance, persistence và backup/restore | PASS trên Python 3.14.6 trong lượt kiểm tra đã ghi nhận |
| Windows full suite trước khi snapshot có Git | **579 passed, 1 failed, exit 1**; lỗi là test tracked-tree cần Git |
| Test Git-aware chạy lại trong clean Git checkout | PASS riêng; không biến full suite cũ thành full-suite PASS mới |
| Focused security/package checks ngày 2026-10-11 | **92 passed, exit 0** |
| Kiểm tra liên quan packaging/secret ở snapshot tài liệu cuối | **6 passed, exit 0**; không phải full regression |
| Debian 12 baseline | **578 passed, 2 skipped** là bằng chứng lịch sử, không phải nghiệm thu mới của public snapshot |

Giữ lịch sử đầy đủ ở [RELEASE_STATUS.md](RELEASE_STATUS.md). Chưa nghiệm thu live provider, soak dài hoặc hardware power-loss; SIGKILL tiến trình idle không thay cho kiểm tra mất điện khi đang ghi. **PRODUCTION: NOT READY**.

Chủ dự án báo Gemini/Mistral key cũ và Discord application cũ đã được xóa. Đây là **owner-reported remediation**, không phải **provider-confirmed revocation**: người duy trì đã báo xử lý, nhưng chưa được nhà cung cấp xác nhận độc lập. Mã nguồn công khai không mang lịch sử Git private gốc; công cụ quét chỉ nhận diện các mẫu nghi ngờ, không chứng minh mọi tài khoản hoặc mọi loại dữ liệu bí mật đều an toàn. Xem [SECURITY.md](SECURITY.md).

## Đóng góp

Bạn có thể [mở issue](https://github.com/93hai/arona/issues) để hỏi cách dùng, báo lỗi không nhạy cảm hoặc đề xuất cải thiện tài liệu. Với lỗi, ghi rõ phiên bản Python/OS, lệnh thực hiện và kết quả đã che thông tin riêng. Đừng gửi credential, database, log đầy đủ hoặc nội dung Memory của bạn.

Nếu sửa code, dùng venv đã tạo và cài thêm các thư viện/công cụ phát triển:

```powershell
& $AronaPython -m pip install --require-hashes -r requirements-dev.lock
& $AronaPython -m pip install --no-deps -e .
& $AronaPython -m pytest -q
& $AronaPython -m ruff check .
& $AronaPython scripts\scan_secrets.py
```

`python -m pytest -q` là lệnh regression của dự án, không phải một full suite mới đã được chạy cho lượt sửa docs. Pytest cấu hình cả `src` và root trong `pythonpath`; nếu đặt `--basetemp`, chọn thư mục ngoài source và ngoài các import roots. Không bỏ/skip assertion bảo mật để lấy PASS.

Scanner tracked-tree cần Git checkout. Nếu dùng source archive không có `.git`, hãy dùng bản clone hoặc tạo Git repository cục bộ mới và stage source đã review; không đưa history private cũ vào. Muốn smoke wheel đã cài ở venv riêng, dùng `python -I scripts/smoke_package.py --workspace` kèm thư mục tạm ngoài source; không đặt `PYTHONPATH` để che lỗi packaging.

Mỗi PR nên có phạm vi rõ và cách kiểm tra. Giữ nguyên các ranh giới quyền; các runtime prompt trong `src/arona/prompts/` là giao ước được ứng dụng đọc, không phải bài giới thiệu để sửa tùy ý.

## Quyền sử dụng và ghi nhận

[MIT License](LICENSE) cho phép sử dụng, sửa đổi và phân phối theo điều kiện giữ copyright notice và permission notice; phần mềm được cung cấp “AS IS”. **LICENSE tiếng Anh trong repository là văn bản chuẩn**. Một bản dịch tự động của trình duyệt không thay thế giấy phép này. Dependency giữ license riêng ở [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Copyright **2026 93hai**. Credit dự án gốc, ý tưởng và định hướng: **[93hai](https://github.com/93hai)**. Việc lập trình được hỗ trợ đáng kể bởi AI dưới sự hướng dẫn của con người. Rà soát chưa tìm thấy xung đột quyền bên thứ ba cụ thể; đây không phải chứng nhận pháp lý độc lập cho mọi nguồn gốc hoặc AI output.

## Đọc tiếp

- [Kiến trúc](docs/ARCHITECTURE.md) và [security design](docs/SECURITY.md).
- [Migration SQLite](migrations/README.md) và [backup/restore](docs/backup_restore.md).
- [Checklist kiểm chứng Linux](docs/linux_validation_checklist.md).
- [Phạm vi public source](docs/public_snapshot.md) và [project manifest](PROJECT_MANIFEST.md).
