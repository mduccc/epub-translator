"""Server giả lập Ollama (/api/chat) và OpenAI (/v1/chat/completions), có cài lỗi cố ý.

    python3 mock_server.py PORT [DELAY_GIÂY] [FILE_LOG]

"Dịch" giả = viết hoa chữ, giữ nguyên thẻ. Các từ khoá trong đoạn gốc kích hoạt lỗi:
  MERGE_ME  gộp đoạn này với đoạn sau (khi dịch theo lô)
  DROPTAG   luôn làm mất một cặp thẻ  -> script phải ráp nới lỏng
  TAGFLAKY  mất thẻ khi dịch theo lô, đúng khi dịch lẻ
  CJK       chèn chữ Trung khi dịch theo lô
  NEVER     luôn trả về rỗng          -> đoạn phải được giữ tiếng Anh
Lượt gọi đầu tiên luôn trả HTTP 500; câu trả lời xen kẽ có <think>, lời mở đầu, ```.
"""
import json, re, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
LOG = sys.argv[3] if len(sys.argv) > 3 else None
STATE = {"calls": 0, "log": []}
TOK = re.compile(r"(</?[gx]\d+/?>)")


def fake_tr(s):
    # "Dịch" giả: viết hoa phần chữ, giữ nguyên thẻ giữ chỗ.
    return "".join(p if TOK.fullmatch(p) else p.upper() for p in TOK.split(s))


JUDGE = {"calls": 0}


def judge_respond(user):
    """Giám khảo giả: chê đoạn có 'STORM' (văn phong) và 'FATHER' (xưng hô, trích sai nguyên văn).
    Đoạn đã có '(ĐÃ SỬA)' thì không chê. Vài lượt đầu trả JSON bẩn để thử bộ đọc."""
    JUDGE["calls"] += 1
    if "Anna" in user:                                   # bộ kiểm tra giám khảo
        obj = {"van_de": [
            {"n": 2, "tieu_chi": "tu_nhien", "trich": "một cách dễ dàng", "goi_y": "viết gọn"},
            {"n": 3, "tieu_chi": "xung_ho", "trich": "tôi vào", "goi_y": "Anna phải xưng cháu"},
            {"n": 4, "tieu_chi": "trung_thanh", "trich": "", "goi_y": "thiếu ý gấp đôi, cất vào áo"}],
            "diem": {"trung_thanh": "tạm", "tu_nhien": "kém", "van_phong": "tạm",
                     "xung_ho": "kém", "loi_thoai": "tạm", "thuat_ngu": "tốt"}}
        return json.dumps(obj, ensure_ascii=False)
    pairs = re.findall(r"^\[(\d+)\]\nEN: (.*)\nVI: (.*)$", user, re.M)
    issues, storm, father, talk = [], False, False, False
    for n, en, vi in pairs:
        talk = talk or '"' in vi
        if "ĐÃ SỬA" in vi:
            continue
        if "STORM" in vi:
            storm = True
            issues.append({"n": int(n), "tieu_chi": "van_phong", "trich": "the storm",
                           "goi_y": "dịch giàu hình ảnh hơn"})
        if "FATHER" in vi:
            father = True
            issues.append({"n": int(n), "tieu_chi": "Xưng hô", "trich": "ông bố",
                           "goi_y": "dùng \"cha nàng\""})
    diem = {"trung_thanh": "tốt", "tu_nhien": "Tốt", "van_phong": "kém" if storm else "tốt",
            "xung_ho": "tạm" if father else "tốt", "loi_thoai": "tạm" if talk else "-",
            "thuat_ngu": "-"}
    text = json.dumps({"van_de": issues, "diem": diem}, ensure_ascii=False)
    k = JUDGE["calls"]
    if k == 2:
        return "<think>xem nào</think>\nKết quả:\n```json\n" + text + "\n```"
    if k == 3:
        return text[:-2] + ",}}"                          # dấu phẩy thừa
    if k == 4:
        return "Xin lỗi, tôi không chấm được đoạn này."   # hỏng -> phải thử lại
    return text


def compare_respond(user):
    a = re.search(r"^Bản A: (.*)$", user, re.M).group(1)
    b = re.search(r"^Bản B: (.*)$", user, re.M).group(1)
    if "FATHER" in a + b:                                 # luôn chuộng bản cũ
        win = "B" if "ĐÃ SỬA" in a else "A"
    else:                                                 # chuộng bản đã sửa
        win = "A" if "ĐÃ SỬA" in a else "B"
    return json.dumps({"ly_do": "câu văn tự nhiên hơn", "tot_hon": win}, ensure_ascii=False)


def respond(user, single):
    if user.startswith("Thẩm định đoạn trích sau."):
        return judge_respond(user)
    if "Bản nào tốt hơn?" in user:
        return compare_respond(user)
    if "Liệt kê MỌI nhân vật" in user:
        return ("Tên gốc | giới tính | tuổi / vai trò | quan hệ | gợi ý\n"
                "|---|---|---|---|---|\n"
                "Mara | nữ | thiếu nữ, con gái người gác hải đăng | con gái của người cha | nàng\n"
                "1. Mrs. Hale | nữ | bà hàng xóm trong làng | quen gia đình Mara | bà\n"
                "Mara | nữ | | em gái của người anh sắp về | \n")
    body = user.split("mỗi đoạn bắt đầu bằng số [n] như bản gốc.", 1)[1]
    items = re.findall(r"^\[(\d+)\] (.*?)(?=^\[\d+\] |\Z)", body, re.S | re.M)
    out = []
    skip_next = False
    for idx, (n, txt) in enumerate(items):
        txt = txt.rstrip("\n")
        if skip_next:
            skip_next = False
            continue
        if "NEVER" in txt:
            out.append(f"[{n}] ")
            continue
        t = fake_tr(txt)
        if "MERGE_ME" in txt and not single and idx + 1 < len(items):
            t = t + " " + fake_tr(items[idx + 1][1].strip())
            skip_next = True
        if "DROPTAG" in txt:                      # luôn làm mất thẻ -> phải ráp nới lỏng
            t = re.sub(r"</?g\d+>", "", t, count=2)
        if "TAGFLAKY" in txt and not single:      # chỉ mất thẻ khi dịch theo lô
            t = re.sub(r"</?g\d+>", "", t)
        if "CJK" in txt and not single:
            t += " 这是中文"
        if "BẢN DỊCH CẦN SỬA" in user:                  # lượt viết lại của review --fix
            t += " (ĐÃ SỬA)"
        out.append(f"[{n}] {t}")
    text = "\n".join(out)
    c = STATE["calls"]
    if c % 3 == 0:
        text = "<think>Hmm, cần dịch văn phong văn học…</think>\nĐây là bản dịch:\n" + text
    elif c % 3 == 1:
        text = "```\n" + text + "\n```"
    return text


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        if self.path.startswith("/api/generate"):      # lệnh nạp / giải phóng model
            if LOG:
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"path": self.path, "model": req.get("model"),
                                        "keep_alive": req.get("keep_alive")}) + "\n")
            data = json.dumps({"model": req.get("model"), "done": True,
                               "done_reason": "unload"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        STATE["calls"] += 1
        if STATE["calls"] == 1:                   # lỗi tạm thời ở lượt đầu
            self.send_response(500); self.end_headers(); self.wfile.write(b"boom"); return
        if req.get("model") == "missing-model":
            self.send_response(404); self.end_headers()
            self.wfile.write(b'{"error":"model \\"missing-model\\" not found"}'); return
        if req.get("model") == "nothink-model" and "think" in req:   # model không có chế độ thinking
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"\\"nothink-model\\" does not support thinking"}'); return
        if "response_format" in req:              # giả lập server không hỗ trợ JSON schema
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"response_format not supported"}'); return
        time.sleep(DELAY)
        user = req["messages"][-1]["content"]
        single = "Dịch 1 đoạn sau" in user
        text = respond(user, single)
        truncated = False
        if req.get("model") == "trunc-model" and not single and "Dịch " in user:
            # Giả lập chạm giới hạn token: cắt đôi đoạn cuối của lô, báo done_reason=length.
            rows = text.split("\n")
            idx = max(i for i, r in enumerate(rows) if r.startswith("["))
            rows[idx] = rows[idx][: max(4, len(rows[idx]) // 2)]
            text, truncated = "\n".join(rows[: idx + 1]), True
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"path": self.path, "single": single, "think": req.get("think"),
                                    "schema": "format" in req,
                                    "options": req.get("options"), "max_tokens": req.get("max_tokens"),
                                    "user": user[-400:]}, ensure_ascii=False) + "\n")
        if self.path.startswith("/api/chat"):
            resp = {"message": {"role": "assistant", "content": text}, "eval_count": len(text) // 3,
                    "eval_duration": int(0.05e9), "done_reason": "length" if truncated else "stop"}
        else:
            resp = {"choices": [{"message": {"role": "assistant", "content": text},
                                 "finish_reason": "length" if truncated else "stop"}],
                    "usage": {"completion_tokens": len(text) // 3}}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
