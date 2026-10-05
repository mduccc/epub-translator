# epub-translator

Dịch nguyên một cuốn EPUB tiếng Anh sang tiếng Việt, **văn phong văn học**, bằng LLM **chạy offline trên máy** (Ollama hoặc LM Studio). Giữ nguyên định dạng sách, rồi tự chấm bản dịch theo 6 tiêu chí và sửa những đoạn kém.

Viết cho MacBook Air M4 24GB, chạy được trên mọi máy có Python 3.9+.

## Cài đặt

```bash
cd epub-translator
./setup_mac.sh
```

Script cài `lxml` vào `.venv`, cài Ollama nếu chưa có, tải model `gemma4:12b-it-qat` (khoảng 7GB) và dịch thử một câu. Chỉ cần mạng ở bước này; sau đó mọi thứ chạy offline.

Luôn gõ `python3` (macOS không có lệnh `python`). Không cần kích hoạt `.venv`, script tự dùng nó.

## Dịch một cuốn sách

```bash
caffeinate -i python3 epub_translate.py run ~/Desktop/sach.epub --open
```

`run` làm lần lượt:

1. **Kiểm tra model**: gọi thử, đo tốc độ, ước tính thời gian.
2. **Bảng nhân vật**: lần đầu tạo bản nháp `sach.glossary.md`, mở ra và **dừng chờ bạn sửa** (xem mục dưới).
3. **Dịch**.
4. **Chấm + tự sửa**: model chấm bản dịch, viết lại đoạn bị chê, chỉ thay khi bản mới tốt hơn.
5. **Đóng gói** thành `sach.vi.epub`.

**Dừng lúc nào cũng được** (Ctrl+C, đóng Terminal, hết pin). Chạy lại đúng lệnh là làm tiếp từ chỗ dừng. Khi xong, model được gỡ khỏi RAM.

```bash
# Chạy thử cả quy trình trên 40 đoạn trước khi dịch cả cuốn
python3 epub_translate.py run sach.epub --max-segments 40 --open

# Chạy qua đêm, không dừng chờ sửa bảng nhân vật
caffeinate -i python3 epub_translate.py run sach.epub --no-pause
```

Mỗi lượt chạy tạo ra, cạnh file sách:

| File | Nội dung |
|---|---|
| `sach.vi.epub` | Bản dịch |
| `sach.glossary.md` | Bảng nhân vật (bạn sửa) |
| `sach.vi-review.html` | Báo cáo chấm |
| `sach.vi-cache.jsonl` | Bản dịch từng đoạn — **đừng xoá** nếu còn muốn chạy tiếp hoặc sửa |

## Bảng nhân vật: phần đáng công nhất

Model chỉ thấy vài trang mỗi lần, nên không tự nhớ ai là ai. Tiếng Việt lại phải chọn đại từ theo quan hệ ("he said" có thể là anh, chàng, hắn, lão…). Bảng nhân vật được gửi kèm mọi lượt dịch để giữ nhất quán.

```markdown
## Nhân vật
- **Elena Ward** — nữ, 24 tuổi, người kể chuyện. Lời kể xưng "tôi".
- **Captain Reyes** — nam, ngoài 50, cộc cằn. Lời kể gọi "lão Reyes".

## Xưng hô trong lời thoại
- Elena ↔ Reyes: Elena xưng "cháu", gọi "bác"; Reyes xưng "ta", gọi "cô".

## Thuật ngữ
- Greyhaven → giữ nguyên (tên đảo)
```

- Mỗi cặp nhân vật hay nói chuyện nên có một dòng xưng hô.
- Viết ngắn, dưới khoảng 2.000 ký tự.
- Quan hệ đổi giữa truyện thì sửa bảng, rồi dịch lại các chương đó: `translate sach.epub --glossary sach.glossary.md --redo chapter07` (tên file chương xem bằng `info`).

Mẫu đầy đủ: [`glossary.example.md`](glossary.example.md).

## Báo cáo chấm

Chính model đã dịch đóng vai biên tập viên, chấm từng trang theo 6 tiêu chí: **trung thành, tự nhiên, văn phong văn học, xưng hô, lời thoại, thuật ngữ**. Báo cáo HTML có điểm theo tiêu chí và theo chương, cùng danh sách từng chỗ cần sửa (cụm bị chê được tô vàng, kèm gợi ý).

Model tự chấm bài mình thường dễ dãi. Vì vậy trước khi chấm, nó phải chấm 4 đoạn đã biết đáp án; báo cáo cho biết nó bắt được bao nhiêu lỗi cài sẵn. Hãy dùng báo cáo để biết **nên soát chỗ nào**, đừng tin tuyệt đối vào con số.

Không ưng các đoạn được tự sửa: `review sach.epub --undo-fixes` rồi `build sach.epub`.

## Chọn model

| Model | File | Ghi chú |
|---|---|---|
| `gemma4:12b-it-qat` | 7,2GB | Mặc định, khuyên dùng |
| `qwen3:14b` | ~9GB | Mạnh; đôi khi lẫn chữ Trung (script tự bắt và dịch lại) |
| Model 26B trở lên | 16GB+ | Không vừa RAM máy 24GB |

Đổi model: `--model qwen3:14b`, hoặc đặt mặc định bằng `export EPUBTR_MODEL=qwen3:14b`. Dùng LM Studio: thêm `--backend openai --model "<tên trong LM Studio>"`.

## Chạy từng bước

`run` chỉ là gộp các lệnh này; vẫn dùng riêng được khi cần kiểm soát từng khâu:

| Lệnh | Làm gì |
|---|---|
| `info sach.epub` | Xem cấu trúc sách, số đoạn, ước tính thời gian |
| `check [sach.epub]` | Kiểm tra model, tốc độ, ước tính thời gian |
| `characters sach.epub -o sach.glossary.md` | Tạo bản nháp bảng nhân vật |
| `translate sach.epub --glossary …` | Dịch |
| `review sach.epub --glossary … [--fix]` | Chấm, xuất báo cáo; `--fix` để tự sửa |
| `build sach.epub [--bilingual]` | Đóng gói lại từ cache, không gọi model (sau khi sửa tay, hoặc để xuất song ngữ) |
| `prompt [--judge]` | In prompt sẽ gửi cho model |

Chi tiết mọi tuỳ chọn: `python3 epub_translate.py <lệnh> --help`.

**Sửa tay bản dịch**: mở `sach.vi-cache.jsonl`, tìm câu tiếng Anh, sửa trường `"vi"` (giữ nguyên các thẻ `<g1>…</g1>`, `<x2/>`), rồi `build sach.epub`.

## Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Mỗi lần chỉ dịch được một ít rồi dừng | Đang dùng `--max-segments` (chế độ thử). Bỏ tuỳ chọn này |
| `Không kết nối được tới server model` | Mở app Ollama: `open -a Ollama` |
| `HTTP 404 … not found` | Sai tên model: xem `ollama list`, hoặc `ollama pull <tên>` |
| Rất chậm, Activity Monitor báo Memory Pressure đỏ | Đóng Chrome và app nặng, hoặc dùng model nhỏ hơn |
| Báo model vẫn suy nghĩ (thinking) | Cập nhật Ollama: `brew upgrade --cask ollama` |
| `Câu trả lời bị cắt vì chạm giới hạn` | Thêm `--max-tokens 6000 --num-ctx 16384` |
| Chữ có dấu hiện ô vuông | `build sach.epub --drop-fonts` |
| `Sách có DRM` | Chỉ dịch được EPUB không DRM |

Mẹo cho MacBook Air: cắm sạc, mở nắp, chạy kèm `caffeinate -i`; máy không quạt nên sẽ chậm đi 20–30% khi nóng, đó là bình thường.

## Thêm

- **Cách hoạt động chi tiết** (nguyên lý, pipeline từng bước, cách chấm, mọi tuỳ chọn): [`docs/chi-tiet.md`](docs/chi-tiet.md)
- **Kiểm thử** (không cần model, không cần mạng): `python3 tests/run_tests.py`
- **Giới hạn**: không dịch chữ nằm trong ảnh hay trong sơ đồ SVG; chất lượng văn phụ thuộc model, vẫn nên có người soát xưng hô và chơi chữ.
