#!/usr/bin/env python3
"""Kiểm thử toàn bộ pipeline với server giả lập — không cần model thật, không cần mạng.

    python3 tests/run_tests.py

Tạo một EPUB mẫu nhiều trường hợp khó, chạy mọi lệnh của epub_translate.py
lên server giả lập (có cài lỗi cố ý) rồi kiểm tra từng chi tiết của EPUB đầu ra.
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "epub_translate.py")
TMP = tempfile.mkdtemp(prefix="epubtr_test_")
BOOK = os.path.join(TMP, "book.epub")
RESULTS = []


# ----------------------------------------------------------------- tiện ích

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(delay=0.0, log_path=None):
    port = free_port()
    cmd = [sys.executable, os.path.join(HERE, "mock_server.py"), str(port), str(delay)]
    if log_path:
        cmd.append(log_path)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc, f"http://127.0.0.1:{port}"
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("không khởi động được server giả lập")


def run(*args, expect=0):
    r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=180)
    if expect is not None and r.returncode != expect:
        raise AssertionError(f"exit {r.returncode} (mong đợi {expect})\n{r.stdout}\n{r.stderr}")
    return r.stdout + r.stderr


def test(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
            print(f"  ✓ {name}")
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, False, str(e)))
            print(f"  ✗ {name}\n      {str(e).strip()[:600]}")
            if os.environ.get("VERBOSE"):
                traceback.print_exc()
        return fn
    return deco


def read_zip(path, name):
    with zipfile.ZipFile(path) as z:
        return z.read(name)


def xml(path, name):
    return etree.fromstring(read_zip(path, name))


def lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def chat_lines(path):
    """Các lượt gọi model trong log (bỏ lệnh nạp / giải phóng model)."""
    return [x for x in lines(path) if x["path"] != "/api/generate"]


def unloads(path):
    return [x for x in lines(path) if x["path"] == "/api/generate"]


def X(node, expr):
    return node.xpath(expr)


# ----------------------------------------------------------------- chuẩn bị

print(f"Thư mục tạm: {TMP}")
subprocess.run([sys.executable, os.path.join(HERE, "make_sample.py"), BOOK], check=True,
               stdout=subprocess.DEVNULL)
server, URL = start_server()
LLM = ["--model", "test", "--base-url", URL]
CACHE = os.path.join(TMP, "book.vi-cache.jsonl")
OUT = os.path.join(TMP, "book.vi.epub")
print("Chạy kiểm thử:")


@test("info: đọc được cấu trúc sách, đếm đoạn và đoạn trùng")
def _():
    out = run("info", BOOK)
    assert "40 đoạn" in out, out
    assert "Cần dịch : 33 đoạn" in out, out


@test("check: gọi model, thử lại sau lỗi HTTP 500, thẻ định dạng giữ đúng")
def _():
    out = run("check", *LLM)
    assert "HTTP 500" in out and "✓ giữ đúng thẻ" in out, out


@test("Thinking tắt mặc định; model không hỗ trợ tham số think thì tự bỏ tham số")
def _():
    log_path = os.path.join(TMP, "think.log")
    srv, url = start_server(log_path=log_path)
    try:
        run("check", "--model", "t", "--base-url", url)
        assert all(x["think"] is False for x in chat_lines(log_path)), "phải gửi think=false"
        out = run("check", "--model", "nothink-model", "--base-url", url)
        assert "✓ giữ đúng thẻ" in out, out
        out = run("check", "--model", "t", "--base-url", url, "--think")
        assert chat_lines(log_path)[-1]["think"] is True
    finally:
        srv.terminate()


@test("Giải phóng model khi xong; --keep-loaded giữ 30 phút; không gửi lệnh nếu chưa nạp model")
def _():
    log_path = os.path.join(TMP, "release.log")
    srv, url = start_server(log_path=log_path)
    try:
        out = run("check", "--model", "t", "--base-url", url)
        assert "Đã giải phóng model t khỏi RAM" in out, out
        u = unloads(log_path)
        assert len(u) == 1 and u[0]["keep_alive"] == 0 and u[0]["model"] == "t", u
        out = run("check", "--model", "t", "--base-url", url, "--keep-loaded")
        assert unloads(log_path)[-1]["keep_alive"] == "30m", out
        n = len(unloads(log_path))
        run("check", "--model", "missing-model", "--base-url", url, expect=1)
        run("info", BOOK)
        assert len(unloads(log_path)) == n, "model không nạp được thì không có gì để giải phóng"
    finally:
        srv.terminate()


@test("Giới hạn token: tự tính đủ rộng, --max-tokens ghi đè, --think cộng ngân sách + ngữ cảnh 16K")
def _():
    log_path = os.path.join(TMP, "budget.log")
    srv, url = start_server(log_path=log_path)
    try:
        out = run("check", "--model", "t", "--base-url", url)
        opt = chat_lines(log_path)[-1]["options"]
        assert opt["num_predict"] >= 1024 and opt["num_ctx"] == 8192, opt
        assert "✓ không bị cắt" in out, out
        run("check", "--model", "t", "--base-url", url, "--max-tokens", "3000")
        assert chat_lines(log_path)[-1]["options"]["num_predict"] == 3000
        run("check", "--model", "t", "--base-url", url, "--think")
        opt = chat_lines(log_path)[-1]["options"]
        assert opt["num_ctx"] == 16384 and opt["num_predict"] >= 1024 + 4096, opt
        out = run("check", "--model", "t", "--base-url", url, "--max-tokens", "9000")
        assert "không vừa cửa sổ ngữ cảnh 8192" in out, out
        assert chat_lines(log_path)[-1]["options"]["num_predict"] < 8192
    finally:
        srv.terminate()


@test("Câu trả lời bị cắt vì chạm giới hạn: không lưu đoạn dở dang, dịch lại riêng")
def _():
    cache = os.path.join(TMP, "trunc.jsonl")
    out = run("translate", BOOK, "--model", "trunc-model", "--base-url", URL, "--cache", cache,
              "-o", os.path.join(TMP, "trunc.epub"), "--max-segments", "12")
    assert "bị cắt vì chạm giới hạn" in out, out
    recs = lines(cache)
    assert len(recs) >= 10, len(recs)
    TAG = re.compile(r"</?[gx]\d+/?>")
    for r in recs:
        en, vi = TAG.sub("", r["en"]), TAG.sub("", r["vi"])
        assert len(vi) >= 0.9 * len(en), ("đoạn dở dang bị lưu", en, vi)


@test("translate --max-segments 5: dịch thử một phần, chia sẻ bản dịch cho đoạn trùng")
def _():
    out = run("translate", BOOK, *LLM, "--max-segments", "5")
    assert len(lines(CACHE)) == 5, out
    assert "11/40 đoạn đã có bản dịch" in out, out


@test("translate: chạy tiếp phần còn lại, xử lý gộp đoạn / mất thẻ / lẫn chữ Trung / đoạn hỏng")
def _():
    out = run("translate", BOOK, *LLM)
    assert "39/40 đoạn đã có bản dịch" in out, out
    assert "1 đoạn mất một phần định dạng" in out, out
    assert len(lines(CACHE)) == 32, len(lines(CACHE))
    rep = open(os.path.join(TMP, "book.vi.report.txt"), encoding="utf-8").read()
    assert "[mất định dạng]" in rep and "DROPTAG" in rep, rep
    assert "[CHƯA DỊCH ĐƯỢC]" in rep and "NEVER" in rep, rep
    vis = {r["en"]: r["vi"] for r in lines(CACHE)}
    assert not any(re.search(r"[一-鿿]", v) for v in vis.values()), "còn chữ Trung"


@test("EPUB: mimetype đứng đầu và không nén")
def _():
    with zipfile.ZipFile(OUT) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"


@test("Chương 1: giữ id, chú thích, neo, ngắt trang, <br>, &nbsp;, lang=vi, không lặp đoạn gộp")
def _():
    raw = read_zip(OUT, "OEBPS/ch1.xhtml").decode("utf-8")
    root = etree.fromstring(raw.encode())
    assert root.get("lang") == "vi"
    h1 = X(root, "//*[local-name()='h1']")[0]
    assert h1.get("id") == "ch1-title" and h1.text == "CHAPTER ONE: THE STORM"
    noteref = X(root, "//*[local-name()='a'][@href='#n1']")
    assert noteref and "".join(noteref[0].itertext()) == "1", "mất liên kết chú thích"
    assert X(root, "//*[@id='page2']"), "mất mốc ngắt trang"
    assert X(root, "//*[local-name()='a'][@id='p5']"), "mất neo p5"
    assert len(X(root, "//*[local-name()='br']")) == 2
    assert " " in raw, "mất &nbsp;"
    assert raw.count("HER FATHER SIGHED") == 1, "đoạn bị gộp/lặp"
    assert "SHE DID NOT MOVE" in raw
    span = X(root, "//*[@class='epigraph']/*[local-name()='span']")
    assert span and "SOME WINDS CARRY" in "".join(span[0].itertext()), "chữ rời trong div chưa được dịch"


@test("Chương 2: giữ DOCTYPE, khoảng trắng trong <pre>, đoạn hỏng giữ nguyên tiếng Anh")
def _():
    raw = read_zip(OUT, "OEBPS/ch2.xhtml").decode("utf-8")
    assert "<!DOCTYPE html PUBLIC" in raw
    assert "THE TIDE COMES IN,\n    THE TIDE GOES OUT," in raw
    assert "NEVER This paragraph is designed" in raw
    assert "<em>HOT</em>" in raw, "ráp nới lỏng phải giữ được thẻ còn lại"


@test("Mục lục: nav giữ cấu trúc li > a, toc.ncx được dịch, mã định danh mới khớp nhau")
def _():
    nav = xml(OUT, "OEBPS/nav.xhtml")
    links = X(nav, "//*[local-name()='li']/*[local-name()='a']")
    assert [a.text for a in links][:2] == ["CHAPTER ONE: THE STORM", "THE LETTER"], [a.text for a in links]
    opf = xml(OUT, "OEBPS/content.opf")
    ident = X(opf, "//*[local-name()='identifier']")[0].text
    assert ident.startswith("urn:uuid:") and ident != "urn:uuid:3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    assert X(opf, "//*[local-name()='language']")[0].text == "vi"
    ncx = xml(OUT, "OEBPS/toc.ncx")
    assert X(ncx, "//*[local-name()='meta'][@name='dtb:uid']/@content")[0] == ident
    assert X(ncx, "//*[local-name()='text']")[1].text == "CHAPTER ONE: THE STORM"


@test("Mọi tệp XHTML đầu ra là XML hợp lệ (parse chặt)")
def _():
    strict = etree.XMLParser(recover=False)
    with zipfile.ZipFile(OUT) as z:
        for n in z.namelist():
            if n.endswith((".xhtml", ".opf", ".ncx")):
                etree.fromstring(z.read(n), strict)


@test("Ctrl+C giữa chừng rồi chạy lại: không mất tiến độ, giải phóng model, không dịch lại đoạn đã xong")
def _():
    log_path = os.path.join(TMP, "int.log")
    slow, url = start_server(delay=0.3, log_path=log_path)
    try:
        cache = os.path.join(TMP, "int.jsonl")
        cmd = [sys.executable, SCRIPT, "translate", BOOK, "--model", "t", "--base-url", url,
               "--cache", cache, "-o", os.path.join(TMP, "int.epub"), "--batch-max", "2"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(5)
        p.send_signal(signal.SIGINT)
        out, _ = p.communicate(timeout=30)
        assert p.returncode == 130, (p.returncode, out)
        assert "Đã giải phóng model t khỏi RAM" in out, out
        assert unloads(log_path)[-1]["keep_alive"] == 0
        done = len(lines(cache))
        assert 0 < done < 32, done
        out2 = run("translate", BOOK, "--model", "t", "--base-url", url, "--cache", cache,
                   "-o", os.path.join(TMP, "int.epub"), "--batch-max", "2")
        assert len(lines(cache)) == 32, (len(lines(cache)), out2)
    finally:
        slow.terminate()


@test("build --bilingual: gốc + bản dịch, không trùng id, li/td nối bản dịch bên trong")
def _():
    bi = os.path.join(TMP, "book.en-vi.epub")
    run("build", BOOK, "--bilingual", "--drop-fonts", "--title", "Bản song ngữ")
    root = xml(bi, "OEBPS/ch1.xhtml")
    twins = X(root, "//*[local-name()='p'][@lang='vi']")
    assert len(twins) >= 8, len(twins)
    ids = X(root, "//@id")
    assert len(ids) == len(set(ids)), "trùng id"
    ch2 = read_zip(bi, "OEBPS/ch2.xhtml").decode("utf-8")
    assert re.search(r"<li>Two loaves of bread<br/><span lang=\"vi\">TWO LOAVES OF BREAD</span></li>", ch2)
    assert "@font-face" not in read_zip(bi, "OEBPS/style.css").decode()
    opf = xml(bi, "OEBPS/content.opf")
    assert X(opf, "//*[local-name()='title']")[0].text == "Bản song ngữ"


@test("translate --redo: dịch lại riêng một chương dù đã có trong cache")
def _():
    out = run("translate", BOOK, *LLM, "--redo", "ch2")
    assert "lần này dịch 13" in out, out


@test("characters: tạo bản nháp bảng nhân vật, gộp trùng tên")
def _():
    gl = os.path.join(TMP, "glossary.md")
    run("characters", BOOK, *LLM, "-o", gl)
    text = open(gl, encoding="utf-8").read()
    assert text.count("**Mara**") == 1 and "**Mrs. Hale**" in text, text
    assert "em gái của người anh" in text, "chưa gộp thông tin từ dòng trùng"


@test("prompt: glossary được đưa vào, dòng mẫu chưa điền bị lọc")
def _():
    out = run("prompt", "--glossary", os.path.join(TMP, "glossary.md"))
    assert "BẢNG NHÂN VẬT" in out and "**Mara**" in out
    assert '"…"' not in out


@test("Báo lỗi rõ ràng khi sai tên model")
def _():
    out = run("translate", BOOK, "--model", "missing-model", "--base-url", URL,
              "--cache", os.path.join(TMP, "x.jsonl"), "-o", os.path.join(TMP, "x.epub"), expect=1)
    assert "ollama pull missing-model" in out, out


@test("prompt: có ví dụ văn phong; prompt --judge có đủ 6 tiêu chí và bảng nhân vật")
def _():
    out = run("prompt")
    assert "VÍ DỤ VỀ VĂN PHONG" in out and "Sát chữ (TRÁNH)" in out
    out = run("prompt", "--judge", "--glossary", os.path.join(TMP, "glossary.md"))
    for key in ("trung_thanh", "tu_nhien", "van_phong", "xung_ho", "loi_thoai", "thuat_ngu"):
        assert key in out, key
    assert "**Mara**" in out


# ----------------------------------------------------------------- thẩm định (review)

shutil.copy(CACHE, os.path.join(TMP, "rv.vi-cache.jsonl"))
RV_CACHE = os.path.join(TMP, "rv.vi-cache.jsonl")
RV_STORE = os.path.join(TMP, "rv.vi-review.jsonl")
RV_HTML = os.path.join(TMP, "rv.vi-review.html")
RV_LOG = os.path.join(TMP, "rv-mock.log")
rv_server, RV_URL = start_server(log_path=RV_LOG)
RV = ["--model", "t", "--base-url", RV_URL, "--cache", RV_CACHE]


def log_count():
    return len(open(RV_LOG, encoding="utf-8").readlines()) if os.path.exists(RV_LOG) else 0


@test("review: kiểm tra giám khảo 3/3, chấm đủ đoạn trích, đọc được JSON bẩn, gửi JSON schema")
def _():
    out = run("review", BOOK, *RV)
    assert "bắt được 3/3 lỗi cài sẵn, báo nhầm 0 → đáng tin" in out, out
    assert "Đã chấm 6/6 đoạn trích" in out, out
    recs = [r for r in lines(RV_STORE) if r["type"] == "chunk"]
    assert len(recs) == 6, len(recs)
    issues = [(i["c"], i["trich"]) for r in recs for i in r["issues"]]
    assert issues.count(("van_phong", "the storm")) == 2, issues
    assert issues.count(("xung_ho", "ông bố")) == 2, "tên tiêu chí hiển thị phải được quy về khoá"
    log = lines(RV_LOG)
    assert all(x["schema"] for x in log if x["path"] == "/api/chat"), "phải gửi format (JSON schema)"
    assert "Điểm tổng: 76/100 (Khá)" in out, out


@test("Báo cáo HTML: hợp lệ, không tải tài nguyên ngoài, đánh dấu trích dẫn, cảnh báo trích sai")
def _():
    from lxml import html as lhtml
    root = lhtml.parse(RV_HTML).getroot()
    assert not [v for v in root.xpath("//@src|//@href") if v.startswith(("http:", "https:", "//"))]
    assert len(root.xpath("//article[contains(@class,'issue')]")) == 4
    assert root.xpath("//mark/text()") == ["THE STORM"], root.xpath("//mark/text()")
    assert len(root.xpath("//p[@class='warn']")) == 3, "trích không khớp nguyên văn phải bị cảnh báo"
    assert len(root.xpath("//table//tbody/tr")) == 3, "mỗi chương một dòng"
    names = [e.text for e in root.xpath("//span[@class='crit-name']")]
    assert names == ["Trung thành", "Tự nhiên", "Văn phong văn học", "Xưng hô", "Lời thoại",
                     "Tên riêng & thuật ngữ"], names


@test("review chạy lại khi không có gì đổi: không gọi model lần nào (ngoài kiểm tra giám khảo)")
def _():
    n0 = log_count()
    out = run("review", BOOK, *RV, "--no-calibration")
    assert "cần chấm 0" in out, out
    assert log_count() == n0, log_count() - n0


@test("review --fix: thay đoạn thắng so sánh A/B, giữ đoạn thua, chấm lại, điểm trước/sau")
def _():
    out = run("review", BOOK, *RV, "--fix", "--no-calibration")
    assert out.count("✓ thay bản mới (2/2 phiếu)") == 2, out
    assert out.count("· giữ bản cũ (0/2 phiếu)") == 2, out
    assert "Chấm lại các đoạn trích đã sửa" in out and "cần chấm 2" in out, out
    assert "trước khi sửa: 76" in out, out
    vis = {r["key"]: r["vi"] for r in lines(RV_CACHE)}
    assert sum("ĐÃ SỬA" in v for v in vis.values()) == 2, "chỉ 2 đoạn được thay"
    assert not any("FATHER" in v and "ĐÃ SỬA" in v for v in vis.values())
    from lxml import html as lhtml
    root = lhtml.parse(RV_HTML).getroot()
    assert len(root.xpath("//article[contains(@class,'fix')]")) == 4
    assert root.xpath("//div[@class='before']"), "báo cáo phải hiện điểm trước khi sửa"


@test("review --fix lần hai: không viết lại đoạn đã thử, không gọi model")
def _():
    n0 = log_count()
    out = run("review", BOOK, *RV, "--fix", "--no-calibration")
    assert "bỏ qua 2 đoạn đã thử ở lần trước" in out, out
    assert log_count() == n0


@test("review --undo-fixes: trả bản dịch về như trước khi sửa")
def _():
    out = run("review", BOOK, "--cache", RV_CACHE, "--undo-fixes")
    assert "Đã hoàn tác 2 đoạn" in out, out
    from collections import OrderedDict
    vis = OrderedDict()
    for r in lines(RV_CACHE):
        vis[r["key"]] = r["vi"]
    assert not any("ĐÃ SỬA" in v for v in vis.values())
    out = run("review", BOOK, *RV, "--no-calibration")
    assert "Điểm tổng: 76/100" in out and "cần chấm 0" in out, out


@test("review với server không hỗ trợ JSON schema (backend openai): tự chuyển chế độ thường")
def _():
    cache = os.path.join(TMP, "oa.vi-cache.jsonl")
    shutil.copy(CACHE, cache)
    out = run("review", BOOK, "--model", "t", "--backend", "openai", "--base-url", RV_URL + "/v1",
              "--cache", cache)
    assert "không nhận ràng buộc JSON schema" in out, out
    assert "Đã chấm 6/6 đoạn trích" in out, out


@test("review báo rõ khi chưa có bản dịch")
def _():
    out = run("review", BOOK, *LLM, "--cache", os.path.join(TMP, "khong-co.jsonl"), expect=1)
    assert "Chạy lệnh `translate` trước" in out, out


rv_server.terminate()
server.terminate()
ok = sum(1 for _, passed, _ in RESULTS if passed)
print(f"\n{ok}/{len(RESULTS)} bài kiểm tra đạt.")
if ok == len(RESULTS):
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok == len(RESULTS) else 1)
