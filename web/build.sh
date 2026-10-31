#!/usr/bin/env bash
# ブラウザ版（Pyodide）を web/dist に組み立てる。サーバー版（server.mjs ＋ ai_gm.py）はそのまま。
#   bash web/build.sh [--no-img]   → web/dist を静的に配信する（例: cd web/dist && python3 -m http.server 8784）
# 根（web/dist/index.html）は検索向けのトップページ（web/landing.html）、対戦画面は web/dist/play/。共有したリプレイの URL（#p= など）は根から play/ へ送る
# 画像は play/assets（setup_images.sh）か originals/ の惨劇コモンズ5th から、盤面が使う4つのフォルダだけ写す（--no-img で写さない＝文字の表示）
set -euo pipefail
WEB="$(cd "$(dirname "$0")" && pwd)"; PLAY="$(dirname "$WEB")"; ROOT="$WEB/dist"; OUT="$ROOT/play"
mkdir -p "$OUT/data"
python3 - "$WEB/landing.html" "$WEB/release.json" "$ROOT/index.html" <<'PY'
# トップページに最近のリリースノートを差し込む（web/release.json は Pages の組み立てで GitHub の API が書く、新しい順の配列。無ければ何も載せない）
import html, json, os, re, sys
t = open(sys.argv[1], encoding='utf-8').read()
sec = ''
if os.path.exists(sys.argv[2]):
    def line(x):  # リリースノートは「- 項目」の並び。**太字** だけ通す
        return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html.escape(x))
    rels = ''.join(f'<div class="rel"><p><a href="{html.escape(r["html_url"])}">{html.escape(r["name"] or r["tag_name"])}</a>（{r["published_at"][:10]}）</p>'
                   f'<ul>{"".join(f"<li>{line(x[2:])}</li>" for x in (r["body"] or "").splitlines() if x.startswith("- "))}</ul></div>'
                   for r in json.load(open(sys.argv[2], encoding='utf-8'))[:5] if not r.get('draft'))
    sec = f'<section>\n    <h2>最近の更新</h2>\n    <div class="rels">{rels}</div>\n  </section>' if rels else ''
open(sys.argv[3], 'w', encoding='utf-8').write(t.replace('<!--RELEASE-->', sec, 1))
PY
cp -r "$WEB/shots" "$ROOT/"  # トップページのゲーム画面（惨劇コモンズ5th を含む画面写し。CC BY-SA 2.1 JP）
printf 'User-agent: *\nAllow: /\nSitemap: https://cha2maru.github.io/sangeki-engine/sitemap.xml\n' > "$ROOT/robots.txt"
printf '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n<url><loc>https://cha2maru.github.io/sangeki-engine/</loc></url>\n</urlset>\n' > "$ROOT/sitemap.xml"
# 画像は表示の大きさに縮めた WebP にする（原寸の PNG は盤面1枚 1MB・合計 16MB で、読み込みが遅かった。ユーザー指摘 2026-10-22）。Pillow が無ければ原寸の PNG のまま
IMGX=.png; python3 -c 'import PIL' 2>/dev/null && IMGX=.webp
# 盤面: サーバー版と同じ board.html に shim.js を先に読み込ませ、画像を相対パスにする
python3 - "$PLAY/board.html" "$OUT/index.html" "$IMGX" <<'PY'
import sys
t = open(sys.argv[1], encoding='utf-8').read()
t = t.replace("const IMG = '/img/', IMGX = '.png';", f"const IMG = 'img/', IMGX = '{sys.argv[3]}';", 1)
t = t.replace('<script>\nconst IMG', '<script src="shim.js"></script>\n<script>\nconst IMG', 1)
assert 'shim.js' in t and "IMG = 'img/'" in t, '差し込む場所が見つからない（board.html が変わった）'
open(sys.argv[2], 'w', encoding='utf-8').write(t)
PY
cp "$WEB/shim.js" "$WEB/worker.js" "$WEB/helper.js" "$OUT/"
cp "$PLAY/engine/data/characters_public.json" "$OUT/data/chars.json"
cp "$PLAY/engine/data/rules.json" "$PLAY/engine/data/roles.json" "$PLAY/engine/data/incidents.json" "$OUT/data/"
# シナリオエディタ: shim.js（検証は Worker の Python）を先に読み込ませる
python3 - "$PLAY/editor.html" "$OUT/editor.html" <<'PY'
import sys
t = open(sys.argv[1], encoding='utf-8').read()
t = t.replace('<script>\nconst EDITOR', '<script src="shim.js"></script>\n<script>\nconst EDITOR', 1)
assert 'shim.js' in t, '差し込む場所が見つからない（editor.html が変わった）'
open(sys.argv[2], 'w', encoding='utf-8').write(t)
PY
cp "$PLAY/open.html" "$OUT/open.html"  # 公開シート（URL の ?o= だけから描く。サーバーも Python も使わない）
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
      rm -rf "${OUT:?}/img"; mkdir -p "$OUT/img"
      if [ "$IMGX" = .webp ]; then
        # 縮める大きさ（長い辺ではなく、表示の最大の約2倍）: 盤面 幅1060・札 幅260・立ち絵 高さ320・トークン 幅96
        python3 - "$src" "$OUT/img" <<'PY'
import os, sys
from PIL import Image
src, out = sys.argv[1:3]
SIZE = {'board': ('w', 1060), 'action_cards': ('w', 260), 'chara_stand': ('h', 320), 'tokens': ('w', 96)}
for d, (side, n) in SIZE.items():
    os.makedirs(os.path.join(out, d), exist_ok=True)
    for f in os.listdir(os.path.join(src, d)):
        if not f.endswith('.png'):
            continue
        im = Image.open(os.path.join(src, d, f)).convert('RGBA')
        k = n / (im.width if side == 'w' else im.height)
        if k < 1:
            im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        im.save(os.path.join(out, d, f[:-4] + '.webp'), 'WEBP', quality=82, method=6)
# 共有したときのカード表示（OGP 1200×630）: 惨劇コモンズ5th の盤面4枚を並べる（ロゴは使わない。画像はリポジトリに入れず、組み立てで作る）
from PIL import ImageOps
boards = [Image.open(os.path.join(src, 'board', b + '.png')).convert('RGB') for b in ['hospital', 'shrine', 'city', 'school']]
# 共有の画像: 1200×630 の枠に合わせ、各盤面を縦横比を保って中央で切り取る（引き伸ばさない）
og = Image.new('RGB', (1200, 630), (20, 19, 18))
for i, t in enumerate(boards):
    og.paste(ImageOps.fit(t, (600, 315), Image.LANCZOS), ((i % 2) * 600, (i // 2) * 315))
og.save(os.path.join(os.path.dirname(os.path.dirname(out)), 'og.jpg'), 'JPEG', quality=85, optimize=True)
PY
      else
        for d in action_cards board tokens chara_stand; do cp -r "$src/$d" "$OUT/img/"; done
      fi
      echo "img: $src ($IMGX)"; break
    fi
  done
fi
# Google アナリティクス（GA4）: 公開する各ページの <meta charset> の直後に差し込む（board.html などはサーバー版と共通なので元は書き換えない）。
# localhost・127.0.0.1・file://（ホスト名なし）では読み込まない＝開発やテストプレイを数えない
python3 - "$ROOT/index.html" "$OUT/index.html" "$OUT/editor.html" "$OUT/open.html" <<'PY'
import sys
GA = ('<script>(function () { var id = "G-9EJ5QSS1SQ"; var h = location.hostname;'
      ' if (!h || h === "localhost" || h === "127.0.0.1") return;'
      ' var s = document.createElement("script"); s.async = true; s.src = "https://www.googletagmanager.com/gtag/js?id=" + id; document.head.appendChild(s);'
      ' window.dataLayer = window.dataLayer || []; window.gtag = function () { dataLayer.push(arguments); };'
      ' gtag("js", new Date()); gtag("config", id); })();</script>')
for path in sys.argv[1:]:
    t = open(path, encoding='utf-8').read()
    assert '<meta charset="utf-8">' in t, f'差し込む場所が見つからない（{path}）'
    open(path, 'w', encoding='utf-8').write(t.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + GA, 1))
PY
echo "→ $ROOT（トップページ）・$OUT（対戦画面）"
