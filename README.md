# Arona 3.0 — Experimental Core

**3.0.0a1 — Experimental Core** là bản source thử nghiệm cho lần phát hành công khai đầu tiên của Arona 3.0. **PUBLIC SOURCE: READY FOR OWNER PUBLICATION APPROVAL**. **PRODUCTION: NOT READY**. Trạng thái này xác nhận khả năng đưa clean source ra review/phát hành, không chứng nhận một service production hoặc chatbot hoàn chỉnh. Việc công bố cần phê duyệt cuối của owner; xem [RELEASE_STATUS.md](RELEASE_STATUS.md).

## Nguồn gốc, tác giả và license

Credit dự án Arona gốc: **[93hai](https://github.com/93hai)**. Theo quyết định của owner, ý tưởng và định hướng dự án do con người tạo; phần triển khai được hỗ trợ đáng kể bởi AI. AI assistance không phải bằng chứng rằng mọi quyền bên thứ ba đã được xử lý.

Arona 3.0 là dự án độc lập với AIRI. Snapshot này không bao gồm code, asset hay adapter AIRI và không tuyên bố liên kết hoặc được AIRI bảo trợ. Những chức năng của các phiên bản tương lai không phải năng lực của bản 3.0.

Owner đã phê duyệt [MIT LICENSE](LICENSE) và copyright attribution `2026 93hai`. Rà soát source chưa phát hiện xung đột quyền bên thứ ba cụ thể; kết luận dựa trên evidence và khai báo của owner, không phải chứng nhận pháp lý độc lập. Dependency giữ license riêng; xem [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Về credential lịch sử, owner báo các Gemini/Mistral key cũ và Discord bot application cũ đã được xóa. Mức bằng chứng là **OWNER-REPORTED REMEDIATED**, không phải **PROVIDER-CONFIRMED REVOKED**. Mô tả ảnh chỉ phản ánh các account/project/workspace được chọn; chưa độc lập xác minh mọi key hoặc token cũ. Đây là rủi ro an ninh tài khoản lịch sử riêng với clean source mới. Không đưa Git history gốc hoặc báo cáo credential riêng vào public source; xem [SECURITY.md](SECURITY.md).

Runtime là **COMPLETE CANDIDATE / PASS WITH KNOWN ISSUES**, chưa Production Ready. Audit Windows ngày 2026-10-11 trước lượt chuẩn bị này: clean wheel install, offline maintenance, persistence, backup/restore và Core READY với cấu hình synthetic có chặn mạng đã PASS trên Python 3.14.6. Full suite khi snapshot chưa có Git: **579 passed, 1 failed, exit 1**; test thất bại cần tracked tree trong Git. Staging dùng Git cục bộ mới: focused security/package tests ngày 2026-10-11 đạt **92 passed, 0 failed, 0 skipped, exit 0**, gồm test Git-aware nguyên trạng. Không diễn giải targeted PASS thành một full-suite PASS mới.

Linux Debian 12 **578 passed, 2 skipped** là bằng chứng baseline lịch sử, không phải lượt kiểm thử mới của staging. Live provider, soak dài, hardware power-loss và các Python/OS khác chưa được kiểm chứng trong lượt này. Xem [trạng thái](RELEASE_STATUS.md), [phạm vi](docs/public_snapshot.md) và [liên hệ bảo mật](SECURITY.md).

## Trạng thái implementation hiện tại

- Cấu hình qua environment rõ ràng, các giới hạn độc lập và lỗi an toàn với secret.
- SQLite transaction, kiểm tra năng lực FTS5, migration có version và nguyên tử.
- Migration v1 → v4, backup đã kiểm chứng, actor/scope cho maintenance.
- Backup/restore tới database path mới, không ghi đè database đang hoạt động.
- Memory Engine, Policy/Tools/Audit/Candidate security và Core/Models/Lifecycle đã được triển khai trong phạm vi Arona 3.0.
- Model Gateway có fallback, cooldown, degraded mode và budget token/cost bền vững qua restart.

Tài liệu public tập trung vào [kiến trúc](docs/ARCHITECTURE.md),
[security design](docs/SECURITY.md) và [backup/restore](docs/backup_restore.md).
Report phát triển nội bộ không đi kèm snapshot.

Arona 3.0 không bao gồm Sandbox, Candidate execution, Self-Correction, Web Search, public API, WebSocket, Workflow, Voice, AIRI, Vision, Scheduler hoặc Plugin platform.

## Cài đặt và kiểm chứng

Cần Python 3.11+ và SQLite có FTS5. Tạo virtual environment cục bộ cho project. Các lệnh dưới đây dùng `python` của môi trường đó.

```text
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

Dependency và developer tool phải ở trong virtual environment, không cài vào system Python. SQL migration resource được đưa vào wheel khi build. `requirements-dev.lock` cũng chứa runtime dependency.

Lệnh regression chuẩn từ repository root là `python -m pytest -q`. Pytest khai
báo cả `src` và root trong `pythonpath` để import `arona`, `migrations` và các
script kiểm chứng nhất quán. Không dùng bản source sao chép trong `dist`.
Giữ thư mục tạm mặc định của pytest ngoài checkout; nếu đặt `--basetemp`, chọn
đường dẫn ngoài source và các import root. Đây là yêu cầu cách ly runtime của
Settings/Candidate, không phải tùy chọn vô hiệu hóa security guard.

Từ cwd khác, dùng cùng interpreter của virtual environment và đường dẫn tuyệt
đối: `python -m pytest -c <repository>/pyproject.toml <repository>/tests -q`.
Smoke wheel trong venv riêng bằng `python -I scripts/smoke_package.py --workspace
<temporary-directory-outside-source>`. Không đặt `PYTHONPATH` để che lỗi packaging.
Scanner mặc định và regression tracked-tree cần Git checkout có tracked files.
Repository phát hành chỉ dùng Git history mới từ allowlist source được review;
không mang commit/history gốc. Test phải chạy nguyên trạng, không bỏ assertion.
Archive source không có `.git`; để chạy gate này từ archive, tạo repository Git
cục bộ mới và stage source đã review trước. Khi clone repository đã phát hành,
tracked-tree gate dùng Git checkout bình thường.

## Cấu hình

### Windows development: cài đặt và dữ liệu ngoài source

Chạy PowerShell từ thư mục snapshot. Không cần kích hoạt venv hoặc quyền
Administrator. Lượt kiểm tra ngày 2026-10-11 dùng Python 3.14.6; manifest cho phép
Python 3.11+, nhưng lượt này không kiểm tra lại mọi phiên bản Python hoặc Linux.

```powershell
py -3 -m venv .venv
$AronaPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $AronaPython -m pip install --require-hashes -r requirements.lock
& $AronaPython -m pip install --no-deps .
& $AronaPython -m pip check
& $AronaPython -I -c "import arona; print(arona.__version__)"
$AronaCommand = Join-Path (Get-Location) '.venv\Scripts\arona.exe'
& $AronaCommand --help

$env:ARONA_ENV = 'development'
$env:ARONA_DATA_DIR = Join-Path $env:LOCALAPPDATA 'Arona3\runtime'
$env:ARONA_DB_PATH = Join-Path $env:ARONA_DATA_DIR 'memory\arona_memory.db'
$env:ARONA_BACKUP_DIR = Join-Path $env:ARONA_DATA_DIR 'backups'
$env:ARONA_CANDIDATE_DIR = Join-Path $env:ARONA_DATA_DIR 'candidate'
$env:ARONA_STATE_DIR = Join-Path $env:ARONA_DATA_DIR 'state'
$env:ARONA_LOG_DIR = Join-Path $env:ARONA_DATA_DIR 'logs'
$env:ARONA_RUNTIME_SOURCE_DIR = (Get-Location).Path
& $AronaCommand check-config
& $AronaCommand migrate --operator-id local-operator
& $AronaCommand backup --operator-id local-operator
```

Đây là cấu hình maintenance offline. Các giá trị trên chỉ tồn tại trong phiên
PowerShell hiện tại. Để restore, dùng tên backup được lệnh `backup` trả về và tên
database mới theo [backup/restore](docs/backup_restore.md). Restore không ghi đè
database đang dùng. Không copy nguyên `.env.example` có đường dẫn Linux để chạy
trên Windows. Dữ liệu runtime có thể tồn tại từ lần chạy trước; chọn thư mục mới
nếu muốn một workspace mới, không xóa dữ liệu cũ.

`& $AronaCommand serve` yêu cầu ít nhất một provider có key, model và cả hai giá
token như `.env.example` mô tả. Khi không có provider, exit 1 trước readiness là
hành vi dự kiến. Không thêm credential giả vào source. Lượt kiểm tra offline chỉ
xác minh Core READY bằng cấu hình giả và chặn mạng; chưa kiểm chứng live AI.
READY của Core không phải một chat UI, Discord bot hay API: Arona 3.0 chưa có
external transport. CLI hiện không hỗ trợ `--version`; xem version bằng lệnh
Python ở trên. Linux production vẫn cần non-root và kiểm chứng riêng.

Dùng `.env.example` làm tham chiếu. Không có việc tự động tải `.env`. Đặt mọi runtime path ngoài checkout và tách biệt với nhau. Windows development cần absolute path có drive. Production yêu cầu Linux, tài khoản non-root và secret chỉ từ environment.

```text
arona --env-file .env check-config
arona --env-file .env migrate --operator-id operator-name
arona --env-file .env backup --operator-id operator-name
arona --env-file .env restore --operator-id operator-name --backup-name arona_backup_<id>.db --restore-name restored.db
arona --env-file .env serve
```

Không có `--env-file` thì cấu hình lấy từ process environment. Lệnh maintenance không khởi động service; `serve` là lệnh service tường minh. `serve` chỉ khởi động khi có ít nhất một provider đã cấu hình; trạng thái READY chỉ có sau migration và dependency Core hoàn tất.

Trong development, explicit dotenv path không tồn tại vẫn có thể dùng
environment/defaults. `check-config` không chứng minh file đã được đọc; operator
phải kiểm tra file trước nếu muốn coi nó là bắt buộc. Production cấm dotenv.

## Tài liệu kiến trúc và vận hành

### Memory user controls

Qua `InteractionGateway` đã xác thực, gửi `/memory <action> <JSON object>`:
`view`, `export`, `update`, `delete`, `forget`, hoặc `disable_auto`.
`forget` xóa toàn bộ Memory trong workspace hiện tại của người dùng, kể cả bản
đã xóa mềm; không tác động workspace hay owner khác. Đây là lệnh từ người dùng;
model không được tự gọi các tool quản lý Memory này.

Ví dụ: `/memory view {"limit":20,"offset":0}` hoặc `/memory disable_auto {}`.
Các action vẫn chịu identity/workspace isolation, phân trang và confirmation
cho dữ liệu nhạy cảm; model không được tự cấp quyền.

### Tham chiếu

- [Trạng thái phát hành hiện tại](RELEASE_STATUS.md)
- [Kiến trúc và ranh giới phase](docs/ARCHITECTURE.md)
- [Lịch sử migration và recovery khi lỗi](migrations/README.md)
- [Backup/restore](docs/backup_restore.md)
- [Phạm vi và giới hạn public snapshot](docs/public_snapshot.md)
- [Checklist kiểm chứng Linux](docs/linux_validation_checklist.md)

DB connection chuẩn là `src/arona/memory/db.py`. `src/arona/database/connection.py` là import compatibility contract cũ. Hạ tầng migration nằm ở `src/arona/database/`; lịch sử SQL nằm ở root `migrations/`.

## Giới hạn và hỗ trợ

Bản alpha thử nghiệm không có chat UI hoặc public API. Lệnh `serve` yêu cầu provider; không có provider sẽ từ chối readiness với exit 1. Lượt nghiệm thu offline không dùng live AI và không tiêu thụ API credit. Memory user controls mô tả ở trên là contract cho integration code, không phải kênh chat có sẵn. Conversation history chỉ ở RAM; memory/preferences/budget có persistence. Khi explicit development env file không tồn tại, loader có thể dùng environment/defaults. Không dùng bản này như một service production đã được chứng nhận.

Liên hệ công khai qua identity [93hai](https://github.com/93hai); hướng dẫn báo lỗi nhạy cảm ở [SECURITY.md](SECURITY.md). Không có private security channel được xác nhận tại thời điểm chuẩn bị staging.
