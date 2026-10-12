# Legacy regression reference — không phải runtime

Chỉ giữ `discord_ai_assistant update 1.0.py` từ HEAD vì
`tests/unit/test_secret_scan.py::test_identified_legacy_default_is_empty` đọc AST
của file này để xác nhận credential default rỗng.

Không import, chạy hoặc dùng nó để triển khai Arona 3.0. Không mang lịch sử Git
hoặc các legacy implementation khác vào snapshot. Public runtime nằm ở `src/arona`.
