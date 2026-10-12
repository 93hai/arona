# Checklist kiểm chứng Linux

Đây là checklist để operator tái nghiệm thu môi trường triển khai. Baseline đã có
native Debian 12 validation theo [RELEASE_STATUS.md](../RELEASE_STATUS.md).
Các ô trống bên dưới dành cho lần triển khai của operator, không phủ nhận kết quả
baseline. Live provider, soak dài, crash giữa request và power-loss vẫn là gate
chưa hoàn tất; SIGKILL process idle không thay thế chúng.

- [ ] Chạy full pytest và Ruff với Python 3.11+ và SQLite FTS5.
- [ ] Tạo artifact mới từ source hiện tại rồi kiểm tra wheel/sdist; không dùng artifact cũ.
- [ ] Chạy `arona --env-file ... migrate` bằng non-root service user.
- [ ] Xác nhận configured data, database, backup, Candidate, state và log directory tách biệt, private và chỉ service user có quyền ghi.
- [ ] Cài `deploy/systemd/arona.service`; kiểm tra `User=arona`, `WorkingDirectory=/opt/arona`, external `EnvironmentFile`, `Restart=on-failure` và `KillSignal=SIGTERM`.
- [ ] Chạy `systemd-analyze verify` với installed unit, không đặt secret trong repository hoặc unit file.
- [ ] Xác nhận `UMask=0077` tạo file private và `CapabilityBoundingSet=` rỗng vẫn cho phép ghi các runtime directory đã cấu hình, DNS và HTTPS tới provider. Hai thiết lập này không giới hạn đường dẫn hay address family.
- [ ] Start/stop bằng SIGTERM; xác nhận audit flush, database close và shutdown idempotent.
- [ ] Kill process trong lúc có request; xác nhận startup kế tiếp không READY giả và database vẫn hợp lệ.
- [ ] Chạy offline phase4 harness và representative full-duration soak.
- [ ] Chạy opt-in live provider smoke test với externally managed credential.
- [ ] Với Gemini thinking, kiểm tra chất lượng hội thoại nhiều lượt trên đúng model triển khai. Arona 3.0 dùng text/JSON proposal; chưa hỗ trợ native `functionCall/functionResponse` hay lưu/replay `thoughtSignature`. Không bật capability yêu cầu state này trước khi có kiểm chứng và cơ chế replay theo scope.
- [ ] Thực hiện crash/power-loss validation theo operational procedure.

Kết quả native Linux, systemd, live provider, soak dài và crash/power-loss phải được ghi riêng khỏi Windows local validation report.
