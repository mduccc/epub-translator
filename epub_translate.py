#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epub_translate.py - Dịch sách EPUB tiếng Anh sang tiếng Việt bằng LLM chạy trên máy.

Backend: Ollama (mặc định) hoặc mọi server tương thích OpenAI
         (LM Studio, mlx_lm.server, llama.cpp server).
Phụ thuộc duy nhất ngoài thư viện chuẩn: lxml (./setup_mac.sh cài vào .venv; script tự dùng
.venv nên chỉ cần gõ `python3`, không cần kích hoạt môi trường ảo).

Các lệnh:
  info        Xem cấu trúc sách, số đoạn, ước tính thời gian dịch
  check       Kiểm tra kết nối model, khả năng giữ định dạng, đo tốc độ
  characters  Quét đầu sách, tạo bản nháp bảng nhân vật / xưng hô
  prompt      In ra system prompt sẽ gửi cho model (để kiểm tra / chỉnh)
  translate   Dịch (ngắt giữa chừng thì chạy lại đúng lệnh để dịch tiếp)
  review      Chấm bản dịch theo 6 tiêu chí bằng chính model, xuất báo cáo HTML
              (--fix: viết lại đoạn bị chê, chỉ thay khi bản mới thắng so sánh A/B)
  build       Đóng gói lại EPUB từ cache, không gọi model (sau khi sửa tay)

Ví dụ:
  python3 epub_translate.py info sach.epub
  python3 epub_translate.py check --model gemma4:12b-it-qat
  python3 epub_translate.py characters sach.epub --model gemma4:12b-it-qat -o glossary.md
  python3 epub_translate.py translate sach.epub --model gemma4:12b-it-qat --glossary glossary.md --max-segments 40
  python3 epub_translate.py review sach.epub --model gemma4:12b-it-qat --glossary glossary.md --open
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html.entities
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

def _reexec_in_venv() -> None:
    """Chạy bằng `python3` hệ thống (không có lxml) mà cạnh script có .venv do setup_mac.sh
    tạo → tự chạy lại bằng Python trong .venv. Nhờ vậy không cần `source .venv/bin/activate`."""
    here = os.path.dirname(os.path.abspath(__file__))
    venv = os.path.join(here, ".venv")
    for py in (os.path.join(venv, "bin", "python3"), os.path.join(venv, "Scripts", "python.exe")):
        if (os.path.exists(py) and not os.environ.get("EPUBTR_REEXEC")
                and os.path.realpath(sys.prefix) != os.path.realpath(venv)):
            os.environ["EPUBTR_REEXEC"] = "1"          # chặn vòng lặp nếu .venv cũng thiếu lxml
            os.execv(py, [py, os.path.abspath(__file__)] + sys.argv[1:])


try:
    from lxml import etree
except ImportError:  # pragma: no cover
    _reexec_in_venv()
    sys.exit("Thiếu thư viện lxml. Chạy ./setup_mac.sh để cài, hoặc:  python3 -m pip install lxml")


# ---------------------------------------------------------------------------
# Hằng số
# ---------------------------------------------------------------------------

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
HTML_TYPES = {"application/xhtml+xml", "text/html"}

# Phần tử cấp khối: dùng để tìm "đoạn lá" (khối không chứa khối con).
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "caption", "center",
    "dd", "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hgroup", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
# Không dịch nội dung bên trong các phần tử này (giữ nguyên khối).
SKIP_TAGS = {
    "script", "style", "svg", "math", "head", "title", "code", "kbd", "samp",
    "var", "noscript", "template", "rt", "rp",
}
# Chế độ song ngữ: các khối này được nhân đôi thành khối anh em;
# khối khác (li, td, dt...) thì bản dịch được nối vào bên trong.
SIBLING_OK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "pre",
              "blockquote", "address", "center"}

WS_RE = re.compile(r"[ \t\r\n\f]+")            # KHÔNG gộp &nbsp; (\xa0)
TOKEN_RE = re.compile(r"<(/?)([gx])(\d+)\s*(/?)>")
CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
MARK_RE = re.compile(r"^[ \t>*_]*\[(\d+)\][*_]*[ \t]*:?[ \t]*", re.M)
FENCE_RE = re.compile(r"^```[A-Za-z]*[ \t]*$", re.M)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

DEFAULT_STYLE = """\
Bạn là dịch giả văn học chuyên nghiệp, dịch tiểu thuyết từ tiếng Anh sang tiếng Việt cho nhà xuất bản.

PHONG CÁCH
- Văn phong văn học: câu văn tự nhiên, uyển chuyển, giàu hình ảnh, đọc như văn viết bằng tiếng Việt chứ không như bản dịch.
- Trung thành với nguyên tác: đủ ý, không thêm, không bớt, không tóm tắt, không giải thích.
- Không dịch từng chữ. Được đảo trật tự, tách hoặc gộp mệnh đề để câu tiếng Việt xuôi tai, nhưng giữ nhịp và giọng của tác giả.
- Thành ngữ, ẩn dụ, lối nói đặc thù: chuyển sang cách diễn đạt tương đương, tự nhiên trong tiếng Việt.
- Lời thoại: dùng khẩu ngữ hợp với tuổi tác, tính cách, tầng lớp và quan hệ giữa các nhân vật.
- Đại từ nhân xưng (I / you / he / she / they) phải chọn theo quan hệ và sắc thái (tôi, ta, anh, em, chàng, nàng, hắn, gã, lão, bà, cô, cậu, nó...). Tuân thủ tuyệt đối bảng nhân vật nếu có. Trong lời kể, tránh lặp đại từ dày đặc; có thể thay bằng tên riêng.
- Giữ nguyên tên riêng tiếng Anh, trừ khi bảng thuật ngữ quy định khác. Giữ cách trình bày lời thoại (ngoặc kép, gạch đầu dòng) như bản gốc.

VÍ DỤ VỀ VĂN PHONG (học cách chuyển ý, không chép lại)
EN: It was raining cats and dogs, and he hadn't eaten a thing since morning.
Sát chữ (TRÁNH): Trời đang mưa như chó và mèo, và anh ấy đã không ăn một thứ gì kể từ buổi sáng.
Văn học (NÊN): Trời mưa như trút nước, mà từ sáng tới giờ anh vẫn chưa có gì bỏ bụng.

EN: The silence that followed was heavier than anything she had said.
Sát chữ (TRÁNH): Sự im lặng mà đã theo sau thì nặng nề hơn bất cứ thứ gì mà cô ấy đã nói.
Văn học (NÊN): Khoảng lặng sau đó còn nặng nề hơn mọi lời cô vừa nói.

EN: "Don't you dare touch that," the old woman snapped.
Sát chữ (TRÁNH): "Bạn đừng dám chạm vào cái đó," người phụ nữ già nói một cách cáu kỉnh.
Văn học (NÊN): "Đừng có mà động vào!" bà lão gắt.
"""

FORMAT_RULES = """\
ĐỊNH DẠNG ĐẦU RA (bắt buộc)
- Mỗi đoạn đầu vào có dạng "[n] nội dung". Trả về từng đoạn theo đúng thứ tự, mỗi đoạn một dòng, bắt đầu bằng đúng số [n] của nó. Không gộp, không tách, không bỏ đoạn nào.
- <g1>...</g1> là thẻ định dạng (in nghiêng, đậm, liên kết...); <x2/> là phần tử cố định (ảnh, ngắt dòng, chú thích...). Giữ nguyên mọi thẻ và số của nó, đặt vào vị trí tương ứng trong câu dịch. Không thêm thẻ mới.
- Chỉ trả về bản dịch. Không lời mở đầu, không ghi chú, không giải thích.

Ví dụ
Đầu vào:
[1] She had <g1>never</g1> seen the sea before.<x2/>
[2] "Come in," he said.
Đầu ra:
[1] Nàng <g1>chưa từng</g1> thấy biển bao giờ.<x2/>
[2] "Vào đi," chàng nói.
"""

CHAR_SYSTEM = "Bạn là biên tập viên văn học, chuyên lập hồ sơ nhân vật cho dịch giả."

CHAR_USER = """\
Liệt kê MỌI nhân vật có tên xuất hiện trong đoạn trích tiểu thuyết dưới đây.
Mỗi nhân vật một dòng, đúng mẫu (5 cột, ngăn bằng dấu |):
Tên gốc | giới tính | tuổi / vai trò | quan hệ với nhân vật khác | gợi ý lời kể gọi nhân vật này (vd: nàng, chàng, cô, anh, hắn, gã, lão, bà, cậu bé...)
Viết phần mô tả bằng tiếng Việt. Không viết gì khác ngoài các dòng đó.

ĐOẠN TRÍCH:
{chunk}
"""


def load_glossary(path: str | None) -> str:
    """Đọc glossary, bỏ chú thích <!-- --> và các dòng mẫu chưa điền ("…")."""
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        gl = f.read()
    gl = re.sub(r"<!--.*?-->", "", gl, flags=re.S)
    return "\n".join(l for l in gl.splitlines() if '"…"' not in l).strip()


def build_system_prompt(prompt_file: str | None, glossary_file: str | None) -> str:
    style = DEFAULT_STYLE
    if prompt_file:
        with open(prompt_file, encoding="utf-8") as f:
            style = f.read().strip() + "\n"
    parts = [style, FORMAT_RULES]
    gl = load_glossary(glossary_file)
    if gl:
        parts.append("BẢNG NHÂN VẬT & THUẬT NGỮ (bắt buộc tuân thủ)\n" + gl + "\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------

def log(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except OSError:          # Terminal đã đóng (SIGHUP): vẫn chạy tiếp phần dọn dẹp
        pass


def install_signal_handlers() -> None:
    """Đóng cửa sổ Terminal (SIGHUP) hay `kill` (SIGTERM) được xử lý như Ctrl+C:
    tiến độ được lưu và model được giải phóng khỏi RAM."""
    def handler(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handler)
            except (ValueError, OSError):
                pass


def fmt_dur(secs: float) -> str:
    secs = int(max(0, secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def lname(node) -> str | None:
    tag = node.tag
    if not isinstance(tag, str):          # comment, PI, entity
        return None
    if tag[:1] == "{":
        tag = tag.split("}", 1)[1]
    return tag.lower()


def norm_ws(s: str | None, keep_ws: bool) -> str:
    if not s:
        return ""
    return s if keep_ws else WS_RE.sub(" ", s)


def plain(s: str) -> str:
    """Bỏ các thẻ giữ chỗ <g1>, <x2/>."""
    return TOKEN_RE.sub("", s)


def has_words(s: str) -> bool:
    return re.search(r"[^\W\d_]", s) is not None


def visible_text(el) -> str:
    parts: list[str] = []

    def rec(e):
        if e.text:
            parts.append(e.text)
        for c in e:
            ln = lname(c)
            if ln is not None and ln not in SKIP_TAGS:
                rec(c)
            if c.tail:
                parts.append(c.tail)

    rec(el)
    return "".join(parts)


def append_text(parent, s: str | None) -> None:
    if not s:
        return
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + s
    else:
        parent.text = (parent.text or "") + s


def span_tag(ref) -> str:
    ns = etree.QName(ref).namespace
    return "{%s}span" % ns if ns else "span"


def br_tag(ref) -> str:
    ns = etree.QName(ref).namespace
    return "{%s}br" % ns if ns else "br"


def empty_copy(el):
    """Bản sao nông: giữ tag + thuộc tính + namespace, bỏ nội dung."""
    c = copy.deepcopy(el)
    c.text = None
    c.tail = None
    for ch in list(c):
        c.remove(ch)
    return c


def strip_ids(el) -> None:
    for e in el.iter():
        if isinstance(e.tag, str):
            e.attrib.pop("id", None)
            e.attrib.pop(XML_ID, None)


def move_content(src, dst) -> None:
    dst.text = src.text
    for c in list(src):
        dst.append(c)


def clear_content(el) -> None:
    el.text = None
    for c in list(el):
        el.remove(c)


# ---------------------------------------------------------------------------
# Đọc / ghi XML
# ---------------------------------------------------------------------------

_XML_SAFE = {"amp", "lt", "gt", "quot", "apos"}
_ENT_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_PARSER = etree.XMLParser(recover=True, resolve_entities=False, no_network=True,
                          huge_tree=True, load_dtd=False)


def _fix_entities(text: str) -> str:
    """&nbsp; &mdash;... -> tham chiếu số, để parser XML không làm rơi ký tự."""
    def rep(m):
        name = m.group(1)
        if name in _XML_SAFE:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        return "&#%d;" % cp if cp else m.group(0)
    return _ENT_RE.sub(rep, text)


def _decode_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    m = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9_.\-]+)[\"']", raw)
    enc = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def read_xml(path: str):
    with open(path, "rb") as f:
        text = _decode_bytes(f.read())
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    root = etree.fromstring(_fix_entities(text).encode("utf-8"), _PARSER)
    if root is None:
        raise ValueError(f"Không đọc được XML: {path}")
    return root.getroottree()


def write_xml(tree, path: str) -> None:
    data = etree.tostring(tree, xml_declaration=True, encoding="utf-8")
    with open(path, "wb") as f:
        f.write(data)


def xp(node, expr: str):
    return node.xpath(expr)


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

class Book:
    def __init__(self, epub_path: str):
        if not os.path.isfile(epub_path):
            sys.exit(f"Không tìm thấy file: {epub_path}")
        self.src = epub_path
        self.dir = tempfile.mkdtemp(prefix="epubtr_")
        real_dir = os.path.realpath(self.dir)
        try:
            with zipfile.ZipFile(epub_path) as z:
                for info in z.infolist():
                    target = os.path.realpath(os.path.join(self.dir, info.filename))
                    if target != real_dir and not target.startswith(real_dir + os.sep):
                        continue                       # chặn zip-slip
                    z.extract(info, self.dir)
        except zipfile.BadZipFile:
            self.close()
            sys.exit("File không phải EPUB hợp lệ (không giải nén được).")

        self.encrypted = False
        enc_path = os.path.join(self.dir, "META-INF", "encryption.xml")
        if os.path.exists(enc_path):
            self.encrypted = True
            refs = xp(read_xml(enc_path), "//*[local-name()='CipherReference']/@URI")
            if any(r.lower().endswith((".xhtml", ".html", ".htm", ".xml")) for r in refs):
                self.close()
                sys.exit("Sách có DRM (nội dung bị mã hoá) nên không thể đọc. "
                         "Chỉ dịch được EPUB không DRM.")

        cont = read_xml(os.path.join(self.dir, "META-INF", "container.xml"))
        rf = xp(cont, "//*[local-name()='rootfile']")
        if not rf:
            self.close()
            sys.exit("EPUB thiếu khai báo rootfile trong META-INF/container.xml")
        self.opf_path = os.path.join(self.dir, urllib.parse.unquote(rf[0].get("full-path")))
        self.opf_dir = os.path.dirname(self.opf_path)
        self.opf = read_xml(self.opf_path)
        root = self.opf.getroot()

        self.items: dict[str, dict] = {}
        for it in xp(root, "//*[local-name()='manifest']/*[local-name()='item']"):
            href = it.get("href") or ""
            path = os.path.normpath(os.path.join(
                self.opf_dir, urllib.parse.unquote(href.split("#")[0])))
            self.items[it.get("id")] = {
                "href": href, "path": path,
                "type": (it.get("media-type") or "").lower(),
                "props": (it.get("properties") or "").split(),
            }

        self.spine_paths: list[str] = []
        ncx_id = None
        spine = xp(root, "//*[local-name()='spine']")
        if spine:
            ncx_id = spine[0].get("toc")
            for ref in xp(spine[0], "./*[local-name()='itemref']"):
                it = self.items.get(ref.get("idref"))
                if (it and it["type"] in HTML_TYPES and os.path.exists(it["path"])
                        and it["path"] not in self.spine_paths):
                    self.spine_paths.append(it["path"])

        self.nav_paths = [it["path"] for it in self.items.values()
                          if "nav" in it["props"] and os.path.exists(it["path"])]
        ncx = self.items.get(ncx_id) if ncx_id else None
        if not ncx:
            ncx = next((it for it in self.items.values()
                        if it["type"] == "application/x-dtbncx+xml"), None)
        self.ncx_path = ncx["path"] if ncx and os.path.exists(ncx["path"]) else None
        self.css_paths = [it["path"] for it in self.items.values()
                          if it["type"] == "text/css" and os.path.exists(it["path"])]

    def rel(self, path: str) -> str:
        return os.path.relpath(path, self.dir)

    def meta(self, name: str) -> str:
        r = xp(self.opf.getroot(), f"//*[local-name()='metadata']/*[local-name()='{name}']")
        return (r[0].text or "").strip() if r else ""

    def set_meta(self, name: str, value: str) -> None:
        r = xp(self.opf.getroot(), f"//*[local-name()='metadata']/*[local-name()='{name}']")
        if r:
            r[0].text = value

    def renew_identifier(self) -> str | None:
        """Đổi mã định danh để thư viện sách (Apple Books...) không coi bản dịch
        là cùng một cuốn với bản gốc. Trả về mã mới."""
        root = self.opf.getroot()
        uid_ref = root.get("unique-identifier")
        cands = xp(root, f"//*[local-name()='metadata']/*[local-name()='identifier'][@id='{uid_ref}']") \
            if uid_ref else []
        if not cands:
            return None
        el = cands[0]
        old = (el.text or "").strip()
        if old.lower().startswith("urn:uuid:"):
            new = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, old + "#vi"))
        else:
            new = (old or "book") + "-vi"
        el.text = new
        return new

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tách đoạn
# ---------------------------------------------------------------------------

class Segment:
    __slots__ = ("el", "src", "mapping", "keep_ws", "kind", "key", "doc", "nav")

    def __init__(self, el, src, mapping, keep_ws, kind, doc, nav=False):
        self.el = el
        self.src = src
        self.mapping = mapping
        self.keep_ws = keep_ws
        self.kind = kind          # "html" | "plain"
        self.doc = doc
        self.nav = nav
        self.key = hashlib.sha1(src.encode("utf-8")).hexdigest()


class Doc:
    def __init__(self, path, tree, segs, kind, nav=False):
        self.path = path
        self.tree = tree
        self.segs = segs
        self.kind = kind          # "html" | "ncx"
        self.nav = nav
        self.changed = False


def has_block_desc(el) -> bool:
    for d in el.iterdescendants():
        if lname(d) in BLOCK_TAGS:
            return True
    return False


def collect_roots(container, out: list) -> None:
    """Đi theo thứ tự đọc, gom các 'đoạn lá' vào out.

    Nếu một khối vừa chứa khối con vừa có chữ rời (mixed content), phần chữ rời
    được bọc vào <span> để thành một đoạn riêng."""
    items: list = []
    if container.text:
        items.append(container.text)
        container.text = None
    for c in list(container):
        tail = c.tail
        c.tail = None
        container.remove(c)
        items.append(c)
        if tail:
            items.append(tail)

    run: list = []

    def put_back(xs):
        for x in xs:
            if isinstance(x, str):
                append_text(container, x)
            else:
                container.append(x)

    def flush():
        if not run:
            return
        txt = "".join(x if isinstance(x, str) else
                      ("" if lname(x) in SKIP_TAGS or lname(x) is None else visible_text(x))
                      for x in run)
        if has_words(txt):
            elems = [x for x in run if not isinstance(x, str)]
            loose = [x for x in run if isinstance(x, str) and x.strip()]
            if (len(elems) == 1 and not loose and lname(elems[0]) is not None
                    and lname(elems[0]) not in SKIP_TAGS):
                put_back(run)                 # dùng luôn phần tử inline làm gốc
                out.append(elems[0])
            else:
                span = container.makeelement(span_tag(container), {})
                for x in run:
                    if isinstance(x, str):
                        append_text(span, x)
                    else:
                        span.append(x)
                container.append(span)
                out.append(span)
        else:
            put_back(run)
        run.clear()

    for x in items:
        if isinstance(x, str) or lname(x) not in BLOCK_TAGS:
            run.append(x)
            continue
        flush()
        container.append(x)
        if has_block_desc(x):
            collect_roots(x, out)
        elif has_words(visible_text(x)):
            out.append(x)
    flush()


def descend_wrapper(el):
    """<li><a>Chương 1</a></li>, <p><i>…</i></p>: lấy phần tử bên trong làm gốc
    để model khỏi phải giữ thẻ và để các đoạn giống nhau có cùng khoá cache."""
    while True:
        if (el.text or "").strip() or len(el) != 1:
            return el
        c = el[0]
        ln = lname(c)
        if ln is None or ln in SKIP_TAGS or (c.tail or "").strip():
            return el
        el = c


def encode(root, keep_ws: bool):
    """Biến nội dung một đoạn thành chuỗi có thẻ giữ chỗ <gN>…</gN>, <xN/>."""
    mapping: dict[str, dict] = {}
    counter = [0]
    parts: list[str] = []

    def enc(e, parent_key):
        parts.append(norm_ws(e.text, keep_ws))
        for c in e:
            counter[0] += 1
            n = counter[0]
            ln = lname(c)
            if ln is None or ln in SKIP_TAGS or not visible_text(c).strip():
                atom = copy.deepcopy(c)
                atom.tail = None
                mapping["x%d" % n] = {"atom": atom, "parent": parent_key}
                parts.append("<x%d/>" % n)
            else:
                full = copy.deepcopy(c)
                full.tail = None
                mapping["g%d" % n] = {"shell": empty_copy(c), "full": full,
                                      "parent": parent_key}
                parts.append("<g%d>" % n)
                enc(c, "g%d" % n)
                parts.append("</g%d>" % n)
            parts.append(norm_ws(c.tail, keep_ws))

    enc(root, None)
    s = "".join(parts)
    return (s if keep_ws else s.strip()), mapping


def decode(translated: str, mapping: dict, host, strict: bool = True):
    """Dựng lại cây từ chuỗi đã dịch. strict=True: sai thẻ thì trả None.
    strict=False: bỏ qua thẻ lỗi, khôi phục neo/chú thích bị mất."""
    tmp = host.makeelement(host.tag, {})
    stack: list = [(None, tmp)]
    used: set[str] = set()
    pos = 0
    for m in TOKEN_RE.finditer(translated):
        append_text(stack[-1][1], translated[pos:m.start()])
        pos = m.end()
        closing, kind, num, selfclose = m.groups()
        key = kind + num
        if key not in mapping or (key in used and not closing):
            if strict:
                return None
            continue
        if kind == "x":
            if closing:
                if strict:
                    return None
                continue
            used.add(key)
            stack[-1][1].append(copy.deepcopy(mapping[key]["atom"]))
        elif closing:
            if any(k == key for k, _ in stack[1:]):
                while stack[-1][0] != key:
                    if strict:
                        return None
                    stack.pop()
                stack.pop()
            elif strict:
                return None
        else:
            used.add(key)
            el = copy.deepcopy(mapping[key]["shell"])
            stack[-1][1].append(el)
            if not selfclose:
                stack.append((key, el))
    append_text(stack[-1][1], translated[pos:])
    missing = set(mapping) - used
    if strict and (len(stack) > 1 or missing):
        return None
    if missing:
        restored: set[str] = set()
        front, back = [], []

        def covered(k):
            p = mapping[k]["parent"]
            while p:
                if p in restored:
                    return True
                p = mapping[p]["parent"]
            return False

        for k in sorted(missing, key=lambda k: int(k[1:])):
            if covered(k):
                continue
            info = mapping[k]
            if k[0] == "x":
                if lname(info["atom"]) == "br":
                    continue
                front.append(copy.deepcopy(info["atom"]))
            elif not has_words(visible_text(info["full"])):
                back.append(copy.deepcopy(info["full"]))   # số chú thích, ký hiệu
                restored.add(k)
            elif info["shell"].get("id") is not None or info["shell"].get(XML_ID) is not None:
                front.append(copy.deepcopy(info["shell"]))  # giữ đích liên kết
        if front:
            lead, tmp.text = tmp.text, None
            for i, e in enumerate(front):
                tmp.insert(i, e)
            front[-1].tail = (front[-1].tail or "") + (lead or "")
        for e in back:
            tmp.append(e)
    return tmp


def extract_docs(book: Book) -> list[Doc]:
    docs: list[Doc] = []
    paths = list(book.spine_paths) + [p for p in book.nav_paths if p not in book.spine_paths]
    for path in paths:
        try:
            tree = read_xml(path)
        except Exception as e:  # noqa: BLE001
            log(f"  ! Bỏ qua {book.rel(path)}: {e}")
            continue
        is_nav = path in book.nav_paths
        body = xp(tree, "//*[local-name()='body']")
        roots: list = []
        if body:
            collect_roots(body[0], roots)
        segs = []
        for el in roots:
            el = descend_wrapper(el)
            keep_ws = lname(el) == "pre"
            src, mapping = encode(el, keep_ws)
            if has_words(plain(src)):
                segs.append(Segment(el, src, mapping, keep_ws, "html", book.rel(path), is_nav))
        docs.append(Doc(path, tree, segs, "html", is_nav))
    if book.ncx_path:
        tree = read_xml(book.ncx_path)
        segs = []
        for el in xp(tree, "//*[local-name()='navLabel' or local-name()='docTitle']"
                           "/*[local-name()='text']"):
            src = WS_RE.sub(" ", el.text or "").strip()
            if has_words(src):
                segs.append(Segment(el, src, {}, False, "plain", book.rel(book.ncx_path), True))
        docs.append(Doc(book.ncx_path, tree, segs, "ncx", True))
    return docs


# ---------------------------------------------------------------------------
# Cache (JSONL, ghi nối tiếp — dòng sau ghi đè dòng trước)
# ---------------------------------------------------------------------------

class Cache:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, str] = {}
        self.f = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue                  # dòng cuối dở dang khi bị ngắt
                    if isinstance(rec, dict) and "key" in rec and "vi" in rec:
                        self.data[rec["key"]] = rec["vi"]

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def put(self, seg: Segment, vi: str) -> None:
        self.put_raw(seg.key, seg.doc, seg.src, vi)

    def put_raw(self, key: str, doc: str, en: str, vi: str) -> None:
        self.data[key] = vi
        if self.f is None:
            self.f = open(self.path, "a", encoding="utf-8")
        self.f.write(json.dumps({"key": key, "doc": doc, "en": en, "vi": vi},
                                ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        if self.f:
            self.f.close()
            self.f = None


# ---------------------------------------------------------------------------
# Gọi model
# ---------------------------------------------------------------------------

class LLMError(Exception):
    pass


_LLMS: list = []          # mọi kết nối model đã tạo trong lần chạy này


def release_models(keep: bool = False) -> None:
    for llm in _LLMS:
        llm.release(keep)


class LLM:
    def __init__(self, args):
        self.backend = args.backend
        self.model = args.model
        default = "http://localhost:11434" if args.backend == "ollama" else "http://localhost:1234/v1"
        self.base = (args.base_url or default).rstrip("/")
        self.api_key = args.api_key or os.environ.get("OPENAI_API_KEY") or "local"
        # Thinking mặc định TẮT: với dịch thuật nó chậm gấp nhiều lần và hay "nghĩ" hết
        # giới hạn token trước khi kịp trả lời.
        self.think = bool(getattr(args, "think", False)) and not getattr(args, "no_think", False)
        self.think_param_ok = True     # server/model có nhận tham số think không
        # Giới hạn token đầu ra mỗi lượt: tự tính theo độ dài đoạn, hoặc --max-tokens.
        # Bật --think thì cộng thêm ngân sách cho phần suy nghĩ và mở rộng ngữ cảnh.
        self.max_tokens = int(getattr(args, "max_tokens", 0) or 0)
        self.think_budget = int(getattr(args, "think_budget", 0) or 4096) if self.think else 0
        self.num_ctx = args.num_ctx or (16384 if self.think else 8192)
        self.last_truncated = False    # lượt gọi gần nhất có bị cắt vì chạm giới hạn không
        self.last_budget = 0
        self.warned_ctx = False
        self.used = False              # đã nạp model lên server chưa (để biết có cần giải phóng)
        _LLMS.append(self)
        self.timeout = args.timeout
        self.schema_ok = True          # server có nhận ràng buộc JSON schema không
        host = urllib.parse.urlparse(self.base).hostname or ""
        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
            # Server trên máy: không đi qua proxy hệ thống.
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            self.opener = urllib.request.build_opener()

    def _post(self, url: str, body: dict) -> dict:
        payload = json.dumps(body).encode("utf-8")
        delay = 3
        err = ""
        for attempt in range(6):
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            })
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")[:300]
                if e.code in (400, 401, 403, 404):
                    hint = ""
                    if e.code == 404 and self.backend == "ollama":
                        hint = f"\n  → Kiểm tra tên model bằng `ollama list`, hoặc tải: ollama pull {self.model}"
                    raise LLMError(f"HTTP {e.code}: {msg}{hint}") from None
                err = f"HTTP {e.code}: {msg}"
            except (urllib.error.URLError, OSError, ValueError) as e:
                err = str(getattr(e, "reason", e))
            if attempt == 0 and "refused" in err.lower():
                log("  ! Không kết nối được tới server model. "
                    + ("Ollama đã chạy chưa? (mở app Ollama hoặc `ollama serve`)"
                       if self.backend == "ollama" else
                       "Server (LM Studio / mlx_lm.server) đã bật chưa?"))
            log(f"  ! Lỗi gọi model ({err}). Thử lại sau {delay}s…")
            time.sleep(delay)
            delay = min(delay * 2, 60)
        raise LLMError(f"Gọi model thất bại nhiều lần: {err}")

    def chat(self, system: str, user: str, max_tokens: int, temperature: float,
             schema: dict | None = None):
        """Trả về (text, số token sinh ra, số giây sinh, có_thinking).

        schema: ép model trả JSON đúng khuôn (structured output). Server không hỗ trợ
        (HTTP 400) thì tự chuyển sang chế độ thường cho các lượt sau."""
        use_schema = schema is not None and self.schema_ok
        budget = self.budget(max_tokens, len(system) + len(user))
        last: LLMError | None = None
        for _ in range(3):
            try:
                result = self._chat(system, user, budget, temperature,
                                    schema if use_schema else None)
                self.used = True       # model đã nằm trong RAM của server
                return result
            except KeyboardInterrupt:
                self.used = True       # ngắt giữa lượt gọi: model có thể đã được nạp
                raise
            except LLMError as e:
                last, msg = e, str(e)
                if not msg.startswith("HTTP 400"):
                    raise
                if self.backend == "ollama" and self.think_param_ok and "think" in msg.lower():
                    # Model không có chế độ thinking: bỏ hẳn tham số think.
                    self.think_param_ok = False
                    continue
                if use_schema:
                    self.schema_ok = use_schema = False
                    log("  ! Server không nhận ràng buộc JSON schema — chuyển sang chế độ thường.")
                    continue
                raise
        raise last  # type: ignore[misc]

    def release(self, keep: bool = False) -> None:
        """Giải phóng model khỏi RAM của Ollama (keep_alive = 0), hoặc giữ thêm 30 phút.

        Chỉ làm khi lượt chạy này đã dùng model: gửi yêu cầu cho model chưa nạp sẽ khiến
        Ollama nạp nó lên rồi lại gỡ xuống, tốn công vô ích."""
        if self.backend != "ollama" or not self.used:
            return
        self.used = False
        body = {"model": self.model, "keep_alive": "30m" if keep else 0}
        try:
            req = urllib.request.Request(self.base + "/api/generate",
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with self.opener.open(req, timeout=15) as r:
                r.read()
            log(f"Giữ model {self.model} trong RAM thêm 30 phút (--keep-loaded)." if keep else
                f"Đã giải phóng model {self.model} khỏi RAM.")
        except (OSError, ValueError, KeyboardInterrupt) as e:
            if isinstance(getattr(e, "reason", None), ConnectionRefusedError):
                return                 # server đã tắt → model cũng không còn trong RAM
            log(f"(Không gửi được lệnh giải phóng — Ollama sẽ tự gỡ {self.model} sau 5 phút. "
                f"Gỡ ngay: ollama stop {self.model})")

    def budget(self, needed: int, prompt_chars: int) -> int:
        """Số token đầu ra tối đa cho một lượt gọi.

        needed: ước tính của nơi gọi (đã có dư); --max-tokens ghi đè ước tính này.
        Với Ollama, prompt + đầu ra phải nằm gọn trong num_ctx, nếu không model bị cắt ngang
        khi ngữ cảnh đầy — nên giới hạn được kẹp lại và có cảnh báo."""
        n = (self.max_tokens or needed) + self.think_budget
        if self.backend == "ollama":
            room = self.num_ctx - prompt_chars // 3 - 256   # ~3 ký tự/token, ước tính dè dặt
            if n > room:
                if not self.warned_ctx:
                    self.warned_ctx = True
                    log(f"  ! Giới hạn {n} token đầu ra không vừa cửa sổ ngữ cảnh {self.num_ctx}; "
                        f"kẹp còn {max(512, room)}. Tăng --num-ctx (vd 16384) nếu câu trả lời bị cắt.")
                n = max(512, room)
        self.last_budget = n
        return n

    def think_hint(self) -> str:
        """Lời khuyên khi phát hiện model vẫn đang 'suy nghĩ'."""
        if self.backend == "ollama" and self.think:
            return "đang bật vì bạn dùng --think. Bỏ --think để tắt."
        if self.backend == "ollama":
            return ("model vẫn suy nghĩ dù script đã gửi think=false. Cập nhật Ollama "
                    "(brew upgrade --cask ollama) hoặc dùng bản model không-thinking.")
        return ("server không cho script tắt được. Trong LM Studio: tắt Thinking/Reasoning "
                "trong cài đặt model, hoặc dùng bản Instruct không-thinking.")

    def _chat(self, system, user, max_tokens, temperature, schema):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t0 = time.time()
        if self.backend == "ollama":
            # keep_alive: giữ model trong RAM giữa các lượt gọi. Thoát bình thường thì script
            # giải phóng ngay (release); bị tắt đột ngột thì Ollama tự giải phóng sau 5 phút.
            body = {"model": self.model, "messages": msgs, "stream": False, "keep_alive": "5m",
                    "options": {"temperature": temperature, "num_ctx": self.num_ctx,
                                "num_predict": max_tokens}}
            if self.think_param_ok:
                body["think"] = self.think
            if schema is not None:
                body["format"] = schema
            data = self._post(self.base + "/api/chat", body)
            msg = data.get("message") or {}
            text = msg.get("content") or ""
            thinking = bool(msg.get("thinking")) or "<think>" in text
            self.last_truncated = data.get("done_reason") == "length"
            ntok = int(data.get("eval_count") or 0)
            secs = (data.get("eval_duration") or 0) / 1e9 or (time.time() - t0)
        else:
            body = {"model": self.model, "messages": msgs, "temperature": temperature,
                    "max_tokens": max_tokens, "stream": False}
            if schema is not None:
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "result", "schema": schema}}
            data = self._post(self.base + "/chat/completions", body)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = msg.get("content") or ""
            self.last_truncated = choice.get("finish_reason") == "length"
            thinking = bool(msg.get("reasoning_content") or msg.get("reasoning")) or "<think>" in text
            ntok = int((data.get("usage") or {}).get("completion_tokens") or 0)
            secs = time.time() - t0
        if not ntok:
            ntok = max(1, len(text) // 3)
        return text, ntok, max(secs, 1e-6), thinking


def strip_think(t: str) -> str:
    t = THINK_RE.sub("", t)
    if "</think>" in t:
        t = t.rsplit("</think>", 1)[1]
    if "<think>" in t:
        t = t.split("<think>", 1)[0]
    return t.strip()


def parse_numbered(text: str, k: int, keep_ws: list[bool]) -> dict[int, str]:
    text = FENCE_RE.sub("", strip_think(text))
    ms = list(MARK_RE.finditer(text))
    out: dict[int, str] = {}
    for i, m in enumerate(ms):
        n = int(m.group(1))
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        body = text[m.end():end]
        if 1 <= n <= k and n not in out:
            body = body.strip("\n") if keep_ws[n - 1] else WS_RE.sub(" ", body).strip()
            if body:
                out[n] = body
    if not ms and k == 1 and text.strip():
        out[1] = text.strip("\n") if keep_ws[0] else WS_RE.sub(" ", text).strip()
    return out


# ---------------------------------------------------------------------------
# Dịch
# ---------------------------------------------------------------------------

class Translator:
    def __init__(self, llm: LLM, system: str, args):
        self.llm = llm
        self.system = system
        self.args = args
        self.context: list[tuple[str, str]] = []
        self.calls = 0
        self.out_tokens = 0
        self.gen_secs = 0.0
        self.retried = 0
        self.failed: list[Segment] = []
        self.warned_think = False
        self.truncated = 0
        self.warned_trunc = False

    def _user_msg(self, segs: list[Segment], preface: str = "") -> str:
        lines: list[str] = [preface] if preface else []
        if self.context:
            lines.append("ĐOẠN NGAY TRƯỚC (chỉ để nắm mạch truyện và cách xưng hô, KHÔNG dịch lại):")
            for en, vi in self.context:
                lines.append("EN: " + en)
                lines.append("VI: " + vi)
            lines.append("")
        lines.append(f"Dịch {len(segs)} đoạn sau. Trả về đúng {len(segs)} đoạn, "
                     f"mỗi đoạn bắt đầu bằng số [n] như bản gốc.")
        lines.append("")
        for i, s in enumerate(segs, 1):
            lines.append(f"[{i}] {s.src}")
        return "\n".join(lines)

    def _ask(self, segs: list[Segment], temperature: float, preface: str = "") -> dict[int, str]:
        src_chars = sum(len(s.src) for s in segs)
        # Tiếng Việt tốn ~0,3–0,5 token/ký tự gốc; 1,5 là dư nhiều để không bao giờ bị cắt.
        max_tokens = max(1024, int(src_chars * 1.5) + 512)
        text, ntok, secs, thinking = self.llm.chat(self.system, self._user_msg(segs, preface),
                                                   max_tokens, temperature)
        self.calls += 1
        self.out_tokens += ntok
        self.gen_secs += secs
        got = parse_numbered(text, len(segs), [s.keep_ws for s in segs])
        if self.llm.last_truncated:
            # Chạm giới hạn token: đoạn cuối có thể bị cắt giữa câu → bỏ, để dịch lại riêng.
            self.truncated += 1
            if got:
                got.pop(max(got))
            if not self.warned_trunc:
                self.warned_trunc = True
                log(f"  ! Câu trả lời bị cắt vì chạm giới hạn {self.llm.last_budget} token — đoạn "
                    "dở dang sẽ được dịch lại. Gặp thường xuyên thì tăng --max-tokens"
                    + (" hoặc bỏ --think." if self.llm.think else "."))
        if thinking and not self.warned_think:
            self.warned_think = True
            log("  ! Model đang suy nghĩ (thinking) trước khi dịch nên chậm gấp nhiều lần: "
                + self.llm.think_hint())
        return got

    @staticmethod
    def check(seg: Segment, vi: str):
        """Trả về (lý do lỗi hoặc None, thẻ có khớp không)."""
        p_src, p_vi = plain(seg.src).strip(), plain(vi).strip()
        if not p_vi:
            return "rỗng", False
        if CJK_RE.search(p_vi) and not CJK_RE.search(p_src):
            return "lẫn chữ Trung/Nhật/Hàn", False
        if len(p_src) > 60:
            ratio = len(p_vi) / len(p_src)
            if ratio < 0.4 or ratio > 3.5:
                return "độ dài bất thường", False
        if len(re.findall(r"[A-Za-z]{2,}", p_src)) >= 5 and p_vi == p_src:
            return "chưa được dịch", False
        if seg.kind != "html":
            return None, True
        return None, decode(vi, seg.mapping, seg.el, strict=True) is not None

    def translate(self, segs: list[Segment]) -> dict[int, str]:
        """Dịch một lô. Trả về {chỉ số (1-based): bản dịch}."""
        temp = self.args.temperature
        k = len(segs)
        got = self._ask(segs, temp)
        results: dict[int, str] = {}
        cand: dict[int, tuple[str, bool]] = {}
        redo: set[int] = set()
        for i, seg in enumerate(segs, 1):
            vi = got.get(i)
            if vi is None:
                redo.update((i - 1, i, i + 1))   # đoạn mất số: có thể bị gộp với hàng xóm
                continue
            reason, tags_ok = self.check(seg, vi)
            if reason is None:
                cand[i] = (vi, tags_ok)
                if tags_ok:
                    results[i] = vi
                    continue
            redo.add(i)
        for i in sorted(j for j in redo if 1 <= j <= k):
            results.pop(i, None)
            seg = segs[i - 1]
            best = cand.get(i)
            for attempt in range(self.args.retries):
                self.retried += 1
                vi = self._ask([seg], min(temp + 0.15 * (attempt + 1), 1.0)).get(1)
                if vi is None:
                    continue
                reason, tags_ok = self.check(seg, vi)
                if reason is None and tags_ok:
                    results[i] = vi
                    break
                if reason is None and (best is None or not best[1]):
                    best = (vi, tags_ok)
            else:
                if best is not None:
                    results[i] = best[0]           # chấp nhận, ráp thẻ kiểu nới lỏng
                else:
                    self.failed.append(seg)
        accepted = [(segs[i - 1], results[i]) for i in sorted(results)]
        if self.args.context > 0 and accepted:
            self.context = [(plain(s.src)[:500], plain(v)[:500])
                            for s, v in accepted[-self.args.context:]]
        return results


def make_batches(segs: list[Segment], max_chars: int, max_items: int) -> list[list[Segment]]:
    batches: list[list[Segment]] = []
    cur: list[Segment] = []
    size = 0
    for s in segs:
        n = len(s.src)
        if cur and (s.doc != cur[0].doc or size + n > max_chars or len(cur) >= max_items):
            batches.append(cur)
            cur, size = [], 0
        if s.keep_ws or n >= max_chars:
            batches.append([s])
            continue
        cur.append(s)
        size += n
    if cur:
        batches.append(cur)
    return batches


# ---------------------------------------------------------------------------
# Ráp bản dịch & đóng gói
# ---------------------------------------------------------------------------

def apply_html(seg: Segment, vi: str, bilingual: bool) -> bool:
    """Gắn bản dịch vào cây. Trả về True nếu phải ráp thẻ kiểu nới lỏng."""
    tmp = decode(vi, seg.mapping, seg.el, strict=True)
    soft = tmp is None
    if soft:
        tmp = decode(vi, seg.mapping, seg.el, strict=False)
    el = seg.el
    if not bilingual:
        clear_content(el)
        move_content(tmp, el)
        return soft
    strip_ids(tmp)
    if lname(el) in SIBLING_OK and el.getparent() is not None:
        twin = empty_copy(el)
        strip_ids(twin)
        twin.set("lang", "vi")
        move_content(tmp, twin)
        parent = el.getparent()
        parent.insert(parent.index(el) + 1, twin)
        twin.tail = el.tail
        el.tail = "\n"
    else:
        sp = el.makeelement(span_tag(el), {"lang": "vi"})
        move_content(tmp, sp)
        el.append(el.makeelement(br_tag(el), {}))
        el.append(sp)
    return soft


def drop_font_faces(paths: list[str]) -> int:
    n = 0
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            css = f.read()
        new, k = re.subn(r"@font-face\s*\{[^}]*\}", "", css)
        if k:
            n += k
            with open(p, "w", encoding="utf-8") as f:
                f.write(new)
    return n


def write_epub(book: Book, out: str) -> None:
    mt = os.path.join(book.dir, "mimetype")
    if not os.path.exists(mt):
        with open(mt, "w") as f:
            f.write("application/epub+zip")
    tmp_out = out + ".part"
    with zipfile.ZipFile(tmp_out, "w") as z:
        z.write(mt, "mimetype", compress_type=zipfile.ZIP_STORED)
        for root, dirs, files in os.walk(book.dir):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(root, name)
                arc = os.path.relpath(full, book.dir).replace(os.sep, "/")
                if arc == "mimetype":
                    continue
                z.write(full, arc, compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp_out, out)


def build_output(book: Book, docs: list[Doc], cache: Cache, out: str, args,
                 failed: list[Segment] | None = None) -> dict:
    stats = {"done": 0, "soft": 0, "missing": 0}
    soft_list: list[Segment] = []
    for d in docs:
        bil = args.bilingual and not d.nav
        for s in d.segs:
            vi = cache.get(s.key)
            if vi is None:
                stats["missing"] += 1
                continue
            stats["done"] += 1
            d.changed = True
            if s.kind == "plain":
                s.el.text = plain(vi).strip()
            elif apply_html(s, vi, bil):
                stats["soft"] += 1
                soft_list.append(s)
        if not d.changed:
            continue
        root = d.tree.getroot()
        if d.kind == "html" and not args.bilingual:
            root.set("lang", "vi")
            root.set(XML_LANG, "vi")
        etree.cleanup_namespaces(root)
        write_xml(d.tree, d.path)

    if not args.bilingual:
        book.set_meta("language", "vi")
        if book.opf.getroot().get(XML_LANG):
            book.opf.getroot().set(XML_LANG, "vi")
    if args.title:
        book.set_meta("title", args.title)
    if not args.keep_id and not book.encrypted:
        new_id = book.renew_identifier()
        if new_id and book.ncx_path:
            ncx_doc = next((d for d in docs if d.kind == "ncx"), None)
            ncx_tree = ncx_doc.tree if ncx_doc else read_xml(book.ncx_path)
            for m in xp(ncx_tree, "//*[local-name()='meta'][@name='dtb:uid']"):
                m.set("content", new_id)
            write_xml(ncx_tree, book.ncx_path)
    write_xml(book.opf, book.opf_path)
    if args.drop_fonts:
        n = drop_font_faces(book.css_paths)
        log(f"Đã bỏ {n} khai báo @font-face (font nhúng thường thiếu dấu tiếng Việt).")
    write_epub(book, out)

    report = os.path.splitext(out)[0] + ".report.txt"
    failed = failed or []
    if soft_list or failed:
        with open(report, "w", encoding="utf-8") as f:
            f.write("Các đoạn nên soát lại\n=====================\n\n")
            for s in soft_list:
                f.write(f"[mất định dạng] {s.doc}\n  EN: {plain(s.src)[:200]}\n"
                        f"  VI: {plain(cache.get(s.key) or '')[:200]}\n\n")
            for s in failed:
                f.write(f"[CHƯA DỊCH ĐƯỢC] {s.doc}\n  EN: {plain(s.src)[:200]}\n\n")
        stats["report"] = report
    return stats


# ---------------------------------------------------------------------------
# Thẩm định bản dịch (review) — chính model dịch đóng vai biên tập viên
# ---------------------------------------------------------------------------

REVIEW_VERSION = 1

# (khoá JSON, tên hiển thị, định nghĩa, được phép "-" = không áp dụng)
CRITERIA = [
    ("trung_thanh", "Trung thành",
     "Đủ ý, đúng nghĩa; không thêm, không bớt, không hiểu sai.", False),
    ("tu_nhien", "Tự nhiên",
     "Đọc như văn viết bằng tiếng Việt; không mang cấu trúc câu tiếng Anh, không \"văn máy\".", False),
    ("van_phong", "Văn phong văn học",
     "Giàu hình ảnh, giữ nhịp và giọng tác giả; thành ngữ, ẩn dụ được chuyển tương đương, "
     "không dịch nghĩa đen.", False),
    ("xung_ho", "Xưng hô",
     "Đại từ nhân xưng hợp quan hệ và sắc thái, đúng bảng nhân vật, nhất quán.", True),
    ("loi_thoai", "Lời thoại",
     "Lời nhân vật tự nhiên, hợp tuổi tác, tính cách, hoàn cảnh.", True),
    ("thuat_ngu", "Tên riêng & thuật ngữ",
     "Tên riêng, địa danh, thuật ngữ đúng bảng và nhất quán.", True),
]
CRIT_KEYS = [c[0] for c in CRITERIA]
CRIT_NAME = {c[0]: c[1] for c in CRITERIA}
CRIT_SHORT = {"trung_thanh": "Trung thành", "tu_nhien": "Tự nhiên", "van_phong": "Văn phong",
              "xung_ho": "Xưng hô", "loi_thoai": "Thoại", "thuat_ngu": "Thuật ngữ"}
NA_WHAT = {"xung_ho": "đại từ nhân xưng", "loi_thoai": "lời thoại",
           "thuat_ngu": "tên riêng hay thuật ngữ"}
GRADES = ("tốt", "tạm", "kém")

JUDGE_INTRO = """\
Bạn là biên tập viên văn học nhiều kinh nghiệm, thẩm định bản dịch tiểu thuyết Anh → Việt của một dịch giả trước khi đưa in.
Hãy khắt khe và cụ thể. Chỉ chấm "tốt" khi thật sự không còn gì đáng sửa. Lỗi hay gặp ở bản dịch: dịch sát từng chữ; giữ cấu trúc câu tiếng Anh; lạm dụng "một", "đã", "thì", "mà", "bởi", "được", "nó"; đại từ sai quan hệ; thiếu hoặc thừa ý; thành ngữ dịch nghĩa đen; lời thoại cứng như văn viết.
"""

JUDGE_HOW = """\
MỨC CHẤM: "tốt" = không có gì đáng sửa · "tạm" = đọc được nhưng có chỗ nên sửa · "kém" = sai nghĩa hoặc gượng rõ rệt · "-" = không áp dụng.

CÁCH LÀM
1. Đọc kỹ từng đoạn [n]: EN là bản gốc, VI là bản dịch.
2. Ghi MỌI vấn đề cụ thể vào "van_de": số đoạn n, tiêu chí, trích NGUYÊN VĂN cụm từ tiếng Việt có vấn đề (ngắn, chép đúng từng chữ), gợi ý sửa ngắn gọn. Không có vấn đề thì để danh sách rỗng.
3. Chấm mỗi tiêu chí MỘT mức chung cho cả đoạn trích, nhất quán với các vấn đề đã ghi.

ĐẦU RA: chỉ một đối tượng JSON, không viết gì khác. Ví dụ:
{"van_de": [{"n": 2, "tieu_chi": "tu_nhien", "trich": "một cách nhanh chóng", "goi_y": "viết gọn: \\"vội vã\\""}], "diem": {"trung_thanh": "tốt", "tu_nhien": "tạm", "van_phong": "tạm", "xung_ho": "tốt", "loi_thoai": "-", "thuat_ngu": "tốt"}}
"""

COMPARE_HOW = """\
So sánh hai bản dịch tiếng Việt (A và B) của cùng một đoạn văn tiếng Anh, xét chung các tiêu chí: trung thành, tự nhiên, văn phong văn học, xưng hô, lời thoại, thuật ngữ. Không thiên vị theo thứ tự trình bày.
ĐẦU RA: chỉ một đối tượng JSON: {"ly_do": "một câu ngắn", "tot_hon": "A"} — "tot_hon" là "A", "B" hoặc "ngang".
"""

# Bộ kiểm tra giám khảo: 4 đoạn đã biết trước đáp án (1 đoạn tốt, 3 đoạn cài lỗi).
CALIB_GLOSSARY = ('- **Anna** — nữ, 17 tuổi. **Grandma** là bà ngoại của Anna. '
                  'Anna xưng "cháu", gọi "bà"; bà xưng "bà", gọi Anna là "cháu".')
CALIB_CASES = [
    ("Bản dịch tốt (không được báo lỗi)",
     "The kettle began to sing, and the kitchen filled with the smell of ginger and rain.",
     "Ấm nước bắt đầu reo, và gian bếp ngập mùi gừng lẫn mùi mưa.", set()),
    ("Dịch sát chữ, văn máy",
     "He was not a man who gave up easily, and the sea had taught him patience.",
     "Anh ấy đã không phải là một người đàn ông mà đã từ bỏ một cách dễ dàng, "
     "và biển cả đã dạy cho anh ấy sự kiên nhẫn.", {"tu_nhien", "van_phong"}),
    ("Sai xưng hô so với bảng nhân vật",
     "\"Grandma, can I come in? Are you still awake?\" Anna whispered.",
     "\"Bà ơi, tôi vào được không? Bà vẫn còn thức à?\" Anna thì thầm.", {"xung_ho", "loi_thoai"}),
    ("Thiếu ý",
     "She folded the letter twice, slipped it into her coat, and walked out into the snow "
     "without a word.",
     "Cô gấp lá thư lại rồi bước ra ngoài trời tuyết.", {"trung_thanh"}),
]
CALIB_STATUS = {"caught": "bắt được", "partial": "phát hiện nhưng sai tiêu chí",
                "missed": "bỏ sót", "ok": "không báo lỗi — đúng", "false_alarm": "báo nhầm"}


def build_judge_system(glossary: str) -> str:
    lines = [JUDGE_INTRO, "TIÊU CHÍ"]
    for key, name, desc, na in CRITERIA:
        extra = f' Dùng "-" nếu đoạn trích không có {NA_WHAT[key]}.' if na else ""
        lines.append(f"- {key} ({name}): {desc}{extra}")
    lines += ["", JUDGE_HOW]
    if glossary:
        lines.append("BẢNG NHÂN VẬT & THUẬT NGỮ (căn cứ để chấm xưng hô và thuật ngữ)\n"
                     + glossary + "\n")
    return "\n".join(lines)


def build_compare_system(glossary: str) -> str:
    s = JUDGE_INTRO + "\n" + COMPARE_HOW
    if glossary:
        s += "\nBẢNG NHÂN VẬT & THUẬT NGỮ\n" + glossary + "\n"
    return s


def judge_schema(k: int) -> dict:
    g = {"type": "string", "enum": list(GRADES)}
    g_na = {"type": "string", "enum": list(GRADES) + ["-"]}
    return {
        "type": "object",
        "properties": {
            # Ghi vấn đề TRƯỚC rồi mới chấm: điểm bám vào lỗi đã tìm ra.
            "van_de": {"type": "array", "maxItems": max(4, 2 * k), "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"},
                               "tieu_chi": {"type": "string", "enum": CRIT_KEYS},
                               "trich": {"type": "string"},
                               "goi_y": {"type": "string"}},
                "required": ["n", "tieu_chi", "trich", "goi_y"]}},
            "diem": {"type": "object",
                     "properties": {key: (g_na if na else g) for key, _, _, na in CRITERIA},
                     "required": CRIT_KEYS},
        },
        "required": ["van_de", "diem"],
    }


COMPARE_SCHEMA = {"type": "object",
                  "properties": {"ly_do": {"type": "string"},
                                 "tot_hon": {"type": "string", "enum": ["A", "B", "ngang"]}},
                  "required": ["ly_do", "tot_hon"]}


def fold(s) -> str:
    """'Văn phong văn học' -> 'van_phong_van_hoc' (bỏ dấu, chữ thường, nối bằng _)."""
    s = unicodedata.normalize("NFD", str(s)).replace("đ", "d").replace("Đ", "D")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


_GRADE_ALIAS = {"tot": "tốt", "good": "tốt", "dat": "tốt", "2": "tốt",
                "tam": "tạm", "kha": "tạm", "trung_binh": "tạm", "ok": "tạm", "fair": "tạm", "1": "tạm",
                "kem": "kém", "loi": "kém", "yeu": "kém", "bad": "kém", "poor": "kém", "0": "kém"}
_NA_ALIAS = {"", "na", "n_a", "none", "null", "khong", "khong_ap_dung", "khong_co"}
_CRIT_ALIAS = {**{fold(k): k for k in CRIT_KEYS},
               **{fold(n): k for k, n, _, _ in CRITERIA},
               **{fold(v): k for k, v in CRIT_SHORT.items()},
               "chinh_xac": "trung_thanh", "y_nghia": "trung_thanh", "mach_lac": "tu_nhien",
               "van_hoc": "van_phong", "dai_tu": "xung_ho", "thoai": "loi_thoai",
               "ten_rieng": "thuat_ngu"}


def norm_grade(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in ("-", "—", "–"):
        return "-"
    f = fold(s)
    return "-" if f in _NA_ALIAS else _GRADE_ALIAS.get(f)


def norm_crit(v):
    return _CRIT_ALIAS.get(fold(v or ""))


def parse_json_obj(text: str):
    """Lấy đối tượng JSON đầu tiên trong câu trả lời (bỏ <think>, ```, lời dẫn, dấu phẩy thừa)."""
    t = FENCE_RE.sub("", strip_think(text or "")).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    s = t[i:j + 1]
    for cand in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            v = json.loads(cand)
        except ValueError:
            continue
        return v if isinstance(v, dict) else None
    return None


def normalize_judgment(obj: dict, k: int):
    diem_in = obj.get("diem") or obj.get("scores") or {}
    if not isinstance(diem_in, dict):
        return None
    diem_in = {(norm_crit(key) or key): val for key, val in diem_in.items()}
    diem = {}
    for key, _, _, na in CRITERIA:
        g = norm_grade(diem_in.get(key))
        diem[key] = None if (g == "-" and not na) else g
    if not any(diem.values()):
        return None
    issues = []
    for it in obj.get("van_de") or obj.get("issues") or []:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("n"))
        except (TypeError, ValueError):
            continue
        c = norm_crit(it.get("tieu_chi"))
        if c and 1 <= n <= k:
            issues.append({"n": n, "c": c,
                           "trich": str(it.get("trich") or "").strip()[:300],
                           "goi_y": str(it.get("goi_y") or "").strip()[:400]})
    return {"diem": diem, "issues": issues}


_QUOTE_TR = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
                           "‘": "'", "’": "'", "\xa0": " "})
_QUOTE_STRIP = " .,;:!?\"'…-—–()"


def _qnorm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").translate(_QUOTE_TR)
    return WS_RE.sub(" ", s).strip().lower().strip(_QUOTE_STRIP)


def quote_found(quote: str, vi: str):
    """True/False: cụm trích có nằm nguyên văn trong bản dịch không. None: không trích."""
    q = _qnorm(quote)
    return None if not q else q in _qnorm(plain(vi or ""))


def quote_span(quote: str, text: str):
    """Vị trí (đầu, cuối) của cụm trích trong text, chấp nhận khác ngoặc kép / khoảng trắng."""
    q = _qnorm(quote)
    if not q:
        return None
    classes = {'"': "[\"“”„«»]", "'": "['‘’]"}
    words = []
    for w in q.split(" "):
        words.append("".join(classes.get(ch, re.escape(ch)) for ch in w))
    m = re.search(r"\s+".join(words), unicodedata.normalize("NFC", text), re.I)
    return (m.start(), m.end()) if m else None


class Judge:
    def __init__(self, llm: LLM, glossary: str):
        self.llm = llm
        self.system = build_judge_system(glossary)
        self.compare_system = build_compare_system(glossary)
        self.calls = 0
        self.out_tokens = 0
        self.gen_secs = 0.0
        self.warned_think = False

    def _chat(self, system, user, max_tokens, temperature, schema) -> str:
        text, ntok, secs, thinking = self.llm.chat(system, user, max_tokens, temperature, schema)
        self.calls += 1
        self.out_tokens += ntok
        self.gen_secs += secs
        if thinking and not self.warned_think:
            self.warned_think = True
            log("  ! Model đang suy nghĩ (thinking) trước khi chấm nên chậm gấp nhiều lần: "
                + self.llm.think_hint())
        return text

    def grade(self, pairs: list[tuple[str, str]], system: str | None = None):
        """Chấm một đoạn trích. pairs = [(EN, VI)]. Trả về {"diem", "issues"} hoặc None."""
        k = len(pairs)
        lines = ["Thẩm định đoạn trích sau.", ""]
        for i, (en, vi) in enumerate(pairs, 1):
            lines += [f"[{i}]", "EN: " + en, "VI: " + vi, ""]
        user = "\n".join(lines).rstrip()
        for temp in (0.0, 0.3):            # lần 2 tăng nhiệt độ để thoát câu trả lời hỏng
            budget = 512 + 200 * k                     # JSON tiếng Việt khá tốn token
            if self.llm.last_truncated:                # lần trước bị cắt giữa JSON → gấp đôi
                budget *= 2
            text = self._chat(system or self.system, user, budget, temp, judge_schema(k))
            obj = parse_json_obj(text)
            j = normalize_judgment(obj, k) if obj else None
            if j:
                return j
        return None

    def compare(self, en: str, a: str, b: str):
        """Trả về ("A" | "B" | "ngang" | None, lý do)."""
        user = f"EN: {en}\n\nBản A: {a}\n\nBản B: {b}\n\nBản nào tốt hơn?"
        for temp in (0.0, 0.3):
            obj = parse_json_obj(self._chat(self.compare_system, user, 512, temp, COMPARE_SCHEMA))
            if not obj:
                continue
            v = str(obj.get("tot_hon") or "").strip()
            why = str(obj.get("ly_do") or "").strip()[:300]
            if v.upper() in ("A", "B"):
                return v.upper(), why
            if fold(v) in ("ngang", "tie", "bang_nhau", "nhu_nhau"):
                return "ngang", why
        return None, ""


def run_calibration(judge: Judge) -> dict:
    pairs = [(en, vi) for _, en, vi, _ in CALIB_CASES]
    j = judge.grade(pairs, system=build_judge_system(CALIB_GLOSSARY))
    total = sum(1 for *_, exp in CALIB_CASES if exp)
    if not j:
        return {"level": "na", "verdict": "không chấm được bộ kiểm tra", "caught": 0,
                "total": total, "false_alarm": 0, "details": []}
    flagged: dict[int, set] = {}
    for iss in j["issues"]:
        flagged.setdefault(iss["n"], set()).add(iss["c"])
    details, caught, partial, fa = [], 0, 0, 0
    for i, (label, en, vi, exp) in enumerate(CALIB_CASES, 1):
        got = flagged.get(i, set())
        if exp:
            st = "caught" if got & exp else ("partial" if got else "missed")
            caught += st == "caught"
            partial += st == "partial"
        else:
            st = "false_alarm" if got else "ok"
            fa += st == "false_alarm"
        details.append({"label": label, "status": st, "got": sorted(got)})
    if caught == total and not fa:
        level, verdict = "good", "đáng tin"
    elif caught + partial >= total - 1:
        level, verdict = "ok", "tương đối"
    else:
        level, verdict = "bad", "dễ dãi — điểm số chỉ nên tham khảo"
    return {"level": level, "verdict": verdict, "caught": caught, "total": total,
            "false_alarm": fa, "details": details}


class ReviewStore:
    """Kết quả chấm và lịch sử sửa, ghi nối tiếp vào .jsonl (dừng giữa chừng không mất)."""

    def __init__(self, path: str):
        self.path = path
        self.chunks: dict[str, dict] = {}
        self.fixes: list[dict] = []
        self.undone: set[str] = set()
        self.f = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        self._index(rec)

    def _index(self, rec: dict) -> None:
        t = rec.get("type")
        if t == "chunk" and rec.get("h"):
            self.chunks[rec["h"]] = rec
        elif t == "fix" and rec.get("id"):
            self.fixes.append(rec)
        elif t == "undo":
            self.undone.add(rec.get("fix"))

    def add(self, rec: dict) -> None:
        self._index(rec)
        if self.f is None:
            self.f = open(self.path, "a", encoding="utf-8")
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()

    def latest_fix(self) -> dict[str, dict]:
        out = {}
        for f in self.fixes:
            if f["id"] not in self.undone:
                out[f["key"]] = f
        return out

    def close(self) -> None:
        if self.f:
            self.f.close()
            self.f = None


def review_paths(cache_path: str, report: str | None = None) -> tuple[str, str]:
    if cache_path.endswith("-cache.jsonl"):
        base = cache_path[:-len("-cache.jsonl")]
    else:
        base = os.path.splitext(cache_path)[0]
    return base + "-review.jsonl", report or base + "-review.html"


def review_segments(docs: list[Doc], cache: Cache) -> list[Segment]:
    out, seen = [], set()
    for d in docs:
        if d.nav or d.kind != "html":
            continue
        for s in d.segs:
            if s.key in cache and s.key not in seen:
                seen.add(s.key)
                out.append(s)
    return out


def chunk_hash(chunk: list[Segment], cache: Cache, model: str, gl_hash: str) -> str:
    payload = [REVIEW_VERSION, model, gl_hash, [[s.key, cache.get(s.key)] for s in chunk]]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def judge_all(chunks, cache, judge: Judge, store: ReviewStore, model: str, gl_hash: str,
              max_new: int = 0, only: set | None = None, title: str = ""):
    """Chấm các đoạn trích chưa có kết quả. Trả về ([(đoạn trích, kết quả | None)], số lỗi)."""
    hashes = [chunk_hash(ch, cache, model, gl_hash) for ch in chunks]
    todo = [i for i, h in enumerate(hashes)
            if h not in store.chunks and (only is None or i in only)]
    if max_new:
        todo = todo[:max_new]
    if title:
        have = sum(1 for h in hashes if h in store.chunks)
        log(f"{title}: {len(chunks):,} đoạn trích · đã có kết quả {have:,} · cần chấm {len(todo):,}")
    t0, failed = time.time(), 0
    for done, i in enumerate(todo, 1):
        ch = chunks[i]
        j = judge.grade([(plain(s.src), plain(cache.get(s.key))) for s in ch])
        if j:
            store.add({"type": "chunk", "h": hashes[i], "doc": ch[0].doc,
                       "keys": [s.key for s in ch], "diem": j["diem"], "issues": j["issues"],
                       "model": model, "t": int(time.time())})
        else:
            failed += 1
        el = time.time() - t0
        tps = judge.out_tokens / judge.gen_secs if judge.gen_secs else 0
        log(f"  [{done:>5,}/{len(todo):,}] {ch[0].doc[-40:]:<40} {tps:5.1f} tok/s · "
            f"đã chạy {fmt_dur(el)} · còn ~{fmt_dur(el / done * (len(todo) - done))}"
            + ("" if j else "  ✗ không đọc được kết quả chấm"))
    return [(ch, store.chunks.get(h)) for ch, h in zip(chunks, hashes)], failed


def _score(dist: dict):
    den = dist["tốt"] + dist["tạm"] + dist["kém"]
    return None if not den else (100 * dist["tốt"] + 50 * dist["tạm"]) / den


def aggregate(results):
    """Gộp điểm theo tiêu chí (cả sách) và theo chương; trọng số = độ dài bản gốc."""
    def blank():
        return {"tốt": 0, "tạm": 0, "kém": 0, "-": 0}
    crit = {c: {"dist": blank(), "issues": 0} for c in CRIT_KEYS}
    per_doc: dict[str, dict] = {}
    for ch, rec in results:
        if not rec:
            continue
        w = sum(len(plain(s.src)) for s in ch) or 1
        d = per_doc.setdefault(ch[0].doc, {"dist": {c: blank() for c in CRIT_KEYS}, "issues": 0})
        for c in CRIT_KEYS:
            g = rec["diem"].get(c)
            if g in crit[c]["dist"]:
                crit[c]["dist"][g] += w
                d["dist"][c][g] += w
        for iss in rec["issues"]:
            crit[iss["c"]]["issues"] += 1
            d["issues"] += 1
    for c in CRIT_KEYS:
        crit[c]["score"] = _score(crit[c]["dist"])
    vals = [crit[c]["score"] for c in CRIT_KEYS if crit[c]["score"] is not None]
    overall = sum(vals) / len(vals) if vals else None
    chapters = {}
    for doc, d in per_doc.items():
        sc = {c: _score(d["dist"][c]) for c in CRIT_KEYS}
        v = [x for x in sc.values() if x is not None]
        chapters[doc] = {"scores": sc, "overall": sum(v) / len(v) if v else None,
                         "issues": d["issues"]}
    return crit, overall, chapters


def grade_label(v) -> str:
    if v is None:
        return "—"
    return "Tốt" if v >= 85 else "Khá" if v >= 65 else "Trung bình" if v >= 45 else "Yếu"


def chapter_info(docs: list[Doc], cache: Cache):
    """Tên chương (tiêu đề đầu tiên, ưu tiên bản dịch) và vị trí từng đoạn trong chương."""
    labels: dict[str, str] = {}
    pos: dict[str, int] = {}
    for d in docs:
        if d.nav or d.kind != "html":
            continue
        for i, s in enumerate(d.segs, 1):
            pos.setdefault(s.key, i)
            if s.doc in labels:
                continue
            e = s.el
            for _ in range(3):
                if e is None:
                    break
                if lname(e) in ("h1", "h2", "h3"):
                    t = plain(cache.get(s.key) or s.src).strip()
                    labels[s.doc] = t[:70] + ("…" if len(t) > 70 else "")
                    break
                e = e.getparent()
        if d.segs:
            labels.setdefault(d.segs[0].doc, os.path.basename(d.path))
    return labels, pos


def rewrite_segment(translator: Translator, seg: Segment, old: str, issues: list, args):
    notes = []
    for iss in issues:
        q = f'"{iss["trich"]}" ' if iss["trich"] else ""
        notes.append(f"- [{CRIT_NAME[iss['c']]}] {q}→ {iss['goi_y'] or 'cần sửa'}")
    preface = ("BẢN DỊCH CẦN SỬA. Biên tập viên nhận xét:\n" + "\n".join(notes)
               + "\n\nBản dịch cũ (tham khảo):\n" + old
               + "\n\nHãy dịch lại cho hay hơn, khắc phục các nhận xét trên, "
                 "giữ đúng mọi thẻ định dạng.\n")
    translator.context = []
    for attempt in range(1 + args.retries):
        temp = min(args.temperature + 0.15 * attempt, 1.0)
        vi = translator._ask([seg], temp, preface).get(1)
        if vi:
            reason, tags_ok = Translator.check(seg, vi)
            if reason is None and tags_ok:
                return vi
    return None


def run_fixes(results, cache: Cache, judge: Judge, translator: Translator,
              store: ReviewStore, args) -> set:
    """Viết lại các đoạn bị nhận xét; chỉ thay khi bản mới thắng cả hai lượt so sánh A/B.
    Trả về tập khoá các đoạn đã được thay."""
    targets: dict[str, tuple] = {}
    for ch, rec in results:
        if not rec:
            continue
        for iss in rec["issues"]:
            s = ch[iss["n"] - 1]
            targets.setdefault(s.key, (s, []))[1].append(iss)
    tried = {(f["key"], f["old"]) for f in store.fixes}
    todo = [(s, iss) for k, (s, iss) in targets.items() if (k, cache.get(k)) not in tried]
    skipped = len(targets) - len(todo)
    if args.max_fixes:
        todo = todo[:args.max_fixes]
    log(f"Viết lại {len(todo):,} đoạn bị nhận xét"
        + (f" (bỏ qua {skipped:,} đoạn đã thử ở lần trước)" if skipped else ""))
    applied: set = set()
    for i, (seg, issues) in enumerate(todo, 1):
        old = cache.get(seg.key)
        new = rewrite_segment(translator, seg, old, issues, args)
        votes, reasons, ok = [], [], False
        if new and plain(new).strip() != plain(old).strip():
            en, po, pn = plain(seg.src), plain(old), plain(new)
            v1, r1 = judge.compare(en, po, pn)          # A = cũ, B = mới
            v2, r2 = judge.compare(en, pn, po)          # đảo thứ tự để khử thiên vị vị trí
            votes = [{"B": "new", "A": "old", "ngang": "tie"}.get(v1, "?"),
                     {"A": "new", "B": "old", "ngang": "tie"}.get(v2, "?")]
            reasons = [r for r in (r1, r2) if r]
            ok = votes.count("new") == 2 or (votes.count("new") == 1 and votes.count("tie") == 1)
        store.add({"type": "fix", "id": uuid.uuid4().hex[:12], "key": seg.key, "doc": seg.doc,
                   "en": seg.src, "old": old, "new": new, "votes": votes, "reasons": reasons,
                   "applied": ok, "issues": issues, "t": int(time.time())})
        if ok:
            cache.put(seg, new)
            applied.add(seg.key)
        status = ("✓ thay bản mới" if ok else "✗ không viết lại được" if not new
                  else "· giữ bản cũ")
        log(f"  [{i:>4}/{len(todo)}] {seg.doc[-40:]:<40} {status}"
            + (f" ({votes.count('new')}/2 phiếu)" if votes else ""))
    return applied


def undo_fixes(store: ReviewStore, cache: Cache) -> int:
    n = 0
    for f in reversed(store.fixes):
        if f.get("applied") and f["id"] not in store.undone and cache.get(f["key"]) == f["new"]:
            cache.put_raw(f["key"], f["doc"], f["en"], f["old"])
            store.add({"type": "undo", "fix": f["id"], "t": int(time.time())})
            n += 1
    return n


def text_bar(dist: dict, width: int = 20) -> str:
    den = dist["tốt"] + dist["tạm"] + dist["kém"]
    if not den:
        return "(không áp dụng)"
    a = round(width * dist["tốt"] / den)
    b = round(width * (dist["tốt"] + dist["tạm"]) / den) - a
    return "█" * a + "▒" * b + "░" * (width - a - b)


REPORT_CSS = """
:root{--bg:#f7f5f0;--fg:#1f1c17;--muted:#6d675d;--card:#fff;--line:#e4dfd4;--accent:#8a4f24;
--g:#2e7d4f;--g-bg:#dff0e5;--k:#5b7d22;--k-bg:#ebf2d9;--m:#a5650c;--m-bg:#fbecd2;--b:#b3372b;--b-bg:#f8dfdb;
--na:#8f897f;--na-bg:#ece9e2;--mark:#ffe58f;--mark-fg:#1f1c17;
--serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#151411;--fg:#ebe6dd;--muted:#a19a8e;--card:#1e1c18;--line:#33302a;
--accent:#e0a774;--g:#6fc993;--g-bg:#1b3325;--k:#b3d47a;--k-bg:#283019;--m:#eab160;--m-bg:#3a2c14;--b:#f08b7f;
--b-bg:#3e1f1b;--na:#8a847a;--na-bg:#2a2823;--mark:#6b5414;--mark-fg:#fff3cf}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 var(--sans)}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}
h1,h2,h4{line-height:1.25;margin:0}
h1{font-family:var(--serif);font-size:30px;font-weight:600;letter-spacing:-.01em}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600;margin-bottom:14px}
h2 .count{background:var(--na-bg);color:var(--fg);border-radius:999px;padding:1px 8px;margin-left:6px;letter-spacing:0}
section{margin-top:36px}.muted{color:var(--muted)}code{font-family:var(--mono);font-size:.92em}
.hero{display:flex;gap:24px;align-items:flex-end;justify-content:space-between;padding-bottom:24px;border-bottom:1px solid var(--line)}
.eyebrow{margin:0 0 6px;color:var(--accent);font-weight:600;font-size:13px;letter-spacing:.06em;text-transform:uppercase}
.meta{margin:6px 0 0;color:var(--muted);font-size:14px}
.hero-score{min-width:160px;text-align:right}
.hero-score .num{font-family:var(--serif);font-size:64px;line-height:1;font-weight:600}
.hero-score .of{color:var(--muted);font-size:18px;margin-left:2px}
.hero-score .lbl{font-weight:600;margin-top:4px}.hero-score .before{color:var(--muted);font-size:13px;margin-top:2px}
.t-g{color:var(--g)}.t-k{color:var(--k)}.t-m{color:var(--m)}.t-b{color:var(--b)}.t-na{color:var(--na)}
.howto p{margin:0;max-width:75ch}
.calib{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--na);border-radius:10px;padding:16px 18px}
.calib.lvl-good{border-left-color:var(--g)}.calib.lvl-ok{border-left-color:var(--m)}.calib.lvl-bad{border-left-color:var(--b)}
.calib h2{margin-bottom:8px}.calib-verdict{margin:0 0 6px;font-size:16px}.calib p{max-width:75ch}
.calib ul{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:4px;font-size:14px}
.calib li::before{display:inline-block;width:1.4em;font-weight:700}
.st-caught::before,.st-ok::before{content:"✓";color:var(--g)}.st-partial::before{content:"~";color:var(--m)}
.st-missed::before,.st-false_alarm::before{content:"✗";color:var(--b)}
.legend{display:flex;gap:14px;font-size:13px;color:var(--muted);margin:-4px 0 12px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.crit-list{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(290px,1fr))}
.crit{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.crit-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.crit-name{font-weight:600}.crit-score{font-family:var(--serif);font-size:28px;font-weight:600;line-height:1}
.crit-desc{font-size:13px;color:var(--muted);margin:4px 0 0}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--na-bg);margin:12px 0 8px}
.bar span{display:block;height:100%}.bg-g{background:var(--g)}.bg-m{background:var(--m)}.bg-b{background:var(--b)}
.crit-foot{display:flex;justify-content:space-between;gap:12px;font-size:13px;color:var(--muted)}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:680px}
th,td{padding:9px 10px;text-align:center;border-bottom:1px solid var(--line)}
th{font-size:12px;color:var(--muted);font-weight:600;white-space:nowrap}tr:last-child td{border-bottom:0}
td.ch,th.ch{text-align:left}td.ch small{display:block;color:var(--muted);font-size:12px}
td.cell{font-variant-numeric:tabular-nums;font-weight:600}
.c-g{background:var(--g-bg);color:var(--g)}.c-k{background:var(--k-bg);color:var(--k)}
.c-m{background:var(--m-bg);color:var(--m)}.c-b{background:var(--b-bg);color:var(--b)}.c-na{color:var(--na)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.chip{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:5px 12px;cursor:pointer}
.chip span{color:var(--muted);margin-left:4px}.chip.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.chip.on span{color:inherit;opacity:.7}
.list{display:grid;gap:12px}
.issue,.fix{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.head{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin-bottom:8px;font-size:13px}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:600;font-size:12px;background:var(--na-bg);color:var(--fg)}
.dot::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:1px;background:var(--dot,var(--na))}
.p-trung_thanh{--dot:#3b6fb6}.p-tu_nhien{--dot:#2e8b7a}.p-van_phong{--dot:#9b4f96}
.p-xung_ho{--dot:#c2662d}.p-loi_thoai{--dot:#b8932a}.p-thuat_ngu{--dot:#7b8190}
.where{color:var(--muted)}
.vi{font-family:var(--serif);font-size:17px;line-height:1.6;margin:0}.en{color:var(--muted);font-size:14px;margin:6px 0 0}
mark{background:var(--mark);color:var(--mark-fg);border-radius:3px;padding:0 2px}
.hint{margin:10px 0 0;font-size:14px}.hint b{color:var(--accent);margin-right:6px}
.warn{margin:8px 0 0;font-size:13px;color:var(--m)}.note{margin:8px 0 0;font-size:13px;color:var(--muted)}
.ab{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px}
.ab h4{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.ab .new{border-left:3px solid var(--g);padding-left:10px}.fix.kept .ab .new{border-left-color:var(--line)}
.ab .old{padding-left:13px}
.s-applied{background:var(--g-bg);color:var(--g)}.s-kept{background:var(--na-bg);color:var(--muted)}
.s-failed{background:var(--b-bg);color:var(--b)}
.empty{padding:18px;text-align:center;color:var(--muted);background:var(--card);border:1px dashed var(--line);border-radius:10px}
footer{margin-top:48px;padding-top:18px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}
footer p{margin:0 0 8px;max-width:80ch}
@media (max-width:640px){.hero{flex-direction:column;align-items:flex-start}.hero-score{text-align:left}
.ab{grid-template-columns:1fr}h1{font-size:24px}.hero-score .num{font-size:52px}}
[hidden]{display:none!important}
"""

REPORT_JS = """
document.querySelectorAll('.chip').forEach(function(b){b.addEventListener('click',function(){
document.querySelectorAll('.chip').forEach(function(x){x.classList.toggle('on',x===b);x.setAttribute('aria-pressed',x===b);});
var f=b.getAttribute('data-f');
document.querySelectorAll('.issue').forEach(function(a){a.hidden=f!=='all'&&a.getAttribute('data-c')!==f;});
});});
"""


def render_report(data: dict) -> str:
    def E(s):
        return html.escape("" if s is None else str(s), quote=True)

    def cls(v):
        return "na" if v is None else "g" if v >= 85 else "k" if v >= 65 else "m" if v >= 45 else "b"

    def num(v):
        return "—" if v is None else f"{v:.0f}"

    def marked(text, quote):
        span = quote_span(quote, text) if quote else None
        if not span:
            return E(text)
        a, b = span
        return E(text[:a]) + "<mark>" + E(text[a:b]) + "</mark>" + E(text[b:])

    o, ob = data.get("overall"), data.get("overall_before")
    out = ['<!doctype html><html lang="vi"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>Đánh giá bản dịch — {E(data["title"])}</title>'
           f'<style>{REPORT_CSS}</style></head><body><main>']

    # Đầu trang
    before = ""
    if ob is not None and o is not None:
        before = f'<div class="before">trước khi sửa: {num(ob)} ({o - ob:+.0f})</div>'
    out.append(
        '<header class="hero"><div>'
        '<p class="eyebrow">Đánh giá bản dịch</p>'
        f'<h1>{E(data["title"])}</h1>'
        f'<p class="meta">{E(data.get("author") or "")}{" · " if data.get("author") else ""}'
        f'giám khảo <code>{E(data["model"])}</code> · {E(data["generated"])}</p>'
        f'<p class="meta">{E(data["coverage"])}</p></div>'
        f'<div class="hero-score t-{cls(o)}"><span class="num">{num(o)}</span><span class="of">/100</span>'
        f'<div class="lbl">{E(grade_label(o))}</div>{before}</div></header>')

    out.append(
        '<section class="howto"><h2>Cách đọc báo cáo</h2><p>Sách được chia thành các đoạn trích '
        'khoảng một trang. Với mỗi đoạn trích, chính model dịch đóng vai biên tập viên: ghi ra từng '
        'vấn đề cụ thể (trích nguyên văn và gợi ý sửa), rồi chấm 6 tiêu chí theo 3 mức '
        '<b>tốt</b> (100) · <b>tạm</b> (50) · <b>kém</b> (0). Điểm mỗi tiêu chí là trung bình có '
        'trọng số theo độ dài; điểm tổng là trung bình 6 tiêu chí. Mốc: từ 85 tốt, 65 khá, '
        '45 trung bình.</p></section>')

    # Kiểm tra giám khảo
    cal = data.get("calibration")
    if cal:
        items = "".join(f'<li class="st-{E(d["status"])}">{E(d["label"])} — '
                        f'{E(CALIB_STATUS.get(d["status"], d["status"]))}'
                        + (f' <span class="muted">({E(", ".join(CRIT_NAME.get(c, c) for c in d["got"]))})</span>'
                           if d["got"] else "") + "</li>"
                        for d in cal["details"])
        out.append(
            f'<section class="calib lvl-{E(cal["level"])}"><h2>Giám khảo có đáng tin không?</h2>'
            f'<p class="calib-verdict"><b>Bắt được {cal["caught"]}/{cal["total"]} lỗi cài sẵn</b>, '
            f'báo nhầm {cal["false_alarm"]} → <b>{E(cal["verdict"])}</b></p>'
            '<p class="muted">Trước khi chấm sách, model chấm thử 4 đoạn đã biết trước đáp án: '
            '1 đoạn dịch tốt và 3 đoạn cài sẵn lỗi. Model tự chấm bài mình thường dễ dãi; nếu nó '
            'bỏ sót lỗi ở đây thì điểm số bên dưới nên đọc dè dặt, còn danh sách vấn đề vẫn có ích.</p>'
            f'<ul>{items}</ul></section>')

    # Theo tiêu chí
    rows = []
    for c in data["criteria"]:
        d = c["dist"]
        den = d["tốt"] + d["tạm"] + d["kém"]
        tot_all = den + d["-"]
        bar = ""
        if den:
            for g, k in (("tốt", "g"), ("tạm", "m"), ("kém", "b")):
                pct = 100 * d[g] / den
                if pct > 0:
                    bar += f'<span class="bg-{k}" style="width:{pct:.2f}%" title="{g}: {pct:.0f}%"></span>'
        na = f' · {100 * d["-"] / tot_all:.0f}% không áp dụng' if tot_all and d["-"] else ""
        rows.append(
            f'<div class="crit"><div class="crit-head"><span class="crit-name">{E(c["name"])}</span>'
            f'<span class="crit-score t-{cls(c["score"])}">{num(c["score"])}</span></div>'
            f'<p class="crit-desc">{E(c["desc"])}</p>'
            f'<div class="bar" role="img" aria-label="tốt {100 * d["tốt"] / den if den else 0:.0f}%, '
            f'tạm {100 * d["tạm"] / den if den else 0:.0f}%, kém {100 * d["kém"] / den if den else 0:.0f}%">{bar}</div>'
            f'<div class="crit-foot"><span>{E(grade_label(c["score"]))}{na}</span>'
            f'<span>{c["issues"]} vấn đề</span></div></div>')
    out.append('<section><h2>Theo tiêu chí</h2><div class="legend"><span><i class="bg-g"></i>tốt</span>'
               '<span><i class="bg-m"></i>tạm</span><span><i class="bg-b"></i>kém</span>'
               '<span>(tỷ lệ theo độ dài văn bản)</span></div>'
               f'<div class="crit-list">{"".join(rows)}</div></section>')

    # Theo chương
    if data["chapters"]:
        head = "".join(f"<th>{E(CRIT_SHORT[k])}</th>" for k in CRIT_KEYS)
        body = []
        for ch in data["chapters"]:
            cells = "".join(f'<td class="cell c-{cls(ch["scores"][k])}">{num(ch["scores"][k])}</td>'
                            for k in CRIT_KEYS)
            body.append(f'<tr><td class="ch">{E(ch["label"])}<small>{E(ch["doc"])}</small></td>{cells}'
                        f'<td class="cell c-{cls(ch["overall"])}">{num(ch["overall"])}</td>'
                        f'<td>{ch["issues"]}</td></tr>')
        out.append('<section><h2>Theo chương</h2><div class="table-wrap"><table>'
                   f'<thead><tr><th class="ch">Chương</th>{head}<th>Tổng</th><th>Vấn đề</th></tr></thead>'
                   f'<tbody>{"".join(body)}</tbody></table></div></section>')

    # Các đoạn cần xem lại
    issues = data["issues"]
    counts = {k: sum(1 for i in issues if i["c"] == k) for k in CRIT_KEYS}
    chips = [f'<button class="chip on" data-f="all" aria-pressed="true">Tất cả<span>{len(issues)}</span></button>']
    chips += [f'<button class="chip" data-f="{k}" aria-pressed="false">{E(CRIT_NAME[k])}<span>{n}</span></button>'
              for k, n in counts.items() if n]
    cards = []
    for it in issues:
        warn = ('<p class="warn">⚠ Cụm trích không khớp nguyên văn bản dịch — nhận xét này có thể '
                'không chính xác.</p>') if it["quote_ok"] is False else ""
        note = f'<p class="note">{E(it["note"])}</p>' if it.get("note") else ""
        hint = f'<p class="hint"><b>Gợi ý</b>{E(it["goi_y"])}</p>' if it["goi_y"] else ""
        where = E(it["chapter"]) + (f' · đoạn {it["pos"]}' if it.get("pos") else "")
        cards.append(
            f'<article class="issue" data-c="{E(it["c"])}"><div class="head">'
            f'<span class="pill dot p-{E(it["c"])}">{E(CRIT_NAME[it["c"]])}</span>'
            f'<span class="where">{where}</span></div>'
            f'<p class="vi">{marked(it["vi"], it["trich"] if it["quote_ok"] else "")}</p>'
            f'<p class="en">{E(it["en"])}</p>{hint}{warn}{note}</article>')
    out.append(f'<section><h2>Các đoạn cần xem lại<span class="count">{len(issues)}</span></h2>'
               + (f'<div class="chips" role="group" aria-label="Lọc theo tiêu chí">{"".join(chips)}</div>'
                  f'<div class="list">{"".join(cards)}</div>' if issues else
                  '<div class="empty">Giám khảo không ghi nhận vấn đề nào.</div>')
               + '</section>')

    # Các đoạn đã viết lại
    fixes = data.get("fixes") or []
    if fixes:
        fx = []
        for f in fixes:
            st = f["status"]
            label = {"applied": "Đã thay bản mới", "kept": "Giữ bản cũ",
                     "failed": "Không viết lại được"}[st]
            votes = f' · {f["votes_new"]}/2 phiếu' if f.get("votes") else ""
            why = (f'<p class="hint"><b>Lý do</b>{E(" / ".join(f["reasons"]))}</p>'
                   if f.get("reasons") else "")
            probs = "".join(f'<span class="pill dot p-{E(i["c"])}">{E(CRIT_NAME.get(i["c"], i["c"]))}</span>'
                            for i in f["issues"])
            newcol = (f'<div class="new"><h4>Bản mới</h4><p class="vi">{E(f["new"])}</p></div>'
                      if f.get("new") else "")
            where = E(f["chapter"]) + (f' · đoạn {f["pos"]}' if f.get("pos") else "")
            fx.append(
                f'<article class="fix {"applied" if st == "applied" else "kept"}"><div class="head">'
                f'<span class="pill s-{st}">{label}{votes}</span>{probs}'
                f'<span class="where">{where}</span></div>'
                f'<p class="en">{E(f["en"])}</p>'
                f'<div class="ab"><div class="old"><h4>Bản cũ</h4><p class="vi">{E(f["old"])}</p></div>'
                f'{newcol}</div>{why}</article>')
        n_app = sum(1 for f in fixes if f["status"] == "applied")
        out.append(f'<section><h2>Các đoạn đã viết lại<span class="count">{n_app}/{len(fixes)}</span></h2>'
                   '<p class="muted" style="margin:-6px 0 14px;max-width:75ch">Đoạn bị nhận xét được viết '
                   'lại kèm góp ý; model so sánh bản cũ và bản mới hai lần, đổi thứ tự A/B để khử thiên vị '
                   'vị trí. Chỉ thay khi bản mới thắng.</p>'
                   f'<div class="list">{"".join(fx)}</div></section>')

    out.append(
        '<footer><p>Giám khảo là chính model đã dịch, chấm ở nhiệt độ 0. Model tự chấm bài mình có xu '
        'hướng dễ dãi và không thay được người đọc; hãy dùng báo cáo để biết <i>nên soát chỗ nào</i>, '
        'không phải để tin tuyệt đối vào con số.</p>'
        f'<p>Dữ liệu chấm: <code>{E(data["store_name"])}</code> · bản dịch: <code>{E(data["cache_name"])}</code>. '
        'Chạy lại lệnh <code>review</code> chỉ chấm những đoạn trích có thay đổi.</p></footer>')
    out.append(f'</main><script>{REPORT_JS}</script></body></html>')
    return "".join(out)


def build_report_data(title, author, model, results, cache, docs, store, calib,
                      overall_before, cache_path, store_path) -> dict:
    crit, overall, chapters = aggregate(results)
    labels, pos = chapter_info(docs, cache)
    fix_by_key = store.latest_fix()

    total_w = sum(len(plain(s.src)) for ch, _ in results for s in ch) or 1
    done_w = sum(len(plain(s.src)) for ch, rec in results if rec for s in ch)
    n_done = sum(1 for _, rec in results if rec)
    coverage = (f"Đã chấm {n_done:,}/{len(results):,} đoạn trích "
                f"({100 * done_w / total_w:.0f}% nội dung đã dịch)")

    issues = []
    for ch, rec in results:
        if not rec:
            continue
        for iss in rec["issues"]:
            s = ch[iss["n"] - 1]
            vi = cache.get(s.key) or ""
            f = fix_by_key.get(s.key)
            note = None
            if f and not f["applied"] and f["old"] == vi:
                note = "Đã thử viết lại nhưng bản mới không hơn — giữ bản cũ."
            issues.append({"doc": s.doc, "chapter": labels.get(s.doc, s.doc), "pos": pos.get(s.key),
                           "c": iss["c"], "en": plain(s.src), "vi": plain(vi), "trich": iss["trich"],
                           "quote_ok": quote_found(iss["trich"], vi), "goi_y": iss["goi_y"],
                           "note": note})

    order = {s.key: i for i, s in enumerate(s for ch, _ in results for s in ch)}
    fixes = []
    for key, f in sorted(fix_by_key.items(), key=lambda kv: order.get(kv[0], 1 << 30)):
        cur = cache.get(key)
        if f["applied"] and cur == f["new"]:
            st = "applied"
        elif not f["applied"] and cur == f["old"]:
            st = "failed" if not f.get("new") else "kept"
        else:
            continue                                   # bản dịch đã đổi sau lần sửa này
        fixes.append({"status": st, "chapter": labels.get(f["doc"], f["doc"]), "pos": pos.get(key),
                      "en": plain(f["en"]), "old": plain(f["old"]), "new": plain(f.get("new") or ""),
                      "votes": f.get("votes"), "votes_new": (f.get("votes") or []).count("new"),
                      "reasons": f.get("reasons") or [], "issues": f.get("issues") or []})

    return {
        "title": title, "author": author, "model": model,
        "generated": time.strftime("%d/%m/%Y %H:%M"), "coverage": coverage,
        "overall": overall, "overall_before": overall_before, "calibration": calib,
        "criteria": [{"key": k, "name": n, "desc": d, "score": crit[k]["score"],
                      "dist": crit[k]["dist"], "issues": crit[k]["issues"]}
                     for k, n, d, _ in CRITERIA],
        "chapters": [{"doc": doc, "label": labels.get(doc, doc), **v} for doc, v in chapters.items()],
        "issues": issues, "fixes": fixes,
        "store_name": os.path.basename(store_path), "cache_name": os.path.basename(cache_path),
    }


def print_review_summary(data: dict, report_path: str) -> None:
    log("")
    cal = data.get("calibration")
    if cal:
        log(f"Kiểm tra giám khảo : bắt được {cal['caught']}/{cal['total']} lỗi cài sẵn, "
            f"báo nhầm {cal['false_alarm']} → {cal['verdict']}")
    log(f"Phạm vi            : {data['coverage']}")
    log("")
    log(f"{'Tiêu chí':<24}{'Điểm':>5}   {'tốt █  tạm ▒  kém ░':<22}{'vấn đề':>8}")
    for c in data["criteria"]:
        sc = "—" if c["score"] is None else f"{c['score']:.0f}"
        log(f"{c['name']:<24}{sc:>5}   {text_bar(c['dist']):<22}{c['issues']:>8}")
    o, ob = data["overall"], data["overall_before"]
    log("")
    log(f"Điểm tổng: {'—' if o is None else f'{o:.0f}'}/100 ({grade_label(o)})"
        + (f"   ·   trước khi sửa: {ob:.0f}" if ob is not None and o is not None else ""))
    if data["chapters"]:
        worst = sorted((ch for ch in data["chapters"] if ch["overall"] is not None),
                       key=lambda ch: ch["overall"])[:3]
        if worst:
            log("Chương điểm thấp nhất: " + " · ".join(f"{ch['label'][:30]} ({ch['overall']:.0f})"
                                                       for ch in worst))
    log(f"Báo cáo chi tiết: {report_path}")


# ---------------------------------------------------------------------------
# Lệnh
# ---------------------------------------------------------------------------

def default_cache(epub: str) -> str:
    return os.path.splitext(epub)[0] + ".vi-cache.jsonl"


def default_out(epub: str, bilingual: bool) -> str:
    return os.path.splitext(epub)[0] + (".en-vi.epub" if bilingual else ".vi.epub")


def unique_segments(docs: list[Doc], cache: Cache, redo: list[str] | None = None) -> list[Segment]:
    seen: set[str] = set()
    out = []
    for d in docs:
        forced = bool(redo) and any(p in d.segs[0].doc for p in redo) if d.segs else False
        for s in d.segs:
            if (forced or s.key not in cache) and s.key not in seen:
                seen.add(s.key)
                out.append(s)
    return out


def cmd_info(args) -> int:
    book = Book(args.epub)
    try:
        docs = extract_docs(book)
        cache = Cache(args.cache or default_cache(args.epub))
        log(f"Sách     : {book.meta('title') or '(không tên)'} — {book.meta('creator') or '?'}")
        log(f"Ngôn ngữ : {book.meta('language') or '?'}   |   EPUB {book.opf.getroot().get('version') or '?'}")
        total_chars = total_words = total_segs = 0
        log("")
        for i, d in enumerate(docs, 1):
            chars = sum(len(plain(s.src)) for s in d.segs)
            words = sum(len(re.findall(r"[^\W\d_]+", plain(s.src))) for s in d.segs)
            total_chars += chars
            total_words += words
            total_segs += len(d.segs)
            tag = " (mục lục)" if d.nav else ""
            log(f"  {i:3d}. {book.rel(d.path):<48} {len(d.segs):5d} đoạn {chars:9,d} ký tự{tag}")
        pending = unique_segments(docs, cache)
        p_chars = sum(len(plain(s.src)) for s in pending)
        est_tokens = int(p_chars * 0.45)
        log("")
        log(f"Tổng     : {total_segs:,} đoạn · {total_chars:,} ký tự · ~{total_words:,} từ")
        cached = sum(1 for d in docs for s in d.segs if s.key in cache)
        log(f"Cần dịch : {len(pending):,} đoạn (đã có trong cache: {cached:,}, "
            f"trùng lặp dùng chung bản dịch: {total_segs - cached - len(pending):,})")
        log(f"Ước tính : ~{est_tokens:,} token đầu ra → ở {args.tps:g} token/s ≈ "
            f"{fmt_dur(est_tokens / args.tps)} (chỉ tính phần sinh chữ, rất thô)")
        if book.encrypted:
            log("Lưu ý    : sách có font bị làm rối (obfuscation) — giữ nguyên mã định danh.")
    finally:
        book.close()
    return 0


CHECK_SAMPLE = ("<p>She had <i>never</i> seen the sea before, and when at last it rose beyond "
                "the dunes, grey and enormous and breathing, she stopped walking and could not "
                "say a word.</p>")


def cmd_check(args) -> int:
    """Gọi model một lần với đoạn mẫu: kiểm tra kết nối, định dạng, tốc độ."""
    llm = LLM(args)
    log(f"Server : {llm.base}  ({args.backend})")
    log(f"Model  : {args.model}")
    el = etree.fromstring(CHECK_SAMPLE)
    src, mapping = encode(el, False)
    seg = Segment(el, src, mapping, False, "html", "check")
    args.context = 0
    tr = Translator(llm, build_system_prompt(args.prompt_file, args.glossary), args)
    log("Đang gọi model (lượt đầu có thể lâu vì phải nạp model vào RAM)…")
    t0 = time.time()
    try:
        got = tr._ask([seg], args.temperature)
    except LLMError as e:
        log(f"\n✗ Lỗi: {e}")
        return 1
    wall = time.time() - t0
    vi = got.get(1)
    log("")
    log("EN : " + plain(src))
    if vi:
        log("VI : " + plain(vi))
    elif tr.warned_think:
        log("VI : (trống — model dùng hết lượt cho phần suy nghĩ mà chưa kịp trả lời)")
    else:
        log("VI : (không đọc được câu trả lời)")
    reason, tags_ok = Translator.check(seg, vi) if vi else ("không có câu trả lời", False)
    tps = tr.out_tokens / tr.gen_secs if tr.gen_secs else 0
    log("")
    log(f"Định dạng : {'✓ giữ đúng thẻ' if tags_ok else '✗ ' + (reason or 'mất / sai thẻ định dạng')}")
    log(f"Tốc độ    : {tps:.1f} token/s (tổng thời gian lượt gọi {wall:.1f}s)")
    log(f"Giới hạn  : {llm.last_budget:,} token đầu ra mỗi lượt"
        + (f" · ngữ cảnh {llm.num_ctx:,}" if args.backend == "ollama" else "")
        + (" · ✗ câu trả lời bị cắt — tăng --max-tokens" if tr.truncated else " · ✓ không bị cắt"))
    if tr.warned_think:
        log("Thinking  : ✗ " + llm.think_hint())
    elif args.backend == "ollama":
        log("Thinking  : ✓ đã tắt")
    if args.backend == "openai":
        log("            (backend openai: tốc độ tính cả thời gian xử lý prompt, nên thấp hơn thực tế)")
    if args.epub and tps > 0:
        book = Book(args.epub)
        try:
            docs = extract_docs(book)
            pending = unique_segments(docs, Cache(default_cache(args.epub)))
        finally:
            book.close()
        est = int(sum(len(plain(s.src)) for s in pending) * 0.45)
        log(f"Ước tính  : {os.path.basename(args.epub)} cần ~{est:,} token → khoảng "
            f"{fmt_dur(est / tps)} ở tốc độ này (chưa tính máy nóng hạ xung)")
    return 0 if vi and tags_ok else 1


def cmd_prompt(args) -> int:
    if args.judge:
        print(build_judge_system(load_glossary(args.glossary)))
    else:
        print(build_system_prompt(args.prompt_file, args.glossary))
    return 0


def cmd_review(args) -> int:
    cache_path = args.cache or default_cache(args.epub)
    if not os.path.exists(cache_path):
        log(f"Chưa có bản dịch: không thấy {cache_path}. Chạy lệnh `translate` trước.")
        return 1
    if not args.undo_fixes and not args.model:
        log("Thiếu --model (giám khảo là chính model đã dịch, vd: --model gemma4:12b-it-qat).")
        return 2
    store_path, report_path = review_paths(cache_path, args.report)
    cache = Cache(cache_path)
    store = ReviewStore(store_path)
    try:
        if args.undo_fixes:
            n = undo_fixes(store, cache)
            log(f"Đã hoàn tác {n} đoạn về bản dịch trước khi sửa."
                + (" Chạy `build` để đóng gói lại EPUB." if n else ""))
            return 0
        book = Book(args.epub)
        try:
            docs = extract_docs(book)
            title = book.meta("title") or os.path.basename(args.epub)
            author = book.meta("creator")
        finally:
            book.close()
        segs = review_segments(docs, cache)
        if not segs:
            log("Chưa có đoạn nào được dịch. Chạy lệnh `translate` trước.")
            return 1
        chunks = make_batches(segs, args.batch_chars, args.batch_max)
        glossary = load_glossary(args.glossary)
        gl_hash = hashlib.sha1(glossary.encode("utf-8")).hexdigest()[:12]
        llm = LLM(args)
        judge = Judge(llm, glossary)
        log(f"Sách: {title} · giám khảo: {args.model} ({args.backend})")

        calib = None
        if not args.no_calibration:
            log("Kiểm tra giám khảo bằng 4 đoạn đã biết trước đáp án…")
            calib = run_calibration(judge)
            log(f"  → bắt được {calib['caught']}/{calib['total']} lỗi cài sẵn, "
                f"báo nhầm {calib['false_alarm']} → {calib['verdict']}")

        results, failed = judge_all(chunks, cache, judge, store, args.model, gl_hash,
                                    max_new=args.max_chunks, title="Chấm bản dịch")
        overall_before = None
        if args.fix:
            translator = Translator(llm, build_system_prompt(args.prompt_file, args.glossary), args)
            changed = run_fixes(results, cache, judge, translator, store, args)
            if changed:
                overall_before = aggregate(results)[1]
                idx = {i for i, ch in enumerate(chunks) if any(s.key in changed for s in ch)}
                results, f2 = judge_all(chunks, cache, judge, store, args.model, gl_hash,
                                        only=idx, title="Chấm lại các đoạn trích đã sửa")
                failed += f2

        data = build_report_data(title, author, args.model, results, cache, docs, store, calib,
                                 overall_before, cache_path, store_path)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(render_report(data))
        print_review_summary(data, report_path)
        if failed:
            log(f"  ! {failed} đoạn trích không đọc được kết quả chấm — chạy lại lệnh để thử lại.")
        if overall_before is not None:
            log("Bản dịch mới đã ghi vào cache. Chạy `build` (kèm các tuỳ chọn đã dùng khi dịch) "
                "để đóng gói lại EPUB; `review --undo-fixes` để hoàn tác.")
        if args.open:
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(report_path))
        return 0
    except KeyboardInterrupt:
        log("\nĐã dừng. Kết quả chấm đã lưu — chạy lại đúng lệnh để chấm tiếp.")
        return 130
    except LLMError as e:
        log(f"\nLỗi model: {e}")
        log("Kết quả chấm đến thời điểm này đã được lưu.")
        return 1
    finally:
        cache.close()
        store.close()


def cmd_characters(args) -> int:
    book = Book(args.epub)
    try:
        docs = [d for d in extract_docs(book) if d.kind == "html" and not d.nav]
        text_parts, total = [], 0
        for d in docs:
            for s in d.segs:
                p = plain(s.src)
                text_parts.append(p)
                total += len(p) + 1
                if total >= args.chars:
                    break
            if total >= args.chars:
                break
    finally:
        book.close()
    chunks, cur, size = [], [], 0
    for p in text_parts:
        if cur and size + len(p) > 6000:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(p)
        size += len(p)
    if cur:
        chunks.append("\n".join(cur))

    llm = LLM(args)
    people: dict[str, dict] = {}
    order: list[str] = []
    for i, ch in enumerate(chunks, 1):
        log(f"Đang quét phần {i}/{len(chunks)}…")
        try:
            text, *_ = llm.chat(CHAR_SYSTEM, CHAR_USER.format(chunk=ch), 3072, 0.2)
        except LLMError as e:
            log(f"Lỗi: {e}")
            return 1
        for line in strip_think(text).splitlines():
            if "|" not in line:
                continue
            cols = [c.strip(" *-•\t") for c in line.strip().strip("|").split("|")]
            if len(cols) < 3 or not cols[0] or set(cols[0]) <= set("-: "):
                continue
            name = re.sub(r"^\d+[.)]\s*", "", cols[0])
            if name.lower() in ("tên gốc", "tên", "name"):
                continue
            cols += [""] * (5 - len(cols))
            k = name.lower()
            if k not in people:
                people[k] = {"name": name, "gender": cols[1], "role": cols[2],
                             "rel": cols[3], "call": cols[4]}
                order.append(k)
            else:
                p = people[k]
                for field, val in (("gender", cols[1]), ("role", cols[2]), ("call", cols[4])):
                    if not p[field] and val:
                        p[field] = val
                if cols[3] and cols[3] not in p["rel"] and len(p["rel"]) < 300:
                    p["rel"] = (p["rel"] + "; " + cols[3]).strip("; ")

    lines = [
        "# Bảng nhân vật & thuật ngữ",
        "",
        "<!-- Bản nháp do máy tạo từ phần đầu sách: HÃY SỬA LẠI cho đúng trước khi dịch.",
        "     Toàn bộ nội dung file (trừ chú thích như dòng này) được đưa vào prompt,",
        "     nên viết ngắn gọn, rõ ràng. Bổ sung khi đọc tới các chương sau. -->",
        "",
        "## Nhân vật",
    ]
    for k in order:
        p = people[k]
        desc = "; ".join(x for x in (p["gender"], p["role"], p["rel"]) if x)
        call = f' Lời kể gọi: "{p["call"]}".' if p["call"] else ""
        lines.append(f"- **{p['name']}** — {desc}.{call}")
    lines += ["", "## Xưng hô trong lời thoại (điền theo từng cặp, sửa khi quan hệ thay đổi)"]
    top = [people[k]["name"] for k in order[:4]]
    for a in range(len(top)):
        for b in range(a + 1, len(top)):
            lines.append(f"- {top[a]} ↔ {top[b]}: {top[a]} xưng \"…\", gọi {top[b]} \"…\"; "
                         f"{top[b]} xưng \"…\", gọi {top[a]} \"…\"")
    lines += ["", "## Tên riêng, địa danh, thuật ngữ",
              "<!-- ví dụ:",
              "- The Old Mill → giữ nguyên",
              "- Mr. / Mrs. + họ → ông / bà + họ (Mr. Hale → ông Hale) -->", ""]
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"Đã ghi {len(order)} nhân vật vào {args.output}. Mở ra sửa trước khi dịch.")
    return 0


def run_translation(args, use_llm: bool) -> int:
    out = args.output or default_out(args.epub, args.bilingual)
    cache = Cache(args.cache or default_cache(args.epub))
    book = Book(args.epub)
    translator = None
    try:
        docs = extract_docs(book)
        total = sum(len(d.segs) for d in docs)
        pending = unique_segments(docs, cache, getattr(args, "redo", None))
        if use_llm:
            if args.max_segments:
                pending = pending[:args.max_segments]
            system = build_system_prompt(args.prompt_file, args.glossary)
            log(f"Sách: {book.meta('title') or os.path.basename(args.epub)}")
            cached = sum(1 for d in docs for s in d.segs if s.key in cache)
            log(f"{total:,} đoạn · đã có trong cache {cached:,} · "
                f"lần này dịch {len(pending):,} · model {args.model} ({args.backend})")
            if pending:
                translator = Translator(LLM(args), system, args)
                batches = make_batches(pending, args.batch_chars, args.batch_max)
                todo_chars = sum(len(s.src) for s in pending)
                done_chars = done_segs = 0
                t0 = time.time()
                for bi, batch in enumerate(batches, 1):
                    res = translator.translate(batch)
                    for i, vi in res.items():
                        cache.put(batch[i - 1], vi)
                    done_chars += sum(len(s.src) for s in batch)
                    done_segs += len(batch)
                    el = time.time() - t0
                    eta = el / done_chars * (todo_chars - done_chars) if done_chars else 0
                    tps = translator.out_tokens / translator.gen_secs if translator.gen_secs else 0
                    log(f"[{done_segs:>6,}/{len(pending):,}] {batch[0].doc[-40:]:<40} "
                        f"{tps:5.1f} tok/s · đã chạy {fmt_dur(el)} · còn ~{fmt_dur(eta)}")
                log(f"Xong phần dịch: {translator.calls} lượt gọi, {translator.retried} lượt dịch lại, "
                    f"{len(translator.failed)} đoạn lỗi, {fmt_dur(time.time() - t0)}.")
        stats = build_output(book, docs, cache, out, args,
                             translator.failed if translator else None)
        log(f"\n→ {out}")
        log(f"  {stats['done']:,}/{total:,} đoạn đã có bản dịch"
            + (f", {stats['missing']:,} đoạn còn giữ tiếng Anh" if stats["missing"] else "")
            + (f", {stats['soft']} đoạn mất một phần định dạng" if stats["soft"] else "") + ".")
        if stats.get("report"):
            log(f"  Danh sách đoạn nên soát: {stats['report']}")
        if stats["missing"] and use_llm:
            log("  Chạy lại đúng lệnh này để dịch tiếp phần còn lại.")
        return 0
    except KeyboardInterrupt:
        log("\nĐã dừng. Tiến độ đã lưu trong cache — chạy lại đúng lệnh để dịch tiếp, "
            "hoặc dùng lệnh `build` để xem bản dịch dở dang.")
        return 130
    except LLMError as e:
        log(f"\nLỗi model: {e}")
        log("Tiến độ đến thời điểm này đã được lưu trong cache.")
        return 1
    finally:
        cache.close()
        book.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Dịch EPUB Anh → Việt bằng LLM chạy trên máy (Ollama / LM Studio / mlx_lm).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def llm_opts(p, required=True):
        p.add_argument("--model", required=required, help="tên model, vd: gemma4:12b-it-qat (Ollama)")
        p.add_argument("--backend", choices=["ollama", "openai"], default="ollama",
                       help="ollama (mặc định) | openai = LM Studio, mlx_lm.server, llama.cpp")
        p.add_argument("--base-url", help="mặc định: http://localhost:11434 (ollama), "
                                          "http://localhost:1234/v1 (openai)")
        p.add_argument("--api-key", help="thường không cần với server trên máy")
        p.add_argument("--num-ctx", type=int, default=0,
                       help="cửa sổ ngữ cảnh cho Ollama (mặc định 8192; 16384 khi --think)")
        p.add_argument("--max-tokens", type=int, default=0,
                       help="giới hạn token đầu ra mỗi lượt gọi (mặc định tự tính: "
                            "1,5 × số ký tự gốc + 512, tối thiểu 1024)")
        p.add_argument("--temperature", type=float, default=0.4)
        p.add_argument("--timeout", type=int, default=900, help="giây chờ mỗi lượt gọi")
        p.add_argument("--think", action="store_true",
                       help="BẬT chế độ suy nghĩ (thinking) của model — mặc định tắt vì chậm "
                            "gấp nhiều lần mà không làm câu văn hay hơn")
        p.add_argument("--think-budget", type=int, default=4096,
                       help="token cộng thêm cho phần suy nghĩ khi --think (4096)")
        p.add_argument("--keep-loaded", action="store_true",
                       help="giữ model trong RAM thêm 30 phút khi xong (mặc định: giải phóng "
                            "ngay để trả RAM cho máy)")
        p.add_argument("--no-think", action="store_true",
                       help="(giữ cho tương thích — thinking đã tắt sẵn)")

    def out_opts(p):
        p.add_argument("-o", "--output", help="file EPUB đầu ra (mặc định: <tên>.vi.epub)")
        p.add_argument("--cache", help="file cache (mặc định: <tên>.vi-cache.jsonl)")
        p.add_argument("--bilingual", action="store_true", help="xuất song ngữ: gốc + bản dịch")
        p.add_argument("--title", help="đặt tên sách mới trong metadata")
        p.add_argument("--drop-fonts", action="store_true",
                       help="bỏ font nhúng (hay thiếu dấu tiếng Việt)")
        p.add_argument("--keep-id", action="store_true",
                       help="giữ nguyên mã định danh sách (mặc định đổi để khỏi trùng bản gốc)")

    p = sub.add_parser("info", help="xem cấu trúc sách và ước tính thời gian")
    p.add_argument("epub")
    p.add_argument("--cache")
    p.add_argument("--tps", type=float, default=12,
                   help="tốc độ giả định, token/giây (12 ≈ model 12B 4-bit trên M4 Air)")

    p = sub.add_parser("check", help="kiểm tra kết nối model, định dạng và đo tốc độ")
    p.add_argument("epub", nargs="?", help="(tuỳ chọn) sách để ước tính thời gian dịch")
    llm_opts(p)
    p.add_argument("--glossary")
    p.add_argument("--prompt-file")

    p = sub.add_parser("prompt", help="in system prompt sẽ gửi cho model")
    p.add_argument("--glossary")
    p.add_argument("--prompt-file")
    p.add_argument("--judge", action="store_true", help="in prompt của giám khảo (lệnh review)")

    p = sub.add_parser("review", help="chấm bản dịch theo 6 tiêu chí bằng chính model, "
                                      "xuất báo cáo HTML")
    p.add_argument("epub")
    llm_opts(p, required=False)
    p.add_argument("--glossary", help="bảng nhân vật / thuật ngữ — căn cứ chấm xưng hô")
    p.add_argument("--prompt-file", help="phong cách dịch dùng khi --fix viết lại")
    p.add_argument("--cache", help="file cache bản dịch (mặc định: <tên>.vi-cache.jsonl)")
    p.add_argument("--report", help="file báo cáo HTML (mặc định: <tên>.vi-review.html)")
    p.add_argument("--batch-chars", type=int, default=1800, help="ký tự gốc mỗi đoạn trích (1800)")
    p.add_argument("--batch-max", type=int, default=10, help="số đoạn tối đa mỗi đoạn trích (10)")
    p.add_argument("--max-chunks", type=int, default=0,
                   help="chỉ chấm N đoạn trích chưa có kết quả (để thử)")
    p.add_argument("--no-calibration", action="store_true", help="bỏ bước kiểm tra giám khảo")
    p.add_argument("--fix", action="store_true",
                   help="viết lại đoạn bị nhận xét; chỉ thay khi bản mới thắng so sánh A/B")
    p.add_argument("--max-fixes", type=int, default=0, help="viết lại tối đa N đoạn")
    p.add_argument("--retries", type=int, default=2, help="số lần thử viết lại một đoạn (2)")
    p.add_argument("--undo-fixes", action="store_true", help="hoàn tác mọi đoạn đã thay bởi --fix")
    p.add_argument("--open", action="store_true", help="mở báo cáo trong trình duyệt khi xong")

    p = sub.add_parser("characters", help="tạo bản nháp bảng nhân vật từ đầu sách")
    p.add_argument("epub")
    p.add_argument("-o", "--output", default="glossary.md")
    p.add_argument("--chars", type=int, default=40000, help="số ký tự đầu sách cần quét (40000)")
    llm_opts(p)

    p = sub.add_parser("translate", help="dịch và đóng gói EPUB")
    p.add_argument("epub")
    llm_opts(p)
    out_opts(p)
    p.add_argument("--glossary", help="file bảng nhân vật / thuật ngữ (markdown)")
    p.add_argument("--prompt-file", help="file thay phần PHONG CÁCH của prompt mặc định")
    p.add_argument("--batch-chars", type=int, default=1800, help="số ký tự gốc mỗi lượt gọi (1800)")
    p.add_argument("--batch-max", type=int, default=10, help="số đoạn tối đa mỗi lượt gọi (10)")
    p.add_argument("--context", type=int, default=2, help="số đoạn trước gửi kèm làm ngữ cảnh (2)")
    p.add_argument("--retries", type=int, default=2, help="số lần dịch lại một đoạn lỗi (2)")
    p.add_argument("--max-segments", type=int, default=0, help="chỉ dịch N đoạn đầu (để thử)")
    p.add_argument("--redo", action="append", metavar="TÊN",
                   help="dịch lại các tệp có tên chứa TÊN dù đã có trong cache (dùng nhiều lần được)")

    p = sub.add_parser("build", help="đóng gói lại EPUB từ cache, không gọi model")
    p.add_argument("epub")
    out_opts(p)

    args = ap.parse_args(argv)
    install_signal_handlers()
    try:
        if args.cmd == "info":
            return cmd_info(args)
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "review":
            return cmd_review(args)
        if args.cmd == "prompt":
            return cmd_prompt(args)
        if args.cmd == "characters":
            return cmd_characters(args)
        if args.cmd == "translate":
            return run_translation(args, use_llm=True)
        if args.cmd == "build":
            return run_translation(args, use_llm=False)
        return 2
    finally:
        # Xong, lỗi, Ctrl+C hay đóng cửa sổ Terminal: đều trả RAM của model về cho máy.
        release_models(keep=getattr(args, "keep_loaded", False))


if __name__ == "__main__":
    sys.exit(main())
