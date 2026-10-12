# Arona 3.0 — Public content manifest

Phạm vi phát hành là Arona 3.0. Tài liệu lộ trình nội bộ của các phiên bản sau không được đưa vào staging; không phải năng lực hiện có. Trạng thái: [RELEASE_STATUS.md](RELEASE_STATUS.md).

- `src/arona/`: Settings, Interaction/Core/Models, Memory, Policy/Dispatcher,
  Audit, Candidate và maintenance CLI; runtime giữ nguyên baseline.
- `migrations/`: SQL/resources immutable và tài liệu migration.
- `tests/`: regression từ HEAD, giữ nguyên; không xóa security assertion.
- `scripts/`: scanner, smoke và offline benchmark/harness nguyên trạng.
- `deploy/systemd/`: unit triển khai, yêu cầu operator setup riêng.
- `docs/`: architecture/security/operations và phạm vi snapshot.
- `.env.example`: template rỗng, không credential thật.
- `old version/`: chỉ một legacy file để AST regression xác nhận default
  credential rỗng, không được import/execute hoặc đóng gói thành runtime.

Report phát triển nội bộ, benchmark output cũ, các legacy implementation khác,
Git metadata/history và mọi dữ liệu runtime/build/cache không thuộc candidate.
Không triển khai Sandbox, Web Search, API, Workflow, Voice hay chức năng 3.1+.
MIT LICENSE và third-party notices được chuẩn bị theo quyết định của owner. Candidate vẫn **BLOCKED** đến khi quyền cấp phép và credential cũ được xác nhận; không Production Ready. Nên thiết lập kênh báo lỗi riêng trước khi tiếp nhận thông tin nhạy cảm.

- `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SECURITY.md`: public attribution, upstream notices và hướng dẫn liên hệ.
- `.git` trong staging là metadata cục bộ mới, không thuộc archive phát hành và không mang history gốc.
