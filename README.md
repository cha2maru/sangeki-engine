# sangeki-engine

ボードゲーム **「惨劇RoopeR」** を、ブラウザやコマンドラインで遊び、研究するための **非公式の二次創作ツール** です。

- **ウェブ対戦ツール**: ブラウザの盤面で **主人公3人を操作して、自動の脚本家と対戦** できます（対AIモード。自分のPCでサーバーを動かして遊ぶ形です）
- ルール（Basic Tragedy Χ）を処理するエンジンと、自動の脚本家・主人公の思考ルーチンを含みます
- 自動どうしの対戦表、次の一手問題、重みの自動調整など、AI を強くするための道具もそろっています

**ブラウザですぐ遊ぶ**: https://cha2maru.github.io/sangeki-engine/ （インストール不要。Chrome・Edge の最近の版。初回は約30MB の読み込み）
自動の脚本家はブラウザを動かしている PC で計算するので、速さは PC の性能に左右されます。脚本家の種類によっては手の計算で待たされます（calcG は1手に数十秒かかることがあります）。

> 原作「惨劇RoopeR」は BakaFire Party の作品です。本リポジトリは BakaFire Party とは関係のないファンの制作物で、
> 公式の「二次創作のガイドライン」に従って公開しています（[原作と出典](#原作と出典)）。
> **遊ぶにはルールを知っている必要があります。** ルールの説明は含みません。製品版をお持ちの方を想定しています。

## 目的

惨劇RoopeR を、人と AI がいろいろな組み合わせで遊び、研究できるようにすることを目指しています。

1. **LLM との対戦** ― 大規模言語モデル（LLM）の AI エージェントが、主人公や脚本家としてゲームに参加できるようにする。
   コマンドラインの対戦（`selfplay/play_cli.py`）は「盤面を表示して止まり、選んだ手を1行足して再実行する」形なので、
   LLM がそのまま遊べます。人が進行役を務める手動モード（`gm.py`）では、LLM に進行役（ゲームマスター）を任せることもできます
2. **LLM を使わないロジックとの対戦** ― 探索と計算だけで動く自動の脚本家・主人公（`calcG`・`tuned-pc_r2` など）と、
   ブラウザやコマンドラインで対戦できるようにする。LLM もネットワークも要らず、手元の CPU だけで動きます
3. **機械学習による判断** ― 自動どうしの対戦や次の一手問題からデータを作り、手や局面の良し悪しを学ぶ・重みを調整する。
   そのためのエンジン、データ作り、評価の道具をそろえています（[`docs/research.md`](docs/research.md)）

3つは互いに支え合います。LLM との対戦で自動のプレイヤーの弱点を見つけ、その局面を次の一手問題にしてロジックや重みを直し、
直した自動のプレイヤーをまた LLM や人と対戦させる、という循環で強くしてきました。

---

## 目次

- [目的](#目的)
- [必要なもの](#必要なもの)
- [インストール](#インストール)
- [はじめての1試合（対AIモード）](#はじめての1試合対aiモード)
- [遊べる脚本](#遊べる脚本)
- [コマンドラインで遊ぶ](#コマンドラインで遊ぶ)
- [自動のプレイヤー](#自動のプレイヤー)
- [AI の研究用の道具](#ai-の研究用の道具)
- [構成](#構成)
- [できないこと・既知の制限](#できないこと既知の制限)
- [原作と出典](#原作と出典)
- [連絡先](#連絡先)
- [ライセンス](#ライセンス)
- [English summary](#english-summary)

---

## 必要なもの

| もの | 版 | 用途 |
|---|---|---|
| Python | 3.11 で確認 | エンジン・自動のプレイヤー |
| Node.js | 18 以上（22 で確認） | 盤面のサーバー（依存パッケージなし） |
| ブラウザ | Chrome・Edge・Firefox など | 盤面の表示と操作 |
| curl・unzip | — | 盤面の画像の取得 |

OS は Linux・macOS・WSL2 で動く想定です（開発は WSL2 上の Linux）。

## インストール

```bash
git clone <このリポジトリ> sangeki-engine
cd sangeki-engine

python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

bash setup_images.sh              # 盤面の画像（惨劇コモンズ5th、約130MB）を assets/ に取得
python -m pytest -q tests         # 動作確認（数分。一部の試験は資料が無いため飛ばされます）
```

画像は公式の配布ページから取得します（リポジトリには入っていません）。画像が無くても動きますが、盤面が文字だけになります。

## はじめての1試合（対AIモード）

ターミナルを2つ使います。どちらもリポジトリの根で実行します。

**いちばん簡単な方法**: `SANGEKI_AI=1 SANGEKI_PYTHON=.venv/bin/python SANGEKI_GAME=game_ai PORT=8766 node server.mjs` を実行して http://localhost:8766/ を開くと、
開始画面で脚本と脚本家を選べます（詳しくは [`docs/browser.md`](docs/browser.md)）。進行役を自分で起動するなら、次の手順です。

**1. 盤面のサーバーを起動**

```bash
SANGEKI_GAME=game_ai PORT=8766 node server.mjs
```

**2. 進行役（エンジンと自動の脚本家）を起動**

```bash
python ai_gm.py --script s03_bomb --mm calcG --seed 1
```

**3. ブラウザで `http://localhost:8766/` を開く**

- 上の帯に、いまのループ・日・フェイズと、誰の入力を待っているかが出ます
- 脚本家の伏せ札は裏向きで盤面に置かれます
- **キャラクターやボードを右クリック** → 「主人公A が 〇〇 を置く」などを選ぶと「送信待ち」にたまります。3人分そろえて **『確定』**
- 友好能力の宣言、最後の戦いの役職の指摘も右クリックのメニューから行います
- 右側のログに、エンジンが処理した結果（公開情報だけ）が出ます。推理シートも盤面から開けます

画面の見方、1日の流れ、友好能力・最後の戦いの操作、推理シート、困ったときの対処は **[`docs/browser.md`](docs/browser.md)** にあります。

脚本家の思考には1手あたり数十秒〜1分ほどかかります。ゲームの状態は `game_ai/` に保存されます（最初からやり直すときは消してください）。

**オプション**

| オプション | 意味 |
|---|---|
| `--script <id>` | 脚本（[遊べる脚本](#遊べる脚本)） |
| `--mm <名前>` | 自動の脚本家（[自動のプレイヤー](#自動のプレイヤー)）。既定は `search` |
| `--seed <整数>` | 乱数の種 |
| `--blind` | 脚本を伏せて選ぶ（sangeki-scripts の自動生成の脚本から） |

## 遊べる脚本

このリポジトリには、動作確認用の脚本が8本入っています（すべて Basic Tragedy Χ）。

| id | 名前 | ループ | 日数 |
|---|---|---|---|
| `s01_seal` | 封じの社 | 3 | 7 |
| `s02_contract` | 約束の少女 | 4 | 5 |
| `s03_bomb` | 時限の魔女 | 3 | 6 |
| `s04_circle` | 友の輪と殺意 | 4 | 7 |
| `s05_future` | 羽ばたく恋 | 4 | 6 |
| `s06_school` | 閉じた教室 | 4 | 6 |
| `s07_shrine` | 社の宴 | 4 | 6 |
| `s08_misfits` | はぐれ者たちの時限 | 3 | 6 |

もっと遊びごたえのある脚本（設計した35本・自動生成した約180本）は、別のリポジトリ **sangeki-scripts** にあります。
脚本の中身（役職・犯人）が見えてしまうので分けています。隣に置くと自動で読み込みます。

```
作業ディレクトリ/
├── sangeki-engine/
└── sangeki-scripts/      ← 隣に置く（または SANGEKI_SCRIPTS=<場所>/scripts を指定）
```

```bash
python ai_gm.py --script designed/d16 --mm calcG     # sangeki-scripts の脚本
```

## コマンドラインで遊ぶ

ブラウザを使わず、1手ずつ自動の相手と対戦できます。対戦は「乱数の種＋それまでの自分の選択の一覧」から毎回再生され、
自分の番で盤面と選べる手を表示して止まります。

```bash
python -m selfplay.play_cli --side pc --opp calcG --script s03_bomb --seed 1 --file my_game.jsonl
```

表示を見て、選んだ手を `my_game.jsonl` に JSON で1行足し、同じコマンドを再実行します。

```json
{"kind": "pc_cards", "choice": [{"by": "A", "target": "C03", "card": "INTX"}, {"by": "B", "target": "B:CIT", "card": "PAR-"}, {"by": "C", "target": "C05", "card": "GW1"}]}
```

| オプション | 意味 |
|---|---|
| `--side pc` | 自分が主人公（相手は `--opp` の脚本家） |
| `--side mm` | 自分が脚本家（相手は `--opp` の主人公） |
| `--side both` | 両方を自分で（`--mm-file`・`--pc-file`・`--as`） |

書式の全体（能力の宣言、最後の戦い、脚本家の各選択）は `selfplay/play_cli.py` の冒頭にあります。
主人公側の表示は公開情報だけです。

## 自動のプレイヤー

名前で指定します（定義は `selfplay/league.py` の `make`）。主人公側の自動のプレイヤーは、脚本の中身を知らず、公開情報だけで推理します。

### おすすめ

| 用途 | 脚本家 | 主人公 |
|---|---|---|
| **いちばん強い相手と遊ぶ・測る** | `calcG` | `tuned-pc_r2`（要 sangeki-scripts） |
| **素早く動かす**（試し遊び・動作確認・大量の対戦） | `search` | `read` |
| 最速（ルールの確認用。弱い） | `route` | `blind` |

### 考え方と速さ

1手（1日の札を決める）にかかる時間の目安です（AMD Ryzen 7 255 の1コア、相手は素早い型、1試合を計測。脚本や局面によって変わります）。

| 名前 | 側 | 1手の平均（最大） s03_bomb | 1手の平均（最大） designed/d16 | 考え方 |
|---|---|---|---|---|
| `calcG` | 脚本家 | 6 秒（25 秒） | 6 秒（42 秒） | **現行最強**（仕組みは [`docs/calcG.md`](docs/calcG.md)）。探索に負け筋の逆算を重ねる: 必ず取れるループはその手順を最優先、取れないときは伏せ札の二択で賭ける、取ったループは押さずに隠す |
| `searchL` | 脚本家 | 5 秒（24 秒） | — | 探索（主人公の応手を読み、その日の脚本家能力・事件まで進めて評価） |
| `search` | 脚本家 | 0.3 秒（3 秒） | 0.6 秒（6 秒） | **素早い**。探索の基本形（対AIモードの既定） |
| `route` | 脚本家 | 0.01 秒 | — | 負け筋に近づく手を1手先だけ読む。主人公の妨害を考えない |
| `tuned-pc_r2` | 主人公 | 1.9 秒（8 秒） | 2.2 秒（8 秒） | **現行最強**。推理＋その日の事件までの先読み。重みを次の一手問題で調整したもの（重みは sangeki-scripts の `tuned/pc_r2.json`） |
| `readlook` | 主人公 | 1.3 秒（4 秒） | — | 推理＋その日の事件までの先読み（調整前） |
| `read` | 主人公 | 0.6 秒（3 秒） | 0.8 秒（7 秒） | **素早い**。推理（仮説を引き、伏せ札の置き場所から脚本家の狙いを読む） |
| `blind` | 主人公 | 0.2 秒（3 秒） | — | 推理の基本形 |

1試合（自動どうし）の時間は、`calcG` 対 `tuned-pc_r2` で約2分、`search` 対 `read` で十数秒です。
どのループも初日がいちばん重く、日が進むほど速くなります。

### 指定のしかた

| 使う場所 | 指定 |
|---|---|
| 対AIモード（脚本家） | `python ai_gm.py --mm calcG`（既定は `search`） |
| コマンドラインで対戦（相手） | `python -m selfplay.play_cli --side pc --opp calcG …`（自分が主人公）／`--side mm --opp tuned-pc_r2 …`（自分が脚本家） |
| 自動どうしの対戦表 | `python -m selfplay.league --pairs calcG:tuned-pc_r2 search:read …`（`脚本家:主人公` の組を並べる） |
| 次の一手問題 | `python -m selfplay.positions … --mm calcG search --pc tuned-pc_r2 read` |

`make` には実験の途中で作った型が多数あります。現役の一覧と研究での使い分けは [`docs/research.md`](docs/research.md) を見てください。

## AI の研究用の道具

自動のプレイヤーを強くするための道具です。**詳しい使い方と結果の読み方は [`docs/research.md`](docs/research.md)** にあります（対戦表の指標、次の一手問題の作り方、重みの調整、負け筋の逆算、推理、脚本の生成）。CPU を多く使うので `--procs` で並列数を調整してください。

```bash
# 自動どうしの対戦表（勝敗・最後の戦い・主人公に割れた役職の数など）
python -m selfplay.league --games 4 --seed 1 --pairs calcG:read --script s03_bomb --name smoke --procs 2

# 次の一手問題（sangeki-scripts の根で実行）
PYTHONPATH=../sangeki-engine python -m selfplay.positions positions/*.json --mm calcG --pc tuned-pc_r2 --samples 3

# 次の一手問題の点で重みを自動調整（進化戦略）
PYTHONPATH=../sangeki-engine python -m selfplay.tune positions/*.json --side mm --base calcG --iters 10 --procs 4 --out tune_mm.jsonl
```

結果は `selfplay/runs/`（対戦表）などに書かれます（git の管理外）。

## 構成

| 場所 | 中身 |
|---|---|
| `engine/phases.py`・`resolve.py`・`abilities.py` | ゲームの進行（フェイズ）、行動カードの解決、キャラクターの友好能力・特性 |
| `engine/routes.py` | 主人公の負け筋の列挙（あと何が足りないか） |
| `engine/route_calc.py` | 負け筋の逆算（そのループを脚本家が必ず取れるか、伏せ札の賭けの確率） |
| `engine/deduce.py` | 推理（公開情報と矛盾しないルール・役職の組み合わせを絞る） |
| `engine/data/` | キャラクター・役職・ルール・事件・カードのデータ（[出典](#原作と出典)） |
| `selfplay/` | 自動のプレイヤー（`search_mm.py`・`pc_v2.py`）、対戦表（`league.py`）、問題（`positions.py`）、調整（`tune.py`） |
| `ai_gm.py`・`server.mjs`・`board.html`・`sheet.html` | 対AIモード（進行役・盤面サーバー・盤面・推理シート） |
| `gm.py` | 手動モード（人やAIエージェントが進行役を務めるときの、記録とハッシュ固定の補助） |
| `tests/` | 試験 |

## できないこと・既知の制限

- 惨劇セットは **Basic Tragedy Χ だけ** です。収録したキャラクター35人のうち、上位存在の特性・友好能力は未対応です
- 画面とメッセージは **日本語だけ** です
- 一部の細かい裁定は、エンジンの判断で処理しています（公式の裁定を確認できていない箇所があります）
- 脚本家 `calcG` は1手に数十秒〜1分かかります
- 対AIモードは1人で主人公3人を操作する形です（複数人での対戦や、人どうしのネット対戦はありません）
- 開発中のため、コマンドや形式は予告なく変わることがあります

不具合や裁定の誤りを見つけたら、Issue で教えてください。

## 原作と出典

- **原作**: 惨劇RoopeR（BakaFire Party）
  公式サイト: http://bakafire.main.jp/rooper/sr_top.htm
- **二次創作のガイドライン**: http://bakafire.main.jp/rooper/sr_dl_04_sozai.htm
  ガイドラインの具体例「Basic Tragedy Χ やカード中に記載された能力を転載したうえでネット対戦ツールを作成する場合」に従い、
  転載した部分の出典と原作を以下に明記します。
- **転載・要約したデータの出典**: `engine/data/*.json` と、コード中のルール・能力の説明は、
  惨劇RoopeR（BakaFire Party）の主人公の書・脚本家の書・早見表（Basic Tragedy Χ）・各キャラクターカード・行動カードによります。
- **盤面の画像**: 惨劇コモンズ5th（リポジトリには含みません。`setup_images.sh` が公式の配布ページから取得します）
  惨劇コモンズ作成: **BakaFire**、**NEKOG(紺ノ玲)** ／ [CC BY-SA 2.1 JP](http://creativecommons.org/licenses/by-sa/2.1/jp/) ／
  http://bakafire.main.jp/rooper/sr_dl_04_sozai.htm

## 連絡先

作者: cha2maru（X: [@withoutcane](https://x.com/withoutcane) ／ GitHub: [cha2maru/sangeki-engine](https://github.com/cha2maru/sangeki-engine)）。不具合や裁定の誤りは [GitHub の Issue](https://github.com/cha2maru/sangeki-engine/issues) か X へ。

## ライセンス

このプロジェクトで書いたコードは **MIT ライセンス**（[`LICENSE`](LICENSE)）です。
ゲームのルール・カードの文面・画像の権利は原作者（BakaFire Party）にあります。転載したデータの利用は、上記のガイドラインに従ってください。

---

## English summary

**sangeki-engine** is an unofficial fan-made toolkit for the board game *Tragedy Looper* (惨劇RoopeR) by BakaFire Party.
Its goals are (1) letting LLM-based agents play the game as Protagonists, Mastermind or game master, (2) playing against
non-LLM AI players that run on the CPU only (search plus exact route calculation), and (3) machine learning of moves and
positions from self-play data and next-move puzzles.
It contains a rules engine for the *Basic Tragedy X* set, AI players for both the Mastermind and the Protagonists, a browser
UI where you control the three Protagonists against an AI Mastermind, and tools for training and evaluating the AIs
(round-robin leagues, next-move puzzles, weight tuning).

- Quick start: `pip install -r requirements.txt`, `bash setup_images.sh`, then run
  `SANGEKI_GAME=game_ai PORT=8766 node server.mjs` and `python ai_gm.py --script s03_bomb --mm calcG`, and open `http://localhost:8766/`.
- The UI and messages are in Japanese only. You need to know the rules (a copy of the game is assumed).
- Game data is transcribed from the rulebooks and cards with attribution, as permitted by the publisher's fan-work guideline.
  Board images come from *Tragedy Commons 5th* (by BakaFire and NEKOG, CC BY-SA 2.1 JP), downloaded by `setup_images.sh`.
- Code: MIT License. Game rules, card texts and images belong to BakaFire Party.
