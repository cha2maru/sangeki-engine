#!/usr/bin/env bash
# ブラウザ版（Pyodide）を web/dist に組み立てる。サーバー版（server.mjs ＋ ai_gm.py）はそのまま。
#   bash web/build.sh [--no-img]   → web/dist を静的に配信する（例: cd web/dist && python3 -m http.server 8784）
# 画像は play/assets（setup_images.sh）か originals/ の惨劇コモンズ5th から、盤面が使う4つのフォルダだけ写す（--no-img で写さない＝文字の表示）
set -euo pipefail
WEB="$(cd "$(dirname "$0")" && pwd)"; PLAY="$(dirname "$WEB")"; OUT="$WEB/dist"
mkdir -p "$OUT/data"
# 盤面: サーバー版と同じ board.html に shim.js を先に読み込ませ、画像を相対パスにする
python3 - "$PLAY/board.html" "$OUT/index.html" <<'PY'
import sys
t = open(sys.argv[1], encoding='utf-8').read()
t = t.replace("const IMG = '/img/';", "const IMG = 'img/';", 1)
t = t.replace('<script>\nconst IMG', '<script src="shim.js"></script>\n<script>\nconst IMG', 1)
assert 'shim.js' in t and "IMG = 'img/'" in t, '差し込む場所が見つからない（board.html が変わった）'
open(sys.argv[2], 'w', encoding='utf-8').write(t)
PY
cp "$WEB/shim.js" "$WEB/worker.js" "$OUT/"
cp "$PLAY/engine/data/characters_public.json" "$OUT/data/chars.json"
cp "$PLAY/engine/data/rules.json" "$PLAY/engine/data/roles.json" "$OUT/data/"
# Python のコード（エンジン・自動のプレイヤー・進行役）と脚本・重みを1つの zip に
python3 - "$PLAY" "$WEB" "$OUT/engine.zip" <<'PY'
import os, sys, zipfile
play, web, out = sys.argv[1:4]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(play, 'ai_gm.py'), 'ai_gm.py')
    z.write(os.path.join(web, 'web_gm.py'), 'web_gm.py')
    for top in ('engine', 'selfplay', 'tuned'):
        for root, dirs, files in os.walk(os.path.join(play, top)):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', 'runs', 'fixtures')]
            for f in files:
                if f.endswith(('.py', '.json')) and not f.startswith('toukou'):
                    p = os.path.join(root, f)
                    z.write(p, os.path.relpath(p, play))
    # 公開版の並び: 設計・自動生成の脚本と重みは隣の sangeki-scripts にある → zip の engine/scripts・tuned に入れる
    sib = os.path.join(os.path.dirname(play), 'sangeki-scripts')
    for sub, dst in (('scripts', 'engine/scripts'), ('tuned', 'tuned')):
        base = os.path.join(sib, sub)
        for root, dirs, files in os.walk(base):
            for f in files:
                if f.endswith('.json'):
                    p = os.path.join(root, f)
                    name = os.path.join(dst, os.path.relpath(p, base))
                    if name not in z.namelist():
                        z.write(p, name)
    z.writestr('records/.keep', '')  # 伏せて選んだ脚本の記録の置き場所（ブラウザの中だけ）
print('engine.zip', os.path.getsize(out) // 1024, 'KB')
PY
# --local-pyodide: Pyodide を dist/pyodide に同梱する（CDN に届かない環境の試験用。?pyodide=local で開く）。PYODIDE_DIR に npm の pyodide パッケージ
if [[ " $* " == *" --local-pyodide "* ]]; then
  : "${PYODIDE_DIR:?PYODIDE_DIR に npm の pyodide パッケージ（node_modules/pyodide）を指定する}"
  mkdir -p "$OUT/pyodide"; cp "$PYODIDE_DIR"/{pyodide.mjs,pyodide.asm.mjs,pyodide.asm.wasm,python_stdlib.zip,pyodide-lock.json} "$OUT/pyodide/"
  cp "$PYODIDE_DIR"/*.whl "$OUT/pyodide/" 2>/dev/null || true
  echo "pyodide: 同梱"
fi
if [[ " $* " != *" --no-img "* ]]; then
  for src in "$PLAY/assets/tragedy_commons_5th" "$PLAY/../originals/tragedy_commons_5th"; do
    if [ -d "$src/board" ]; then
      mkdir -p "$OUT/img"
      for d in action_cards board tokens chara_stand; do cp -r "$src/$d" "$OUT/img/"; done
      echo "img: $src"; break
    fi
  done
fi
echo "→ $OUT"
