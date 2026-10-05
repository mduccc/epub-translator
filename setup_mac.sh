#!/usr/bin/env bash
# Cài đặt epub-translator trên macOS (Apple Silicon).
#
#   ./setup_mac.sh                 # cài + Ollama + tải model mặc định (gemma4:12b-it-qat)
#   ./setup_mac.sh qwen3:14b       # cài + Ollama + tải model khác
#   ./setup_mac.sh lmstudio        # chỉ cài phần Python (bạn tự dùng LM Studio)
#
set -euo pipefail
cd "$(dirname "$0")"

MODEL="${1:-gemma4:12b-it-qat}"
OLLAMA_URL="http://localhost:11434"

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
fail() { printf "\n\033[31m✗ %s\033[0m\n" "$*"; exit 1; }

# ---------------------------------------------------------------- Python
say "Kiểm tra Python 3"
command -v python3 >/dev/null || fail "Chưa có python3. Cài bằng:  xcode-select --install   (hoặc: brew install python)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || fail "Cần Python 3.9 trở lên (đang có: $(python3 --version 2>&1))."
echo "   $(python3 --version)"

say "Tạo môi trường ảo .venv và cài thư viện"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt
./.venv/bin/python -c 'import lxml; print("   lxml", lxml.__version__)'

if [ "$MODEL" = "lmstudio" ]; then
  say "Xong phần Python."
  cat <<'EOF'
   Trong LM Studio: tải một model bản MLX 4-bit (12–14B), vào tab Developer, bật server (cổng 1234).
   Rồi kiểm tra:
     python3 epub_translate.py check --backend openai --model "<tên model trong LM Studio>"
EOF
  exit 0
fi

# ---------------------------------------------------------------- Ollama
say "Kiểm tra Ollama"
if ! command -v ollama >/dev/null; then
  if command -v brew >/dev/null; then
    echo "   Chưa có Ollama — cài qua Homebrew…"
    brew install --cask ollama
  else
    fail "Chưa có Ollama. Tải tại https://ollama.com/download , mở app một lần, rồi chạy lại script này."
  fi
fi
echo "   $(ollama --version 2>/dev/null | head -1)"

if ! curl -s -m 3 "$OLLAMA_URL/api/tags" >/dev/null; then
  echo "   Đang khởi động Ollama…"
  open -a Ollama 2>/dev/null || (nohup ollama serve >/dev/null 2>&1 &)
  for _ in $(seq 1 30); do
    curl -s -m 2 "$OLLAMA_URL/api/tags" >/dev/null && break
    sleep 1
  done
  curl -s -m 2 "$OLLAMA_URL/api/tags" >/dev/null || fail "Ollama không khởi động được. Mở app Ollama bằng tay rồi chạy lại."
fi

say "Tải model $MODEL (vài GB, chỉ cần một lần)"
ollama pull "$MODEL"

# ---------------------------------------------------------------- Kiểm tra
say "Dịch thử một câu để kiểm tra"
# Gọi bằng python3 hệ thống: script tự chuyển sang .venv — đúng cách bạn sẽ dùng hằng ngày.
python3 epub_translate.py check --model "$MODEL" || true

# Model mặc định của script là gemma4:12b-it-qat; model khác thì nhắc thêm --model.
M=""
[ "$MODEL" = "gemma4:12b-it-qat" ] || M=" --model $MODEL"

cat <<EOF

✓ Cài đặt xong. Dùng python3, không cần kích hoạt .venv (xem README.md):

  # Một lệnh làm tất cả: kiểm tra → bảng nhân vật → dịch → chấm + tự sửa → đóng gói.
  # Dừng lúc nào cũng được; chạy lại đúng lệnh là làm tiếp.
  caffeinate -i python3 epub_translate.py run sach.epub$M --open

  # Chạy thử cả pipeline trên 40 đoạn trước:
  python3 epub_translate.py run sach.epub$M --max-segments 40 --open

  # Hoặc chạy từng bước: info, check, characters, translate, review, build
  python3 epub_translate.py --help
EOF
