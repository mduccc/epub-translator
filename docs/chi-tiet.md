# epub-translator — tài liệu chi tiết

Bản hướng dẫn ngắn nằm ở [README](../README.md). Tài liệu này giải thích kỹ nguyên lý, từng bước của pipeline, cách đọc báo cáo chấm và tham chiếu đầy đủ mọi lệnh, mọi tuỳ chọn.

Mọi lệnh đều có `--help`, ví dụ `python3 epub_translate.py run --help`.

---

## Mục lục

1. [Nguyên lý cơ bản](#1-nguyên-lý-cơ-bản)
2. [Pipeline chi tiết](#2-pipeline-chi-tiết)
3. [Cài đặt](#3-cài-đặt)
4. [Cách dùng](#4-cách-dùng)
5. [Đọc báo cáo đánh giá](#5-đọc-báo-cáo-đánh-giá)
6. [Viết bảng nhân vật hiệu quả](#6-viết-bảng-nhân-vật-hiệu-quả)
7. [Tuỳ chỉnh văn phong](#7-tuỳ-chỉnh-văn-phong)
8. [Các file sinh ra](#8-các-file-sinh-ra)
9. [Soát và sửa bản dịch](#9-soát-và-sửa-bản-dịch)
10. [Mẹo chạy trên MacBook Air](#10-mẹo-chạy-trên-macbook-air)
11. [Xử lý sự cố](#11-xử-lý-sự-cố)
12. [Kiểm thử](#12-kiểm-thử)
13. [Cấu trúc thư mục](#13-cấu-trúc-thư-mục)
14. [Giới hạn đã biết](#14-giới-hạn-đã-biết)

---

## 1. Nguyên lý cơ bản

### 1.1. EPUB thực chất là gì

Một file `.epub` là một file **zip** chứa:

```
mimetype                     ← luôn là dòng "application/epub+zip", phải đứng đầu, không nén
META-INF/container.xml       ← chỉ đường tới file OPF
OEBPS/content.opf            ← "mục lục máy": danh sách mọi file (manifest) + thứ tự đọc (spine) + metadata
OEBPS/chapter01.xhtml …      ← nội dung từng chương, là HTML viết theo chuẩn XML
OEBPS/nav.xhtml, toc.ncx     ← mục lục người đọc nhìn thấy (EPUB 3 / EPUB 2)
OEBPS/style.css, ảnh, font   ← trình bày
```

Vì thế "dịch EPUB" có nghĩa là: thay **chữ** bên trong các thẻ HTML, giữ nguyên **khung** (thẻ, thuộc tính, id, liên kết), rồi nén lại đúng chuẩn.

### 1.2. LLM chạy trên máy cần gì

**RAM quyết định chạy được model nào.** Toàn bộ trọng số model phải nằm trong bộ nhớ. Ở mức lượng tử hoá 4-bit, mỗi tỷ tham số tốn khoảng 0,55–0,6GB. Mac dùng chung RAM cho CPU và GPU, nhưng macOS mặc định chỉ cho GPU dùng khoảng 2/3 (~16GB trên máy 24GB). Hệ điều hành, các app đang mở và bộ nhớ ngữ cảnh (KV cache) còn chiếm thêm.

**Model MoE tiết kiệm tốc độ, không tiết kiệm RAM.** Ví dụ "26B-A4B" nghĩa là mỗi token chỉ *tính toán* với khoảng 4B tham số, nhưng vẫn phải *nạp* đủ 26B vào bộ nhớ.

**Băng thông bộ nhớ quyết định tốc độ.** Mỗi token sinh ra, máy phải đọc qua toàn bộ trọng số. Tốc độ tối đa xấp xỉ băng thông chia kích thước model. M4 Air có băng thông khoảng 120GB/s, nên model 4-bit 12B (~7GB) đạt tối đa khoảng 16 token/s, thực tế khoảng 10–14 token/s. **Đọc** (xử lý prompt) nhanh hơn **viết** (sinh token) nhiều lần, vì đọc được xử lý song song.

**Thinking và giới hạn token.** Một số model (Gemma 4, Qwen3…) có chế độ "suy nghĩ": viết ra một đoạn lập luận trước rồi mới trả lời. Phần suy nghĩ tốn token y như câu trả lời và **tính chung vào giới hạn token đầu ra** của lượt gọi, nên model có thể nghĩ hết giới hạn mà chưa kịp viết bản dịch, kết quả là câu trả lời trống. Với dịch văn, thinking làm chậm gấp nhiều lần mà không làm câu văn hay hơn, nên script **tắt thinking mặc định** và đặt giới hạn token dư dả (mục ⑦).

**QAT tốt hơn nén sau.** Thông thường model được huấn luyện ở độ chính xác cao rồi mới nén xuống 4-bit, và mất một ít chất lượng. Bản QAT (quantization-aware training) được huấn luyện sẵn để chạy ở 4-bit, nên giữ chất lượng tốt hơn với cùng dung lượng.

### 1.3. Chọn model cho Mac 24GB

| Model (4-bit) | Lệnh tải | File | Mac 24GB | Ghi chú |
|---|---|---|---|---|
| **Gemma 4 12B QAT** | `ollama pull gemma4:12b-it-qat` | 7,2GB | ✅ khuyên dùng | Ra mắt 6/2026, 140 ngôn ngữ, dư nhiều RAM |
| Qwen3 14B | `ollama pull qwen3:14b` | ~9GB | ✅ | Mạnh; đôi khi lẫn chữ Trung (script tự bắt) |
| Qwen3.5 9B | `ollama pull qwen3.5:9b` | 6,6GB | ✅ | Nhẹ, nhanh hơn |
| Gemma 4 26B-A4B (MoE) | | 16GB | ❌ tràn | Cần ~19GB khi mở ngữ cảnh. Dù chỉ kích hoạt ~4B, mọi chuyên gia vẫn phải nằm trong RAM |
| Qwen3.6 27B | | 17GB | ❌ tràn | Qwen3.6 chưa có bản nhỏ hơn |

Lời khuyên: dịch thử cùng 40 đoạn bằng hai model rồi **tự đọc** so sánh (lệnh ở mục 4.1). Với văn chương, cảm nhận của bạn đáng tin hơn benchmark. Nếu tên model không tải được, tra tên đúng trên ollama.com/library.

### 1.4. Vì sao dịch văn học bằng máy khó

- **Model không đọc được cả cuốn cùng lúc.** Mỗi lượt gọi chỉ chứa vài trang, nên model không nhớ chương 3 đã gọi nhân vật là gì. Giải pháp: **bảng nhân vật** gửi kèm *mọi* lượt, cộng với vài đoạn dịch ngay trước làm ngữ cảnh.
- **Đại từ tiếng Việt phụ thuộc quan hệ.** "He said" có thể là "anh nói", "chàng nói", "hắn nói", "lão nói", "ông nói"… Đây là chỗ máy sai nhiều nhất. Bảng xưng hô theo từng cặp nhân vật quan trọng hơn cả việc chọn model.
- **Model nhỏ hay "lười".** Chúng bỏ đoạn, gộp hai đoạn làm một, quên thẻ định dạng, hoặc chèn lời giải thích. Pipeline bên dưới được thiết kế để phát hiện và sửa từng lỗi này.

### 1.5. Điều khiển văn phong bằng gì

Với LLM, văn phong chỉ điều khiển được qua ba "cần gạt":

1. **Chỉ dẫn (system prompt):** nói cho model biết phải viết thế nào.
2. **Ví dụ mẫu (few-shot):** cho model xem vài cặp *dịch sát chữ (tránh)* và *dịch văn học (nên)*. Với model 12–14B, ví dụ thường tác động mạnh hơn lời dặn.
3. **Nhiệt độ (temperature):** thấp thì sát chữ, an toàn. Cao thì bay bổng hơn nhưng dễ "sáng tác".

Code kiểm tra được những thứ đo đếm được (thẻ, số đoạn, độ dài, chữ Trung), nhưng **không chấm được "văn có hay không"**. Muốn chấm văn phải nhờ chính LLM đọc lại. Đó là việc của lệnh `review`.

### 1.6. LLM làm giám khảo: giỏi và dở ở đâu

Thiết kế của `review` dựa trên mấy đặc điểm đã biết của LLM khi chấm bài:

| Đặc điểm | Cách `review` xử lý |
|---|---|
| Model nhỏ chấm **điểm số** kém (đoạn nào cũng 7–8/10) nhưng nhận ra **lỗi cụ thể** khá tốt | Chỉ chấm 3 mức có định nghĩa rõ (tốt / tạm / kém) theo 6 tiêu chí, và bắt **liệt kê lỗi trước rồi mới chấm** |
| Model hay "khen bài mình" (self-preference bias), nhất là khi chính nó vừa dịch | Prompt yêu cầu khắt khe, nêu sẵn các lỗi hay gặp; **bộ kiểm tra giám khảo** đo xem model có bắt được lỗi cài sẵn không |
| Model có thể bịa nhận xét | Mỗi lỗi phải **trích nguyên văn** cụm từ; trích không khớp bản dịch thì báo cáo gắn cảnh báo |
| So sánh A với B đáng tin hơn chấm tuyệt đối, nhưng hay **thiên vị vị trí** (chuộng bản đứng trước) | Khi `--fix`, so sánh bản cũ và bản mới **hai lần, đổi thứ tự**; chỉ thay khi bản mới thắng cả hai |
| Câu trả lời JSON của model nhỏ hay hỏng | Ép khuôn bằng JSON schema (structured output) nếu server hỗ trợ; bộ đọc nới lỏng; hỏng thì chấm lại |
| Chấm lại khi không có gì đổi là lãng phí | Kết quả lưu theo mã băm của (bản dịch + model + bảng nhân vật); chỉ chấm lại đoạn trích có thay đổi |

---

## 2. Pipeline chi tiết

Pipeline có hai giai đoạn: **dịch** (lệnh `translate`, bước ①–⑪) và **thẩm định** (lệnh `review`, bước ⑫–⑮).

```
 GIAI ĐOẠN 1 — DỊCH (translate)
 sách.epub
    │
 ①  Giải nén, đọc container.xml → content.opf → manifest + spine (thứ tự đọc), kiểm tra DRM
 ②  Tách "đoạn lá": <p>, <h1>…<h6>, <li>, <td>, <figcaption>…
    │      chữ rời trong khối hỗn hợp được bọc <span>; <li><a>X</a></li> lấy thẳng <a>
 ③  Mã hoá định dạng thành thẻ giữ chỗ
    │      She had <i>never</i> seen<br/> it   →   She had <g1>never</g1> seen<x2/> it
 ④  Khử trùng (đoạn giống hệt chỉ dịch một lần), bỏ qua đoạn đã có trong cache
 ⑤  Gom lô: ~1800 ký tự / tối đa 10 đoạn mỗi lượt, không gộp khác chương, <pre> đi riêng
 ⑥  Dựng prompt:  [phong cách + ví dụ văn học] + [quy tắc định dạng] + [bảng nhân vật]
    │             + 2 đoạn dịch ngay trước + các đoạn cần dịch đánh số [1] [2] …
 ⑦  Gọi model; lỗi mạng thì thử lại
 ⑧  Kiểm tra từng đoạn: đủ số? thẻ khớp? lẫn chữ Trung? độ dài hợp lý? có dịch thật không?
    │      lỗi → dịch lại riêng; vẫn hỏng → giữ tiếng Anh, ghi báo cáo
 ⑨  Ghi ngay vào cache .jsonl  →  Ctrl+C lúc nào cũng được
 ⑩  Ráp bản dịch vào cây HTML, lang="vi", đổi mã định danh, đóng gói đúng chuẩn EPUB
 ⑪  sách.vi.epub  +  sách.vi.report.txt

 GIAI ĐOẠN 2 — THẨM ĐỊNH (review), giám khảo là chính model đã dịch
 ⑫  Kiểm tra giám khảo: chấm 4 đoạn đã biết đáp án (1 tốt, 3 cài lỗi) → mức đáng tin
 ⑬  Chấm từng đoạn trích (~1 trang): EN + VI + bảng nhân vật + 6 tiêu chí
    │      model trả JSON: danh sách lỗi (đoạn, tiêu chí, trích nguyên văn, gợi ý) → điểm 6 tiêu chí
    │      lưu vào sách.vi-review.jsonl; trích không khớp nguyên văn → gắn cảnh báo
 ⑭  (tuỳ chọn --fix) Viết lại đoạn bị chê kèm góp ý → kiểm tra định dạng như ⑧
    │      → so sánh cũ/mới hai lần đổi thứ tự A/B → chỉ thay khi bản mới thắng
    │      → chấm lại các đoạn trích đã đổi
 ⑮  sách.vi-review.html: điểm tổng, độ tin cậy giám khảo, điểm theo tiêu chí và theo chương,
        danh sách chỗ cần xem lại, các đoạn đã viết lại
```

### ① Giải nén và đọc cấu trúc

Script đọc `META-INF/container.xml` để tìm `content.opf`. Từ OPF nó lấy danh sách các file chương theo **thứ tự đọc** (spine), file mục lục `nav.xhtml` (EPUB 3) và `toc.ncx` (EPUB 2), cùng các file CSS.

Nếu `META-INF/encryption.xml` cho thấy nội dung chương bị mã hoá, đó là **DRM**, và script dừng với thông báo rõ ràng. Font bị "làm rối" (obfuscation) thì không phải DRM, script vẫn chạy nhưng giữ nguyên mã định danh sách để font còn đọc được.

Các thực thể HTML như `&nbsp;`, `&mdash;` được đổi sang dạng số trước khi parse, để không rơi ký tự nào.

### ② Tách đoạn

Script đi theo thứ tự đọc và lấy các **khối lá**, tức là khối không chứa khối con: `<p>`, tiêu đề, `<li>`, `<td>`, `<blockquote>` không có `<p>` bên trong… Nội dung trong `<script>`, `<style>`, `<svg>`, `<math>`, `<code>` không được dịch.

- **Khối hỗn hợp** như `<div>Chữ rời <p>đoạn</p></div>`: phần chữ rời được bọc vào `<span>` để thành một đoạn riêng, không bị bỏ sót.
- **Vỏ bọc đơn**: `<li><a>Chương 1</a></li>` hay `<p><i>cả đoạn nghiêng</i></p>` sẽ lấy phần tử bên trong làm gốc. Nhờ vậy model không phải giữ thẻ, và tiêu đề trong mục lục trùng khoá cache với tiêu đề trong chương.
- Đoạn không có chữ cái (`* * *`, số trang) được bỏ qua.

### ③ Mã hoá định dạng

Model không được nhìn thấy HTML thật (dễ làm hỏng). Thay vào đó:

| Gốc | Gửi cho model |
|---|---|
| `<i>never</i>`, `<a href="#n1">…</a>` (có chữ bên trong) | `<g1>never</g1>` (thẻ cặp) |
| `<br/>`, `<img/>`, `<a id="p5"/>`, `<span epub:type="pagebreak"/>` (không chữ) | `<x2/>` (thẻ đơn) |

Mỗi số ứng với một phần tử gốc được lưu lại đầy đủ thuộc tính, nên khi ráp lại, `<g1>` trở về đúng `<i class="…">` ban đầu. Khoảng trắng được chuẩn hoá, riêng `<pre>` giữ nguyên từng dấu cách và xuống dòng. `&nbsp;` không bao giờ bị gộp.

### ④ Khử trùng và khoá cache

Mỗi đoạn có khoá là SHA-1 của chuỗi đã mã hoá. Đoạn giống hệt nhau (tiêu đề chương xuất hiện ở cả mục lục, `toc.ncx` lẫn đầu chương) chỉ dịch một lần, nên luôn nhất quán. Đoạn đã có trong cache được bỏ qua.

### ⑤ Gom lô

Các đoạn liên tiếp được gom thành một lượt gọi, tối đa `--batch-chars` ký tự (mặc định 1800) và `--batch-max` đoạn (mặc định 10). Lượt lớn thì nhanh hơn và mạch văn liền hơn. Lượt nhỏ thì model nhỏ ít lỗi hơn. Lô không bao giờ vượt qua ranh giới chương. `<pre>` và đoạn rất dài được gửi riêng.

### ⑥ Dựng prompt

System prompt gồm 3 lớp:

1. **Phong cách**: dịch giả văn học, trung thành nhưng không dịch từng chữ, thành ngữ chuyển tương đương, lời thoại hợp nhân vật, quy tắc đại từ, kèm **3 cặp ví dụ sát chữ (TRÁNH) / văn học (NÊN)**: một thành ngữ, một câu trừu tượng, một câu thoại. Toàn bộ lớp này thay được bằng `--prompt-file`.
2. **Quy tắc định dạng**: giữ số `[n]`, giữ thẻ, chỉ trả bản dịch, kèm một ví dụ. Lớp này cố định vì bộ phân tích câu trả lời phụ thuộc vào nó.
3. **Bảng nhân vật và thuật ngữ** từ `--glossary`. Các dòng chú thích `<!-- -->` và dòng mẫu chưa điền (`"…"`) bị lọc bỏ.

Tin nhắn người dùng gồm `--context` đoạn dịch ngay trước (mặc định 2, dạng `EN:` / `VI:`, ghi rõ "không dịch lại"), rồi đến các đoạn cần dịch đánh số `[1]`, `[2]`… Xem chính xác prompt bằng `python3 epub_translate.py prompt --glossary glossary.md`.

### ⑦ Gọi model

- **`--backend ollama`** (mặc định) dùng API `/api/chat` của Ollama. Script tự gửi `num_ctx` (cửa sổ ngữ cảnh), `num_predict` (giới hạn token đầu ra), `keep_alive: 30m` (giữ model trong RAM giữa các lượt) và `think: false`. Model không có chế độ thinking mà từ chối tham số `think` thì script tự bỏ tham số này.
- **`--backend openai`** dùng `/chat/completions`, chạy được với LM Studio, `mlx_lm.server`, `llama-server`. Thinking và cửa sổ ngữ cảnh khi đó do server quyết định (cài trong LM Studio).
- **Giới hạn token đầu ra** mỗi lượt = 1,5 × số ký tự bản gốc + 512, tối thiểu 1024 (lô 1800 ký tự → khoảng 3200 token). Bản dịch tiếng Việt thực tế chỉ tốn khoảng 0,3–0,5 token mỗi ký tự gốc, nên mức này dư nhiều lần. Đặt cao **không làm chậm** (model dừng khi viết xong), nó chỉ là trần an toàn. Giám khảo dùng 512 + 200 token mỗi đoạn văn trong đoạn trích. `--max-tokens` ghi đè mức tự tính.
- **Prompt + đầu ra phải vừa cửa sổ ngữ cảnh** (mặc định 8192). Nếu giới hạn đặt quá lớn so với ngữ cảnh, script kẹp lại và cảnh báo; khi đó tăng `--num-ctx`. Đổi `num_ctx` làm Ollama nạp lại model, nên giá trị này giữ cố định suốt một lần chạy.
- **Bật `--think`** (không khuyên dùng): script cộng thêm `--think-budget` (mặc định 4096) token cho phần suy nghĩ và mở ngữ cảnh lên 16384.
- **Chạm trần** (`done_reason = length`): đoạn cuối của câu trả lời có thể bị cắt giữa câu, nên bị bỏ và dịch lại riêng; script cảnh báo để bạn tăng `--max-tokens`.
- **Giải phóng RAM khi thoát.** Ollama là một server chạy nền, độc lập với script: thoát script không làm model rời khỏi RAM. Vì vậy, khi xong việc, khi gặp lỗi, khi bấm Ctrl+C, khi đóng cửa sổ Terminal (SIGHUP) hay bị `kill` (SIGTERM), script đều gửi `keep_alive: 0` để Ollama gỡ model ngay, trả lại khoảng 8–10GB RAM. Trong lúc chạy, mỗi lượt gọi đặt `keep_alive: 5m`, nên nếu script bị tắt đột ngột không kịp dọn (`kill -9`, sập nguồn) thì Ollama tự gỡ model sau 5 phút. `--keep-loaded` giữ model thêm 30 phút, tiện khi chạy `translate` rồi `review` liền nhau. Script chỉ gửi lệnh gỡ khi lượt chạy đó đã thực sự nạp model.
- Lỗi mạng hoặc HTTP 5xx: thử lại tối đa 6 lần, chờ 3s → 6s → 12s… (tối đa 60s). Lỗi cấu hình (404 sai tên model, 401…) dừng ngay kèm gợi ý.
- Kết nối tới `localhost` luôn bỏ qua proxy hệ thống.

### ⑧ Kiểm tra và sửa lỗi

Câu trả lời được làm sạch trước: bỏ `<think>…</think>`, lời mở đầu, khối ```` ``` ````. Sau đó tách theo số `[n]` và kiểm tra từng đoạn:

| Kiểm tra | Nếu sai |
|---|---|
| Thiếu số `[n]` (model bỏ hoặc gộp đoạn) | Dịch lại riêng đoạn đó **và hai đoạn kề bên**, vì đoạn bên cạnh có thể đã "nuốt" nội dung |
| Thẻ `<gN>`/`<xN/>` thiếu, thừa, lồng sai | Dịch lại riêng |
| Lẫn chữ Trung/Nhật/Hàn mà bản gốc không có | Dịch lại riêng |
| Độ dài bản dịch < 0,4 hoặc > 3,5 lần bản gốc | Dịch lại riêng |
| Giữ nguyên tiếng Anh (≥ 5 từ) | Dịch lại riêng |

Mỗi lần dịch lại, nhiệt độ được tăng nhẹ 0,15. Sau `--retries` lần (mặc định 2):

- Nếu có bản dịch tốt nhưng sai thẻ, script chấp nhận và **ráp kiểu nới lỏng**: giữ các thẻ còn đúng, khôi phục neo, ảnh và số chú thích bị mất. Đoạn được ghi vào báo cáo là "mất định dạng".
- Nếu không có bản nào dùng được, đoạn giữ nguyên tiếng Anh, ghi "CHƯA DỊCH ĐƯỢC", và **không** ghi cache, nên lần chạy sau sẽ thử lại.

### ⑨ Cache và chạy tiếp

Mỗi lô dịch xong được ghi nối tiếp vào `sách.vi-cache.jsonl` (mỗi dòng một đoạn). Bị ngắt giữa chừng thì tối đa mất lô đang dịch dở. Dòng cuối ghi dở do mất điện sẽ được bỏ qua khi đọc lại. Nếu một khoá xuất hiện nhiều lần, **dòng sau thắng**. Nhờ đó bạn có thể sửa tay bằng cách sửa dòng hoặc thêm dòng mới, và lịch sử các bản cũ vẫn còn trong file.

### ⑩ Ráp và đóng gói

- Bản dịch được dựng lại thành cây HTML và thay vào đúng chỗ. Thuộc tính, id và phần đuôi văn bản của khối được giữ nguyên.
- Chỉ những file thực sự có thay đổi mới được ghi lại. Ảnh, CSS, font được giữ nguyên từng byte.
- `lang="vi"` được đặt cho các chương, `dc:language` thành `vi`, `toc.ncx` được dịch.
- **Mã định danh sách được đổi** (UUID mới suy ra từ mã cũ), đồng bộ cả `dtb:uid` trong `toc.ncx`. Việc này để Apple Books và các thư viện sách không coi bản dịch là cùng một cuốn với bản gốc. Dùng `--keep-id` để giữ nguyên.
- `--drop-fonts` bỏ các khai báo `@font-face`, vì font nhúng thường thiếu glyph tiếng Việt và hiện ô vuông.
- Nén lại với `mimetype` đứng đầu và không nén, đúng chuẩn EPUB. File được ghi ra `.part` trước rồi mới đổi tên, nên không bao giờ để lại EPUB hỏng.

### ⑪ Báo cáo dịch

`sách.vi.report.txt` liệt kê các đoạn "mất định dạng" và "chưa dịch được", kèm tên file và đoạn trích gốc/dịch.

### ⑫ Kiểm tra giám khảo

Trước khi chấm sách, model chấm một "bài kiểm tra" 4 đoạn đã biết trước đáp án, kèm một bảng nhân vật mini:

| Đoạn | Cài sẵn | Đúng khi giám khảo… |
|---|---|---|
| 1 | Bản dịch tốt | không báo lỗi gì |
| 2 | Dịch sát chữ, văn máy ("đã không phải là một người đàn ông mà đã…") | báo lỗi *Tự nhiên* hoặc *Văn phong* |
| 3 | Cháu xưng "tôi" với bà, trái bảng nhân vật | báo lỗi *Xưng hô* hoặc *Lời thoại* |
| 4 | Bỏ mất nửa câu | báo lỗi *Trung thành* |

Kết quả: **đáng tin** (bắt đủ 3, không báo nhầm), **tương đối** (thiếu một), hoặc **dễ dãi** (điểm số chỉ nên tham khảo). Bước này chỉ tốn một lượt gọi; bỏ bằng `--no-calibration`.

### ⑬ Chấm từng đoạn trích

Bản dịch được chia thành các **đoạn trích** khoảng một trang (cùng cách gom lô như ⑤). Mỗi đoạn trích gửi cho model: bản gốc và bản dịch dạng chữ thuần (bỏ thẻ), đánh số `[n]`, cùng bảng nhân vật. Model trả một đối tượng JSON:

```json
{"van_de": [{"n": 2, "tieu_chi": "xung_ho", "trich": "tôi vào", "goi_y": "Anna xưng \"cháu\""}],
 "diem":   {"trung_thanh": "tốt", "tu_nhien": "tạm", "van_phong": "tạm",
            "xung_ho": "kém", "loi_thoai": "tạm", "thuat_ngu": "-"}}
```

**6 tiêu chí:**

| Khoá | Tiêu chí | Chấm điều gì | Được "không áp dụng"? |
|---|---|---|---|
| `trung_thanh` | Trung thành | Đủ ý, đúng nghĩa; không thêm, bớt, hiểu sai | Không |
| `tu_nhien` | Tự nhiên | Đọc như văn viết tiếng Việt; không cấu trúc câu tiếng Anh, không "văn máy" | Không |
| `van_phong` | Văn phong văn học | Giàu hình ảnh, giữ nhịp và giọng tác giả; thành ngữ, ẩn dụ chuyển tương đương | Không |
| `xung_ho` | Xưng hô | Đại từ hợp quan hệ, đúng bảng nhân vật, nhất quán | Có (không có đại từ) |
| `loi_thoai` | Lời thoại | Tự nhiên, hợp tuổi tác, tính cách, hoàn cảnh | Có (không có thoại) |
| `thuat_ngu` | Tên riêng & thuật ngữ | Đúng bảng, nhất quán | Có (không có tên riêng) |

Chi tiết kỹ thuật:

- **Liệt kê lỗi trước, chấm sau.** Thứ tự trường trong JSON (`van_de` trước `diem`) buộc model tìm lỗi rồi mới cho điểm, nên điểm bám vào lỗi đã tìm.
- **JSON schema.** Với Ollama, schema được gửi qua tham số `format`; với backend openai qua `response_format`. Server không hỗ trợ (HTTP 400) thì script tự chuyển sang chế độ thường.
- **Bộ đọc nới lỏng.** Bỏ `<think>`, ```` ``` ````, lời dẫn, dấu phẩy thừa; chấp nhận tên tiêu chí viết có dấu ("Xưng hô") hay mức chấm viết khác ("Tốt", "khá", "lỗi"). Không đọc được thì chấm lại một lần ở nhiệt độ 0,3; vẫn hỏng thì bỏ qua đoạn trích đó và lần chạy sau sẽ thử lại.
- **Kiểm tra trích dẫn.** Cụm "trich" được so (không phân biệt hoa thường, ngoặc kép, khoảng trắng) với bản dịch. Không khớp nguyên văn thì nhận xét được gắn cảnh báo "có thể không chính xác".
- **Lưu và chạy tiếp.** Mỗi kết quả lưu ngay vào `sách.vi-review.jsonl`, khoá bằng mã băm của (phiên bản prompt, tên model, bảng nhân vật, bản dịch của đoạn trích). Chạy lại thì chỉ chấm đoạn trích mới hoặc đã đổi. Sửa bảng nhân vật hoặc đổi model thì toàn bộ được chấm lại.
- Giám khảo chấm ở **nhiệt độ 0** để kết quả ổn định.

### ⑭ Tự sửa (`--fix`, tuỳ chọn)

Với mỗi đoạn bị liệt kê lỗi:

1. **Viết lại**: gọi lại model dịch (cùng prompt phong cách và bảng nhân vật), kèm bản dịch cũ và các nhận xét của giám khảo. Bản mới phải qua các kiểm tra định dạng như ⑧.
2. **So sánh A/B hai lần**: lần 1 bản cũ là A, bản mới là B; lần 2 đổi chỗ. Mỗi lần model trả "A", "B" hoặc "ngang" kèm lý do.
3. **Chỉ thay khi bản mới thắng** cả hai lượt (hoặc thắng một, hoà một). Đổi thứ tự là để khử thiên vị vị trí: một model luôn chọn bản đứng trước sẽ cho 1–1 và không thay gì.
4. **Chấm lại** các đoạn trích có đoạn đã thay, để báo cáo hiện điểm **trước và sau khi sửa**.

Mọi lần thử đều được ghi vào `sách.vi-review.jsonl` (bản cũ, bản mới, phiếu, lý do). Đoạn đã thử mà bản mới không thắng sẽ **không bị thử lại** ở lần chạy sau (trừ khi bản dịch của nó đổi), nên chạy `--fix` nhiều lần không tốn thêm. `review --undo-fixes` trả mọi đoạn đã thay về bản cũ.

### ⑮ Báo cáo

`sách.vi-review.html` là một trang HTML tự chứa (không tải gì từ mạng, mở offline, tự đổi sáng/tối), đồng thời Terminal in bản tóm tắt. Cách đọc ở mục 5.

---

## 3. Cài đặt

Chỉ cần mạng **một lần** để cài đặt và tải model. Sau đó mọi thứ chạy offline.

### 3.1. Yêu cầu

- macOS trên Apple Silicon (M1–M4). Linux và Windows cũng chạy được script, chỉ có `setup_mac.sh` là dành riêng cho Mac.
- Python 3.9 trở lên.
- Khoảng 10GB ổ trống cho model.
- Một trong các backend: **Ollama** (dễ nhất), **LM Studio** (thường nhanh hơn trên Mac nhờ MLX), `mlx_lm.server` hoặc `llama-server`.

### 3.2. Cài tự động (khuyên dùng)

```bash
cd epub-translator
./setup_mac.sh                       # Ollama + model gemma4:12b-it-qat
# hoặc
./setup_mac.sh qwen3:14b             # chọn model khác
./setup_mac.sh lmstudio              # chỉ cài phần Python, bạn tự dùng LM Studio
```

Script sẽ lần lượt:

1. Kiểm tra Python ≥ 3.9.
2. Tạo môi trường ảo `.venv` và cài `lxml`.
3. Cài Ollama qua Homebrew nếu chưa có, rồi khởi động nó.
4. Tải model.
5. Chạy `check` để dịch thử một câu và đo tốc độ.

### 3.3. Cài thủ công

**Bước 1: Python và thư viện**

```bash
xcode-select --install                       # nếu máy chưa có python3
cd epub-translator
python3 -m venv .venv                        # môi trường ảo, PHẢI tên .venv, nằm cạnh script
.venv/bin/python3 -m pip install -r requirements.txt
```

Không cần `source .venv/bin/activate`: khi bạn gõ `python3 epub_translate.py …` bằng Python hệ thống (không có `lxml`), script tự chạy lại bằng Python trong `.venv`.

**Bước 2: chọn một backend**

*A. Ollama*

```bash
brew install --cask ollama       # hoặc tải từ https://ollama.com/download
open -a Ollama                   # chạy nền, có biểu tượng trên thanh menu
ollama pull gemma4:12b-it-qat
ollama pull qwen3:14b            # (tuỳ chọn) để so sánh
ollama list                      # xem tên model đã tải
```

Không cần cấu hình `OLLAMA_CONTEXT_LENGTH`, vì script tự gửi `num_ctx` trong mỗi lượt gọi.

*B. LM Studio*

1. Cài LM Studio từ lmstudio.ai.
2. Tìm và tải một model 12–14B bản **MLX 4-bit**.
3. Vào tab **Developer**, nạp model, bật **Start Server** (mặc định cổng 1234).
4. Khi chạy script, thêm `--backend openai --model "<tên model hiển thị trong LM Studio>"`.

*C. mlx_lm.server hoặc llama.cpp*

```bash
pip install mlx-lm
mlx_lm.server --model <repo-model-MLX-4bit> --port 8080
# rồi chạy script với:
--backend openai --base-url http://localhost:8080/v1 --model <repo-model-MLX-4bit>
```

### 3.4. Kiểm tra cài đặt

```bash
python3 epub_translate.py check --model gemma4:12b-it-qat
python3 epub_translate.py check --model gemma4:12b-it-qat sach.epub   # kèm ước tính thời gian
```

Kết quả mẫu (câu dịch chỉ để minh hoạ, chữ thực tế tuỳ model):

```
Server : http://localhost:11434  (ollama)
Model  : gemma4:12b-it-qat
EN : She had never seen the sea before, and when at last it rose beyond the dunes, …
VI : Nàng chưa từng thấy biển bao giờ, và khi rốt cuộc nó hiện lên sau những đụn cát, …
Định dạng : ✓ giữ đúng thẻ
Tốc độ    : 12.4 token/s (tổng thời gian lượt gọi 6.1s)
Giới hạn  : 1,024 token đầu ra mỗi lượt · ngữ cảnh 8,192 · ✓ không bị cắt
Thinking  : ✓ đã tắt
Ước tính  : sach.epub cần ~190,000 token → khoảng 4h15m ở tốc độ này
```

---

## 4. Cách dùng

> **Luôn gõ `python3`.** macOS không có lệnh `python` (Apple bỏ Python 2 từ macOS 12.3); `python` chỉ tồn tại khi bạn đã kích hoạt một môi trường ảo. Script tự chuyển sang Python trong `.venv` nằm cạnh nó, nên không cần `source .venv/bin/activate`. Nếu đã kích hoạt thì `python3` vẫn chạy đúng.

### 4.1. Một lệnh làm tất cả: `run`

```bash
python3 epub_translate.py run ~/Desktop/sach.epub
```

Lệnh này chạy lần lượt 5 bước, đúng như khi bạn gõ từng lệnh lẻ:

| Bước | Làm gì | Khi chạy lại |
|---|---|---|
| 1. Kiểm tra model | Như `check`: gọi thử, đo tốc độ, ước tính thời gian, đồng thời nạp model vào RAM. Không gọi được model thì **dừng ngay** | Luôn chạy (vài giây) |
| 2. Bảng nhân vật | Chưa có `sach.glossary.md` cạnh file sách thì tạo bản nháp, **mở bằng trình soạn thảo và dừng chờ** bạn sửa, lưu, nhấn Enter | Có rồi thì dùng luôn |
| 3. Dịch | Như `translate` | Chỉ dịch phần chưa có trong cache |
| 4. Chấm + tự sửa | Như `review --fix`, xuất báo cáo HTML | Chỉ chấm đoạn trích mới / đã đổi; không thử sửa lại đoạn đã thử |
| 5. Đóng gói | Như `build`, gồm cả các đoạn vừa được tự sửa | Luôn chạy (vài giây) |

Vì mỗi bước lưu trạng thái ra đĩa, **dừng ở đâu cũng được** (Ctrl+C, đóng Terminal, hết pin): chạy lại đúng lệnh `run` là làm tiếp từ chỗ dừng.

Các biến thể hay dùng:

```bash
# Chạy thử cả pipeline trên 40 đoạn để xem chất lượng trước khi chạy qua đêm
python3 epub_translate.py run sach.epub --max-segments 40 --open

# Chạy qua đêm, không dừng chờ sửa bảng nhân vật (dùng bản nháp)
caffeinate -i python3 epub_translate.py run sach.epub --no-pause --open

# Dùng bảng nhân vật có sẵn ở chỗ khác, model khác, bỏ bước chấm
python3 epub_translate.py run sach.epub --glossary glossary.md --model qwen3:14b --no-review
```

Model mặc định của mọi lệnh là `gemma4:12b-it-qat`. Đổi cho một lần bằng `--model`, hoặc đổi luôn mặc định bằng biến môi trường (thêm vào `~/.zshrc`):

```bash
export EPUBTR_MODEL=qwen3:14b
```

### 4.2. Chạy từng bước (khi muốn kiểm soát từng khâu)

```bash
M=gemma4:12b-it-qat

# 1. Xem sách: bao nhiêu chương, bao nhiêu đoạn, ước tính thời gian
python3 epub_translate.py info sach.epub

# 2. Tạo bản nháp bảng nhân vật từ ~40.000 ký tự đầu sách, rồi MỞ RA SỬA (mục 6)
python3 epub_translate.py characters sach.epub --model $M -o glossary.md

# 3. Dịch THỬ 40 đoạn và chấm thử, đọc sach.vi.epub + mở báo cáo.
#    --max-segments chỉ để thử: mỗi lần chạy dịch thêm đúng 40 đoạn rồi dừng.
python3 epub_translate.py translate sach.epub --model $M --glossary glossary.md --max-segments 40
python3 epub_translate.py review    sach.epub --model $M --glossary glossary.md --open

# 4. Chưa ưng? Sửa glossary / prompt / model, XOÁ cache và kết quả chấm rồi thử lại:
rm sach.vi-cache.jsonl sach.vi-review.jsonl

# 5. Ưng rồi: dịch cả cuốn (qua đêm). Ctrl+C để dừng, chạy lại đúng lệnh để tiếp tục
caffeinate -i python3 epub_translate.py translate sach.epub --model $M --glossary glossary.md

# 6. Chấm cả cuốn, tự sửa các đoạn bị chê, rồi đóng gói lại
caffeinate -i python3 epub_translate.py review sach.epub --model $M --glossary glossary.md --fix --open
python3 epub_translate.py build sach.epub
```

**So sánh hai model** trên cùng 40 đoạn: đặt tên cache và file đầu ra riêng.

```bash
python3 epub_translate.py translate sach.epub --model gemma4:12b-it-qat --glossary glossary.md \
  --max-segments 40 --cache thu-gemma.jsonl -o thu-gemma.epub
python3 epub_translate.py translate sach.epub --model qwen3:14b --glossary glossary.md \
  --max-segments 40 --cache thu-qwen.jsonl -o thu-qwen.epub
# chấm mỗi bản bằng chính model đã dịch nó:
python3 epub_translate.py review sach.epub --model gemma4:12b-it-qat --glossary glossary.md --cache thu-gemma.jsonl
python3 epub_translate.py review sach.epub --model qwen3:14b --glossary glossary.md --cache thu-qwen.jsonl
```

Lưu ý khi so điểm: mỗi model tự chấm bài mình, nên điểm của hai model **không so trực tiếp được**. Hãy so phần "kiểm tra giám khảo" và tự đọc các đoạn bị nêu.

Trong khi chạy, mỗi lô in một dòng tiến độ:

```
[   120/3,412] OEBPS/chapter03.xhtml   12.1 tok/s · đã chạy 14m02s · còn ~6h31m
```

### 4.3. Tham chiếu lệnh

#### `run`: cả pipeline

```bash
python3 epub_translate.py run sach.epub [tuỳ chọn]
```

Nhận mọi tuỳ chọn của `translate` (model, giới hạn token, `--max-segments`, `-o`, `--bilingual`, `--title`, `--drop-fonts`…) cộng thêm:

| Tuỳ chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--glossary` | `<tên sách>.glossary.md` | Bảng nhân vật. Chưa có thì tự tạo bản nháp |
| `--no-glossary` | | Không dùng / không tạo bảng nhân vật |
| `--no-pause` | | Không dừng chờ sửa bản nháp bảng nhân vật. Chạy không trong Terminal (vd. qua lịch hẹn) thì tự không dừng |
| `--chars` | `40000` | Số ký tự đầu sách để lập bảng nhân vật |
| `--no-review` | | Bỏ bước chấm (và tự sửa) |
| `--no-fix` | | Chấm nhưng không tự viết lại đoạn bị chê |
| `--max-fixes`, `--no-calibration`, `--report` | | Như `review` |
| `--open` | | Mở báo cáo chấm khi xong |

Mã thoát: 0 khi xong; 1 khi không gọi được model hay lỗi model giữa chừng; 130 khi bị dừng (Ctrl+C).

#### `info`: xem cấu trúc sách

```bash
python3 epub_translate.py info sach.epub [--tps 12] [--cache FILE]
```

In danh sách file theo thứ tự đọc, số đoạn và ký tự từng file, tổng số từ, số đoạn cần dịch, và ước tính thời gian ở tốc độ `--tps` token/giây. Ước tính dùng hệ số khoảng 0,45 token đầu ra cho mỗi ký tự gốc, rất thô. Dùng `check` để có tốc độ thật.

#### `check`: kiểm tra model

```bash
python3 epub_translate.py check [sach.epub] --model TÊN [tuỳ chọn model]
```

Dịch một câu mẫu có thẻ định dạng, rồi báo: kết nối được không, model có giữ đúng thẻ không, có đang bật thinking không, tốc độ token/s. Nếu truyền sách vào thì ước tính thời gian dịch cuốn đó. Mã thoát: 0 nếu ổn, 1 nếu câu thử chưa đạt (sai thẻ, trống), 2 nếu không gọi được model.

#### `characters`: tạo bản nháp bảng nhân vật

```bash
python3 epub_translate.py characters sach.epub --model TÊN [-o glossary.md] [--chars 40000]
```

Đọc `--chars` ký tự đầu sách, chia thành phần ~6000 ký tự, nhờ model liệt kê nhân vật (giới tính, vai trò, quan hệ, gợi ý đại từ trong lời kể), gộp các dòng trùng tên, rồi ghi ra file Markdown kèm khung các cặp xưng hô để bạn điền. **Đây chỉ là bản nháp, phải sửa lại.**

#### `prompt`: xem prompt sẽ gửi

```bash
python3 epub_translate.py prompt [--glossary glossary.md] [--prompt-file style.md]   # prompt dịch
python3 epub_translate.py prompt --judge [--glossary glossary.md]                    # prompt giám khảo
```

#### `translate`: dịch

```bash
python3 epub_translate.py translate sach.epub --model TÊN [tuỳ chọn]
```

| Tuỳ chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--model` | `gemma4:12b-it-qat` | Tên model. Đổi mặc định bằng biến môi trường `EPUBTR_MODEL` |
| `--backend` | `ollama` | `ollama` hoặc `openai` (LM Studio, mlx_lm, llama.cpp) |
| `--base-url` | `http://localhost:11434` / `http://localhost:1234/v1` | Địa chỉ server |
| `--glossary` | | File bảng nhân vật và thuật ngữ |
| `--prompt-file` | | File thay phần phong cách (kể cả ví dụ) của prompt |
| `--temperature` | `0.4` | Cao: văn bay hơn nhưng dễ "sáng tác". Thấp: sát nghĩa hơn |
| `--batch-chars` | `1800` | Ký tự gốc tối đa mỗi lượt |
| `--batch-max` | `10` | Số đoạn tối đa mỗi lượt. Model hay bỏ hoặc gộp đoạn thì giảm xuống 5–6 |
| `--context` | `2` | Số đoạn dịch trước gửi kèm. `0` để tắt |
| `--retries` | `2` | Số lần dịch lại một đoạn lỗi |
| `--max-tokens` | tự tính | Giới hạn token đầu ra mỗi lượt (mặc định 1,5 × ký tự gốc + 512, tối thiểu 1024) |
| `--num-ctx` | `8192` (`16384` khi `--think`) | Cửa sổ ngữ cảnh (Ollama). Tăng nếu glossary rất dài hoặc tăng `--max-tokens` |
| `--think` | tắt | Bật chế độ suy nghĩ. Không khuyên dùng: chậm gấp nhiều lần |
| `--think-budget` | `4096` | Token cộng thêm cho phần suy nghĩ khi `--think` |
| `--keep-loaded` | tắt | Giữ model trong RAM thêm 30 phút khi xong. Mặc định: giải phóng ngay |
| `--timeout` | `900` | Giây chờ tối đa mỗi lượt gọi |
| `--max-segments` | | **Chỉ để thử**: dịch N đoạn chưa có trong cache rồi dừng. Dịch cả cuốn thì bỏ tuỳ chọn này |
| `--redo TÊN` | | Dịch lại các file có tên chứa TÊN dù đã có cache. Dùng nhiều lần được |
| `-o`, `--output` | `sach.vi.epub` | File đầu ra |
| `--cache` | `sach.vi-cache.jsonl` | File cache |
| `--bilingual` | | Xuất song ngữ (gốc rồi đến bản dịch) |
| `--title` | | Đặt tên sách mới trong metadata |
| `--drop-fonts` | | Bỏ font nhúng (sửa lỗi ô vuông ở chữ có dấu) |
| `--keep-id` | | Giữ nguyên mã định danh sách |

#### `review`: thẩm định bản dịch

```bash
python3 epub_translate.py review sach.epub --model TÊN [tuỳ chọn]
```

Giám khảo là model bạn truyền vào `--model`. Để "chính model đó chấm", dùng đúng model đã dịch.

| Tuỳ chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--model`, `--backend`, `--base-url`, `--max-tokens`, `--num-ctx`, `--think`, `--keep-loaded`, `--timeout` | | Như `translate` |
| `--glossary` | | Bảng nhân vật: căn cứ để chấm xưng hô và thuật ngữ. **Nên dùng đúng file khi dịch** |
| `--cache` | `sach.vi-cache.jsonl` | Bản dịch cần chấm |
| `--report` | `sach.vi-review.html` | File báo cáo |
| `--batch-chars` / `--batch-max` | `1800` / `10` | Độ dài mỗi đoạn trích |
| `--max-chunks N` | | Chỉ chấm N đoạn trích chưa có kết quả (để thử, đo thời gian) |
| `--no-calibration` | | Bỏ bước kiểm tra giám khảo |
| `--fix` | | Viết lại đoạn bị chê, so sánh A/B, chỉ thay khi bản mới thắng, rồi chấm lại |
| `--max-fixes N` | | Viết lại tối đa N đoạn mỗi lần chạy |
| `--prompt-file`, `--temperature`, `--retries` | | Dùng khi `--fix` viết lại (như `translate`) |
| `--undo-fixes` | | Trả mọi đoạn đã thay về bản cũ (không cần `--model`) |
| `--open` | | Mở báo cáo trong trình duyệt khi xong |

Terminal in tóm tắt:

```
Kiểm tra giám khảo : bắt được 3/3 lỗi cài sẵn, báo nhầm 0 → đáng tin
Phạm vi            : Đã chấm 312/312 đoạn trích (100% nội dung đã dịch)

Tiêu chí                 Điểm   tốt █  tạm ▒  kém ░     vấn đề
Trung thành                88   ████████████████▒▒▒░        21
Tự nhiên                   71   ███████████▒▒▒▒▒▒▒░░        64
Văn phong văn học          66   ██████████▒▒▒▒▒▒▒░░░        78
Xưng hô                    74   ████████████▒▒▒▒░░░░        35
Lời thoại                  70   ███████████▒▒▒▒▒▒░░░        29
Tên riêng & thuật ngữ      93   ██████████████████▒▒         4

Điểm tổng: 77/100 (Khá)   ·   trước khi sửa: 72
Chương điểm thấp nhất: Chương 7 (61) · Chương 12 (64) · Chương 3 (66)
Báo cáo chi tiết: sach.vi-review.html
```

(Các con số ở trên chỉ để minh hoạ định dạng.)

#### `build`: đóng gói lại từ cache, không gọi model

```bash
python3 epub_translate.py build sach.epub [-o FILE] [--cache FILE] [--bilingual] [--title …] [--drop-fonts] [--keep-id]
```

Dùng sau khi sửa tay cache, sau `review --fix` / `--undo-fixes`, để xuất bản song ngữ, hoặc để xem bản dịch dở dang khi đang dừng giữa chừng.

---

## 5. Đọc báo cáo đánh giá

Báo cáo `sach.vi-review.html` có các phần sau, theo thứ tự:

1. **Điểm tổng** (góc trên phải): trung bình 6 tiêu chí, kèm xếp loại và điểm *trước khi sửa* nếu có chạy `--fix`.
2. **Giám khảo có đáng tin không?** Kết quả bước ⑫, từng đoạn kiểm tra bắt được hay bỏ sót. **Đọc phần này trước**: nếu giám khảo "dễ dãi", điểm số bên dưới nên đọc dè dặt, nhưng danh sách lỗi cụ thể vẫn có ích.
3. **Theo tiêu chí**: mỗi tiêu chí một thẻ, có điểm, định nghĩa, thanh tỷ lệ tốt / tạm / kém (tính theo độ dài văn bản), tỷ lệ "không áp dụng" và số vấn đề.
4. **Theo chương**: bảng chương × tiêu chí, ô tô màu theo điểm. Dùng để tìm chương cần soát hoặc dịch lại (`translate --redo`).
5. **Các đoạn cần xem lại**: từng vấn đề với tiêu chí, vị trí (chương · đoạn thứ mấy), bản dịch có **tô vàng cụm bị chê**, bản gốc, gợi ý sửa. Bấm nút tiêu chí ở trên để lọc. Dòng cảnh báo màu cam nghĩa là cụm trích không khớp nguyên văn bản dịch, nhận xét có thể sai.
6. **Các đoạn đã viết lại** (khi `--fix`): bản cũ và bản mới đặt cạnh nhau, trạng thái (đã thay / giữ bản cũ), số phiếu, lý do của giám khảo.

**Cách tính điểm:**

- Mỗi đoạn trích, mỗi tiêu chí: tốt = 100, tạm = 50, kém = 0. "Không áp dụng" không tính.
- Điểm tiêu chí của sách / chương = trung bình có trọng số theo độ dài bản gốc của các đoạn trích.
- Điểm tổng = trung bình các tiêu chí có điểm.
- Xếp loại: từ 85 **Tốt**, từ 65 **Khá**, từ 45 **Trung bình**, dưới 45 **Yếu**.

**Nên làm gì với báo cáo:**

- Điểm thấp tập trung ở **Xưng hô** → bổ sung cặp xưng hô vào glossary, dịch lại các chương điểm thấp bằng `--redo`, rồi chấm lại.
- Điểm thấp ở **Tự nhiên / Văn phong** khắp sách → thử tăng `--temperature`, thêm ví dụ của riêng bạn vào `--prompt-file`, hoặc đổi model; sau đó dùng `review --fix`.
- Vấn đề lẻ tẻ → sửa tay trong cache (mục 9) hoặc để `--fix` lo.

---

## 6. Viết bảng nhân vật hiệu quả

File glossary được đưa **nguyên văn** vào mọi lượt dịch và mọi lượt chấm, nên hãy viết ngắn, dứt khoát, dạng gạch đầu dòng. Xem mẫu đầy đủ trong [`glossary.example.md`](../glossary.example.md).

```markdown
## Nhân vật
- **Elena Ward** — nữ, 24 tuổi, người kể chuyện ngôi thứ nhất. Lời kể xưng "tôi".
- **Captain Reyes** — nam, ngoài 50, cộc cằn. Lời kể gọi "lão Reyes".

## Xưng hô trong lời thoại
- Elena ↔ Captain Reyes: Elena xưng "cháu", gọi "bác"; Reyes xưng "ta", gọi "cô".

## Tên riêng, địa danh, thuật ngữ
- Greyhaven → giữ nguyên (tên đảo)
- the lamp room → buồng đèn
```

Mẹo:

- **Cặp xưng hô là phần đáng công nhất.** Mỗi cặp nhân vật hay nói chuyện với nhau nên có một dòng.
- **Quan hệ thay đổi theo truyện** (lạ thành quen, quen thành yêu): sửa glossary trước khi dịch tới chương đó. Nếu đã dịch rồi thì dùng `--redo` cho các chương liên quan.
- Ghi rõ cả những từ **không** được dịch kiểu nào, ví dụ "the Keeper → người gác đèn, không dịch là Thủ Môn".
- Dưới khoảng 2.000 ký tự là vừa. Glossary quá dài làm model nhỏ "loãng" sự chú ý và chạy chậm hơn.
- Viết chú thích cho riêng mình trong `<!-- … -->`, phần này không gửi cho model.
- Sửa glossary làm **toàn bộ kết quả chấm cũ hết hiệu lực** (vì căn cứ chấm đã đổi), lần `review` sau sẽ chấm lại cả sách.

---

## 7. Tuỳ chỉnh văn phong

In prompt mặc định ra file, sửa phần **PHONG CÁCH** và **VÍ DỤ VỀ VĂN PHONG**, rồi dùng lại:

```bash
python3 epub_translate.py prompt > style.md
# mở style.md, chỉ giữ và sửa phần đầu (PHONG CÁCH + VÍ DỤ), xoá phần ĐỊNH DẠNG ĐẦU RA trở đi
python3 epub_translate.py translate sach.epub --model gemma4:12b-it-qat --prompt-file style.md --glossary glossary.md
```

Ví dụ những điều có thể thêm:

- Giọng văn: "văn cổ, trang trọng, dùng từ Hán Việt vừa phải" cho tiểu thuyết lịch sử; "giọng trẻ, đời thường" cho văn học đương đại.
- Quy ước lời thoại: "chuyển dấu ngoặc kép thành gạch đầu dòng (—) như sách in Việt Nam".
- **Ví dụ của riêng bạn**: thay 3 cặp ví dụ mặc định bằng 3–4 câu lấy từ chính cuốn sách đang dịch, kèm bản dịch bạn ưng ý. Đây là cách hiệu quả nhất để kéo model về đúng giọng bạn muốn.

Phần **ĐỊNH DẠNG ĐẦU RA** luôn được script tự thêm vào và không thay được, vì bộ phân tích câu trả lời phụ thuộc vào nó.

---

## 8. Các file sinh ra

| File | Nội dung |
|---|---|
| `sach.vi.epub` | Bản dịch |
| `sach.en-vi.epub` | Bản song ngữ (khi dùng `--bilingual`) |
| `sach.vi-cache.jsonl` | Toàn bộ bản dịch theo từng đoạn, kể cả các bản cũ. **Đừng xoá** nếu còn muốn chạy tiếp hoặc sửa |
| `sach.vi.report.txt` | Các đoạn mất định dạng / chưa dịch được. Chỉ tạo khi có |
| `sach.vi-review.jsonl` | Kết quả chấm từng đoạn trích và lịch sử `--fix` (bản cũ, bản mới, phiếu, lý do) |
| `sach.vi-review.html` | Báo cáo đánh giá |
| `glossary.md` | Bảng nhân vật, do lệnh `characters` tạo rồi bạn sửa |

Một dòng trong cache trông như sau:

```json
{"key": "3f1c…", "doc": "OEBPS/ch1.xhtml", "en": "She had <g1>never</g1> seen the sea.", "vi": "Nàng <g1>chưa từng</g1> thấy biển."}
```

---

## 9. Soát và sửa bản dịch

1. **Đọc báo cáo** `sach.vi-review.html` để biết soát chỗ nào trước.
2. **Đọc bản song ngữ** trên máy đọc sách hoặc Apple Books:
   ```bash
   python3 epub_translate.py build sach.epub --bilingual
   ```
3. **Sửa tay**: mở `sach.vi-cache.jsonl` bằng trình soạn thảo, tìm đoạn (Cmd+F theo câu tiếng Anh), sửa trường `"vi"`. Giữ nguyên các thẻ `<g1>…</g1>`, `<x2/>`. Sau đó đóng gói lại:
   ```bash
   python3 epub_translate.py build sach.epub
   ```
4. **Dịch lại cả chương** sau khi sửa glossary:
   ```bash
   python3 epub_translate.py translate sach.epub --model gemma4:12b-it-qat --glossary glossary.md --redo chapter07 --redo chapter08
   ```
   Tên file chương xem bằng lệnh `info` hoặc ở cột "Chương" trong báo cáo.
5. **Hoàn tác `--fix`** nếu không ưng: `python3 epub_translate.py review sach.epub --undo-fixes`, rồi `build`.

---

## 10. Mẹo chạy trên MacBook Air

- **Cắm sạc, để mở nắp, chạy kèm `caffeinate -i`** để máy không ngủ giữa chừng.
- **RAM được trả lại khi script dừng.** Xong việc hay bấm Ctrl+C, script gỡ model khỏi Ollama ngay (dòng cuối in "Đã giải phóng model … khỏi RAM"). Kiểm tra bằng `ollama ps`: danh sách phải trống. Bản thân app Ollama vẫn chạy nền nhưng rất nhẹ khi không có model; muốn tắt hẳn thì thoát ở biểu tượng trên thanh menu. Với LM Studio, script không gỡ được model: bấm Eject trong LM Studio, hoặc đặt thời gian tự gỡ (TTL) trong phần cài đặt server.
- **Chạy liền `translate` rồi `review`**: thêm `--keep-loaded` vào lệnh đầu để khỏi nạp lại model (tiết kiệm vài giây đến vài chục giây).
- **Đóng Chrome và các app nặng.** Mở Activity Monitor, tab Memory: "Memory Pressure" phải xanh. Với Ollama, `ollama ps` phải hiện `100% GPU`. Nếu thấy chia CPU/GPU tức là tràn RAM.
- **Air không có quạt** nên sẽ hạ xung sau 15–30 phút tải nặng. Kê máy chỗ thoáng, tránh để trên chăn đệm. Tốc độ giảm 20–30% là bình thường.
- **Thời gian thẩm định** (ước tính thô): `review` chủ yếu *đọc* (EN + VI) và chỉ *viết* JSON ngắn, nên tốn khoảng một nửa thời gian dịch. `--fix` tốn thêm cho mỗi đoạn bị chê: một lượt viết lại và hai lượt so sánh. Chạy `review --max-chunks 5` để đo trên máy bạn.
- **LM Studio với model MLX** thường nhanh hơn Ollama (bản GGUF) trên Apple Silicon. Nên thử cả hai bằng `check`.
- Lượt gọi lớn hơn (`--batch-chars 2500`) giảm tỷ lệ thời gian xử lý prompt, nhưng chỉ nên tăng khi model ít lỗi.

---

## 11. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| Mỗi lần chạy chỉ dịch được một ít rồi dừng | Bạn đang dùng `--max-segments` (chế độ dịch thử). Bỏ tuỳ chọn này để dịch cả cuốn; phần đã dịch vẫn giữ trong cache |
| `Không kết nối được tới server model` | Mở app Ollama (`open -a Ollama`) hoặc bật server trong LM Studio |
| `HTTP 404 … not found` | Sai tên model. Xem `ollama list`, hoặc `ollama pull <tên>` |
| Rất chậm, Memory Pressure đỏ | Đóng app khác, hoặc dùng model nhỏ hơn |
| `check` báo "VI: (trống — model dùng hết lượt cho phần suy nghĩ…)" | Bạn đang dùng `--think`, hoặc Ollama cũ không hiểu `think: false`. Bỏ `--think`; cập nhật Ollama (`brew upgrade --cask ollama`) |
| Script báo model vẫn suy nghĩ (thinking) | Ollama: cập nhật Ollama hoặc dùng bản model không-thinking. LM Studio: tắt Thinking/Reasoning trong cài đặt model |
| `Câu trả lời bị cắt vì chạm giới hạn … token` | `--max-tokens 6000 --num-ctx 16384` |
| `Giới hạn … không vừa cửa sổ ngữ cảnh` | Tăng `--num-ctx` (ví dụ 16384), hoặc giảm `--max-tokens` / `--batch-chars` |
| `ollama ps` vẫn còn model sau khi script dừng | Script bị tắt đột ngột, hoặc bạn dùng `--keep-loaded`. Gỡ ngay: `ollama stop gemma4:12b-it-qat`, hoặc đợi Ollama tự gỡ (5 phút / 30 phút) |
| Nhiều đoạn bị dịch lại, model hay bỏ hoặc gộp đoạn | `--batch-max 5` |
| Bản dịch lẫn chữ Trung Quốc liên tục | Đổi sang Gemma, hoặc giảm `--temperature 0.3` |
| Câu văn khô, sát chữ | Tăng `--temperature 0.6`, thêm ví dụ của bạn vào `--prompt-file`, rồi `review --fix` |
| Xưng hô sai từ một chương nào đó | Sửa glossary, rồi `--redo <tên-chương>` |
| Chữ có dấu hiện ô vuông | `build … --drop-fonts`, hoặc đổi font trong app đọc sách |
| Apple Books hiện bản dịch trùng với bản gốc | Không dùng `--keep-id` (mặc định script đã đổi mã định danh) |
| `Sách có DRM` | Script chỉ đọc được EPUB không DRM |
| `Thiếu thư viện lxml` | Chưa có `.venv` cạnh script: chạy `./setup_mac.sh`, hoặc `python3 -m pip install lxml` |
| `zsh: command not found: python` | Dùng `python3`. macOS không có lệnh `python` ngoài môi trường ảo |
| `review`: giám khảo "dễ dãi" | Bình thường với model nhỏ tự chấm. Dùng danh sách vấn đề hơn là con số; đọc thêm các chương điểm thấp |
| `review`: nhiều đoạn trích "không đọc được kết quả chấm" | Giảm `--batch-max 6`; tăng `--max-tokens 4000`; chạy lại lệnh (chỉ chấm phần còn thiếu) |
| `review`: `Server không nhận ràng buộc JSON schema` | Không phải lỗi; script tự chuyển chế độ thường. Có thể nhiều câu trả lời hỏng hơn một chút |
| `review --fix` gần như không thay đoạn nào | Model thấy bản cũ không kém hơn, hoặc so sánh A/B ra 1–1 (thiên vị vị trí). Không sao; tự sửa các đoạn trong báo cáo |

---

## 12. Kiểm thử

```bash
python3 tests/run_tests.py
```

Bộ test **không cần model và không cần mạng**:

1. `tests/make_sample.py` tạo một EPUB 3 mẫu có đủ trường hợp khó: chú thích, neo, ngắt trang, `<br>`, `&nbsp;`, DOCTYPE XHTML 1.1, `<pre>`, khối hỗn hợp, bảng, danh sách, ảnh, tên file có dấu cách, đoạn trùng lặp.
2. `tests/mock_server.py` là server giả lập Ollama/OpenAI, "dịch" bằng cách viết hoa và cố ý gây lỗi: HTTP 500 ở lượt đầu, gộp đoạn, mất thẻ, chèn chữ Trung, trả rỗng, chèn `<think>`, lời mở đầu, khối ```` ``` ````. Với `review`, nó đóng vai giám khảo: trả JSON bẩn (bọc ```` ``` ````, dấu phẩy thừa, câu trả lời hỏng), trích dẫn sai nguyên văn, dùng tên tiêu chí có dấu, trả HTTP 400 khi nhận `response_format`, và so sánh A/B có đoạn chuộng bản mới, có đoạn chuộng bản cũ.
3. `tests/run_tests.py` chạy mọi lệnh rồi kiểm tra từng chi tiết của EPUB và báo cáo đầu ra, gồm Ctrl+C giữa chừng (kèm giải phóng model), chạy lại không gọi model thừa, `--fix`, `--undo-fixes`.

Kết quả mong đợi: `32/32 bài kiểm tra đạt.` Đặt `VERBOSE=1` để xem traceback khi có lỗi.

---

## 13. Cấu trúc thư mục

```
epub-translator/
├── epub_translate.py       # toàn bộ chương trình (một file)
├── requirements.txt        # lxml
├── setup_mac.sh            # cài đặt tự động trên macOS
├── glossary.example.md     # mẫu bảng nhân vật & xưng hô
├── README.md
└── tests/
    ├── make_sample.py      # tạo EPUB mẫu
    ├── mock_server.py      # server LLM giả lập có cài lỗi (dịch + giám khảo)
    └── run_tests.py        # chạy toàn bộ kiểm thử
```

---

## 14. Giới hạn đã biết

- Không đọc được sách có DRM.
- Chữ nằm trong ảnh và chữ trong `<svg>` không được dịch.
- Tên chương trong `<title>` ở phần `<head>` (thứ app đọc sách ít khi hiển thị) không được dịch.
- Đoạn văn cực dài (trên ~10.000 ký tự) có thể vượt cửa sổ ngữ cảnh 8192. Khi đó tăng `--num-ctx`.
- Chất lượng văn phụ thuộc hoàn toàn vào model. Model 12–14B cho bản nháp đọc trôi, nhưng vẫn cần người soát xưng hô, chơi chữ và những đoạn giàu hình ảnh.
- **Model tự chấm bài mình** có xu hướng dễ dãi và có "gu" giống lúc dịch, nên bỏ sót đúng những lỗi nó hay mắc. Báo cáo cho biết *nên soát chỗ nào*, không thay được người đọc. Điểm của hai model khác nhau không so trực tiếp được.
- Giám khảo chấm từng đoạn trích độc lập, nên không phát hiện được lỗi chỉ lộ ra khi đọc liền nhiều chương (ví dụ một nhân vật đổi cách xưng hô giữa chương 2 và chương 9), trừ khi bảng nhân vật nêu rõ.
- Thay đổi glossary không tự làm mới các đoạn *đã dịch* trong cache. Dùng `--redo` hoặc xoá cache.
