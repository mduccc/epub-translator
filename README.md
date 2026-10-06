# epub-translator

Dịch sách EPUB từ tiếng Anh sang tiếng Việt bằng LLM chạy trên máy (Ollama hoặc LM Studio), giữ nguyên định dạng sách. Sau khi dịch, chính model chấm lại bản dịch và viết lại những đoạn kém.

## Cài đặt

```bash
./setup_mac.sh
```

Script cài `lxml` vào `.venv`, cài Ollama và tải model mặc định `gemma4:12b-it-qat` (khoảng 7GB). Sau bước này không cần mạng. Yêu cầu Python 3.9+; trên macOS luôn gõ `python3`.

## Dịch

```bash
caffeinate -i python3 epub_translate.py run ~/Desktop/sach.epub --open
```

Thay `~/Desktop/sach.epub` bằng đường dẫn tới file sách của bạn; các lệnh bên dưới viết tắt là `sach.epub`, và các file sinh ra mang tên theo sách (`sach.vi.epub`…).

`run` kiểm tra model, tạo bảng nhân vật, dịch, chấm và tự sửa, rồi đóng gói. Lần đầu, lệnh dừng lại để bạn sửa bảng nhân vật. Có thể dừng bất cứ lúc nào bằng Ctrl+C; chạy lại đúng lệnh để làm tiếp.

```bash
# Thử trên 40 đoạn trước khi dịch cả cuốn
python3 epub_translate.py run sach.epub --max-segments 40 --open

# Chạy qua đêm, không dừng chờ sửa bảng nhân vật
caffeinate -i python3 epub_translate.py run sach.epub --no-pause
```

Kết quả nằm cạnh file sách: `sach.vi.epub` (bản dịch), `sach.glossary.md` (bảng nhân vật), `sach.vi-review.html` (báo cáo chấm) và `sach.vi-cache.jsonl` (bản dịch từng đoạn, cần giữ lại nếu còn muốn chạy tiếp hoặc sửa).

Đổi model: `--model qwen3:14b`. Dùng LM Studio: `--backend openai --model "<tên trong LM Studio>"`.

## Bảng nhân vật

Model chỉ thấy vài trang mỗi lượt nên không biết ai là ai, trong khi tiếng Việt phải chọn đại từ theo quan hệ ("he" có thể là anh, chàng, hắn, lão…). Bảng nhân vật được gửi kèm mọi lượt dịch:

```markdown
## Nhân vật
- **Elena Ward** — nữ, 24 tuổi, người kể chuyện. Lời kể xưng "tôi".
- **Captain Reyes** — nam, ngoài 50, cộc cằn. Lời kể gọi "lão Reyes".

## Xưng hô trong lời thoại
- Elena ↔ Reyes: Elena xưng "cháu", gọi "bác"; Reyes xưng "ta", gọi "cô".

## Thuật ngữ
- Greyhaven → giữ nguyên (tên đảo)
```

Nên có một dòng xưng hô cho mỗi cặp nhân vật hay nói chuyện, và giữ bảng dưới khoảng 2.000 ký tự. Mẫu đầy đủ: [`glossary.example.md`](glossary.example.md).

## Sửa bản dịch

Báo cáo `sach.vi-review.html` chấm theo 6 tiêu chí và liệt kê từng chỗ nên sửa. Model nhỏ tự chấm không hoàn toàn đáng tin, nên dùng báo cáo để biết cần soát chỗ nào hơn là tin vào con số.

Để model tự viết lại các đoạn bị chê rồi đóng gói lại:

```bash
python3 epub_translate.py review sach.epub --glossary sach.glossary.md --fix --max-fixes 50
python3 epub_translate.py build sach.epub
```

`--fix` chỉ thay một đoạn khi bản mới thắng bản cũ ở cả hai lượt so sánh, và chỉ ghi vào cache; phải chạy `build` thì EPUB mới thay đổi. Mỗi đoạn tốn ba lượt gọi model, nên `--max-fixes` giúp sửa dần qua nhiều lần chạy; đoạn đã thử sẽ không thử lại. Hoàn tác: `review sach.epub --undo-fixes` rồi `build`.

Sửa tay: mở `sach.vi-cache.jsonl`, tìm câu tiếng Anh, sửa trường `"vi"` (giữ nguyên các thẻ như `<g1>…</g1>`, `<x2/>`), rồi `build`.

Xưng hô sai từ một chương nào đó: sửa bảng nhân vật, rồi dịch lại chương đó bằng `translate sach.epub --glossary sach.glossary.md --redo chapter07` (tên chương xem bằng `info`).

## Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| `Không kết nối được tới server model` | `open -a Ollama` |
| `HTTP 404 … not found` | Sai tên model; xem `ollama list` |
| Mỗi lần chỉ dịch một ít rồi dừng | Bỏ `--max-segments` |
| Rất chậm, Memory Pressure đỏ | Đóng các app nặng hoặc dùng model nhỏ hơn |
| Chữ có dấu hiện ô vuông | `build sach.epub --drop-fonts` |

Các lỗi khác, mọi tuỳ chọn và cách hoạt động: [`docs/chi-tiet.md`](docs/chi-tiet.md). Kiểm thử (không cần model): `python3 tests/run_tests.py`.
