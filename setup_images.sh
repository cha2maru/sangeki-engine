#!/usr/bin/env bash
# 盤面の画像（惨劇コモンズ5th）を取得して play/assets/tragedy_commons_5th に置く。server.mjs はここを優先して配信する。
# 惨劇コモンズ作成: BakaFire・紺ノ玲（CC BY-SA 2.1 JP）http://bakafire.main.jp/rooper/sr_dl_04_sozai.htm
# 利用要綱は展開した readme.txt（Shift_JIS）を参照。惨劇RoopeR に関連する内容に限り利用できる。
#   使い方: bash play/setup_images.sh [置き場所（既定 play/assets）]
set -euo pipefail
URL=http://bakafire.main.jp/rooper/dl/tragedy_commons_5th.zip
DEST="${1:-$(cd "$(dirname "$0")" && pwd)/assets}"
mkdir -p "$DEST"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "取得: $URL（約130MB）"
curl -fL --progress-bar -o "$tmp/commons.zip" "$URL"
unzip -q "$tmp/commons.zip" -d "$tmp/x"
src=$(dirname "$(find "$tmp/x" -name readme.txt | head -1)")
[ -d "$src/chara_cards" ] || { echo "想定した中身（chara_cards/）が無い: $src" >&2; exit 1; }
rm -rf "$DEST/tragedy_commons_5th"
mv "$src" "$DEST/tragedy_commons_5th"
echo "配置: $DEST/tragedy_commons_5th"
