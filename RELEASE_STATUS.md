# Arona 3.0 — Trạng thái phát hành và kiểm chứng

**3.0.0a1 — Experimental Core** đã được công bố source tại [93hai/arona](https://github.com/93hai/arona). **PUBLIC SOURCE: PUBLISHED**. **PRODUCTION: NOT READY**.

Source công khai bắt đầu bằng một commit gốc mới từ snapshot đã review; không mang Git history private cũ. Việc công bố source không chứng nhận độ ổn định production, live provider hoặc trạng thái credential trên tài khoản provider.

## Trạng thái hiện tại

| Hạng mục | Trạng thái và phạm vi |
| --- | --- |
| Runtime baseline | COMPLETE CANDIDATE / PASS WITH KNOWN ISSUES |
| Version / Python | `3.0.0a1`; manifest yêu cầu Python `>=3.11`, cài sạch Windows gần đây dùng Python 3.14.6 |
| Public source | PUBLISHED; không phải bản chatbot hoàn chỉnh hoặc package PyPI đã phát hành |
| MIT / attribution | Owner đã phê duyệt MIT, copyright 2026 93hai; dependency giữ license riêng |
| Third-party review | Không thấy conflict cụ thể hoặc vendored assets; dựa trên source review và khai báo owner, không phải chứng nhận pháp lý độc lập |
| Private vulnerability reporting | Chưa bật tại thời điểm review docs; xem [SECURITY.md](SECURITY.md) |
| Production | NOT READY |

## Bằng chứng kiểm thử đã ghi nhận

Các số liệu dưới đây là lịch sử các lượt kiểm tra, không phải một full-suite PASS mới cho mỗi commit tài liệu.

| Lượt kiểm tra | Kết quả | Giới hạn |
| --- | --- | --- |
| Windows clean wheel / offline workflow ngày 2026-10-11 | PASS trên Python 3.14.6 | Import, CLI, SQLite, persistence, backup/restore và Core READY với dữ liệu/provider giả, có chặn mạng |
| Windows full regression trước khi snapshot có Git | **579 passed / 1 failed / exit 1** | Test Git-aware thất bại vì snapshot không có tracked Git tree |
| Git-aware regression chạy lại trong clean Git checkout | PASS riêng | Không đổi số liệu full Windows suite cũ |
| Focused security/package checks ngày 2026-10-11 | **92 passed / 0 failed / 0 skipped / exit 0** | Chỉ phạm vi focused; không full regression mới |
| Kiểm tra liên quan ở snapshot tài liệu cuối ngày 2026-10-12 | **6 passed / 0 failed / 0 skipped / exit 0** | Secret-scan và package-import tests; wheel/sdist build, cài sạch và smoke cũng PASS |
| Native Debian 12 baseline | **578 passed / 2 skipped** | Bằng chứng lịch sử; không nghiệm thu lại published snapshot trong lượt này |
| Historical build / SQLite persistence / lifecycle / systemd | PASS trong baseline offline | Systemd validation có network isolation bổ sung; không coi đó là mặc định của unit |
| Live provider / soak dài / hardware power-loss | NOT PROVEN | SIGKILL/restart idle không chứng minh durability lúc mất điện hoặc đang xử lý request |

Checklist để tự tái nghiệm thu môi trường Linux ở [docs/linux_validation_checklist.md](docs/linux_validation_checklist.md). Không dùng kết quả lịch sử để bỏ qua kiểm chứng môi trường mới.

## Ranh giới chức năng

Arona 3.0 có Core, Memory, Model Gateway, Policy, Dispatcher, Audit, SQLite migration và maintenance. `serve` khởi động Core khi provider được cấu hình hợp lệ; Core READY không tạo chat UI, Discord transport, public API hoặc WebSocket.

Chưa có voice, AIRI integration, Safe Runner, Web Search, Candidate execution, Self-Correction, Scheduler hoặc Plugin platform. Memory/preferences/ngân sách có persistence; conversation history ngắn hạn ở RAM. Windows hướng dẫn development/maintenance; production yêu cầu Linux/non-root và kiểm chứng riêng.

## Credential lịch sử và nguồn gốc

| Nhóm | Bằng chứng hiện có |
| --- | --- |
| Gemini / Mistral key cũ | **OWNER-REPORTED REMEDIATED**: owner báo đã xóa key; chưa **PROVIDER-CONFIRMED REVOKED** |
| Discord application cũ | **OWNER-REPORTED REMEDIATED**: owner báo application cũ đã xóa và thay thế; chưa **PROVIDER-CONFIRMED REVOKED** |

Mô tả ảnh chỉ phản ánh account/project/workspace đã chọn; ảnh Deleted User không độc lập chứng minh mọi token cũ đã bị thu hồi. Đây là rủi ro tài khoản lịch sử riêng với độ sạch của source công khai. Secret scan là heuristic. Không đưa credential, private history, báo cáo chứa dữ liệu riêng hoặc test evidence archives vào repository.

MIT không cấp lại license cho dependency hoặc chứng minh mọi nguồn gốc đã được pháp lý chứng nhận. Giữ notices upstream và disclosure AI-assisted development. Xem [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [public snapshot](docs/public_snapshot.md) và [security design](docs/SECURITY.md).
