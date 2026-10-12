# Arona 3.0 — Phạm vi repository công khai

[93hai/arona](https://github.com/93hai/arona) đã công bố **3.0.0a1 — Experimental Core** theo MIT. **PRODUCTION: NOT READY**; xem [RELEASE_STATUS.md](RELEASE_STATUS.md).

| Đường dẫn | Nội dung |
| --- | --- |
| `README.md`, `README.vi.md` | Giới thiệu ngắn và hướng dẫn chi tiết bằng tiếng Việt |
| `src/arona/` | Settings, Core/Models, Memory, Policy/Dispatcher, Audit, Candidate và maintenance CLI |
| `src/arona/prompts/` | Runtime prompt contracts; không phải tài liệu giới thiệu để dịch hoặc sửa tùy ý |
| `migrations/` | Lịch sử SQL và tài liệu migration; SQL được giữ nguyên |
| `tests/` | Regression tests, gồm các assertion bảo mật |
| `scripts/` | Secret scanner, package smoke, benchmark/harness offline |
| `deploy/systemd/` | Unit Linux, cần operator cấu hình và kiểm chứng riêng |
| `docs/` | Kiến trúc, bảo mật, vận hành và phạm vi public source |
| `.env.example` | Template cấu hình, key trống; đường dẫn mẫu Linux cần điều chỉnh cho Windows |
| `old version/` | Legacy reference cho AST regression kiểm tra default credential rỗng; không import/chạy/đóng gói làm runtime |
| `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SECURITY.md` | MIT chuẩn, notices upstream và hướng dẫn liên hệ/bảo mật |

Bản source đầu tiên có 106 tệp được review. Các commit tài liệu tiếp theo có thể thêm hướng dẫn mà không thêm capability runtime. Git history công khai bắt đầu mới từ snapshot sạch; không đưa history private gốc vào. Git metadata không thuộc wheel/sdist hoặc source archive tải từ GitHub.

Repository không chứa `.env` thật, database/Memory người dùng, log, backup, cache, build output, private evidence archives hoặc tài liệu kế hoạch nội bộ của các phiên bản sau. Runtime 3.0 không có chat UI, public API, WebSocket, Discord transport, voice, AIRI integration hoặc Safe Runner; không suy diễn từ legacy reference thành chức năng hiện có.

MIT/identity đã được owner phê duyệt. Không tìm thấy third-party conflict cụ thể trong review, nhưng không coi đó là chứng nhận pháp lý độc lập. Credential remediation lịch sử là owner-reported, chưa provider-confirmed; chi tiết mức bằng chứng ở [RELEASE_STATUS.md](RELEASE_STATUS.md).
