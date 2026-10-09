# AI の研究用の道具

自動の脚本家・主人公を強くし、その強さを測るための道具の使い方です。コマンドはすべてリポジトリの根で実行します
（次の一手問題だけは sangeki-scripts の根で実行します）。

計算は CPU を多く使います。`--procs` で並列数を調整してください。結果の多くは `selfplay/runs/`（git の管理外）に書かれます。

## 目次

1. [考え方: 何を「強い」とするか](#1-考え方-何を強いとするか)
2. [自動のプレイヤーの一覧](#2-自動のプレイヤーの一覧)
3. [対戦表（league）と結果の読み方](#3-対戦表leagueと結果の読み方)
4. [次の一手問題（positions）](#4-次の一手問題positions)
5. [重みの自動調整（tune）](#5-重みの自動調整tune)
6. [負け筋の列挙と逆算（routes・route_calc）](#6-負け筋の列挙と逆算routesroute_calc)
7. [推理（deduce）](#7-推理deduce)
8. [脚本の自動生成と評価](#8-脚本の自動生成と評価)
9. [学習データと模型（試作）](#9-学習データと模型試作)
10. [おすすめの進め方](#10-おすすめの進め方)

---

## 1. 考え方: 何を「強い」とするか

勝率だけでは測りません。脚本によって主人公有利・脚本家有利の差が大きく、勝率は脚本の性質に引っぱられるからです。代わりに次を見ます。

- **主人公の強さ**: 公開情報から、真相（ルール・役職・犯人）にどこまで迫れたか。最後の戦いで当てられたか。
- **脚本家の強さ**: ループを取りながら、確定する情報をどれだけ隠せたか。最後の戦いを「勘の勝負」以上に持ち込めたか。

目標とする試合は「どちらの側も強く、最後の戦いまでもつれる」ものです。

また、自動の対戦表の数字は「壊れていないか」の確認に使い、改良の採否は人（や AI エージェント）が実際に遊んだ結果と、
次の一手問題で判断する、という運用をしています。自動どうしの対戦は、相手の弱点に合わせた改良を「強くなった」と見誤りやすいためです。

## 2. 自動のプレイヤーの一覧

名前で指定します（`selfplay/league.py` の `make`）。`make` には実験の途中で作った型が多数ありますが、現役は次のとおりです。

**脚本家**

| 名前 | 中身 |
|---|---|
| `calcG` | 現在いちばん強い。`searchL` に負け筋の逆算を重ねたもの。① 必ず取れるループは、その手順の札を最優先で置く ② 取れないときは、伏せ札の中身が見えないことを使った二択の賭け（確率が 0.5 以上なら）③ 取ったループは押す札をやめ、情報の能力の閾値の手前に友好禁止を置いて隠す |
| `calcL` | `calcG` から賭けを除いたもの |
| `searchL` | 探索。主人公の応手を読み、その日の脚本家能力・事件まで進めて評価する |
| `search` | 探索の基本形（対AIモードの既定） |

**主人公**（脚本の中身を知らず、公開情報だけで推理する）

| 名前 | 中身 |
|---|---|
| `tuned-pc_r2` | 現在いちばん強い。`readlookQ2` の重みを次の一手問題で調整したもの（重みは sangeki-scripts の `tuned/pc_r2.json`） |
| `readlook` | 推理＋その日の事件まで進めて手を評価する |
| `read` | 推理（仮説を引き、伏せ札の置き場所から脚本家の狙いを読む） |
| `oread` | 脚本を知っている主人公（上限の目安。脚本の検査に使う） |

`tuned-<名前>` は `tuned/<名前>.json` を読み込みます（[5](#5-重みの自動調整tune)）。

## 3. 対戦表（league）と結果の読み方

```bash
python -m selfplay.league --games 10 --seed 1 --pairs calcG:tuned-pc_r2 searchL:tuned-pc_r2 \
    --script designed/d16 --name d16_cmp --procs 4
```

| オプション | 意味 |
|---|---|
| `--pairs 脚本家:主人公 ...` | 対戦させる組（複数可） |
| `--games N` | 組ごとの試合数 |
| `--seed S` | 種。同じ種なら、改良していない側の手は同じ乱数で動く（対応のある比較ができる） |
| `--script ID` | 脚本 |
| `--compare 前回.json` | 前回の結果との差を出す |

結果は `selfplay/runs/league_<名前>.json` に、1行の要約が標準出力に出ます。主な項目:

| 項目 | 意味 |
|---|---|
| `pc_wins` | 主人公の勝ち（ループで勝った＋最後の戦いで勝った） |
| `pc_loop_wins` | ループで勝った数 |
| `finals` | 最後の戦いまで行った数 |
| `final_pc_wins` | 最後の戦いで主人公が勝った数 |
| `final_wins_deduced` / `final_wins_guessed` | 最後の戦いの勝ちのうち、推理で確定していた／勘で当てた |
| `final_guess` / `final_hidden` | 最後の戦いで、勘の勝負になった（真相まで 1〜3 ビット）／勘でもほぼ届かない（3 ビット以上）数。脚本家はこれを増やしたい |
| `final_bits` | 最後の戦いの時点で、真の配役まで残っていた情報量（ビット、平均） |
| `confirmed` | 公開情報だけで役職が確定したキャラクターの数（平均）。主人公は増やしたい、脚本家は減らしたい |
| `confirmed_key_roles` | そのうち重要な役職の数 |
| `mean_true` | 真の役職の確率の、キャラクターごとの平均 |
| `nlog_p_truth` | 真の配役の確率の −log2（小さいほど真相に近い） |
| `declared_ok` | 最後の戦いで何人目まで正しく指摘できたか |
| `missed_confirmed` | 確定していたのに外した数（主人公の不具合の目印） |
| `truth_lost` | 推理が真相を消してしまった数。**0 でなければ推理の不具合** |
| `by_loop` | ループごとの `confirmed` と、役職の不確かさ（エントロピー） |
| `sec_per_game` | 1試合の秒数 |

「真相にどこまで迫れたか」は、公開の出来事だけを入れた推理（理想の観測者）で測ります（`engine/deduce.truth_metrics`）。

**2つの対戦表を試合ごとに比べる**（同じ種で回したもの）:

```bash
python -m selfplay.paired selfplay/runs/league_A.json selfplay/runs/league_B.json
```

組ごとに「A だけ勝った・B だけ勝った・両方」と、符号検定の p 値を出します。

**脚本の難易度の一覧**（これまでの対戦表から）:

```bash
python -m selfplay.script_difficulty
```

## 4. 次の一手問題（positions）

対戦の記録の特定の局面で、ロジックが良い手を選べるかを数分で測ります。対戦表よりずっと速く、狙った弱点を直接測れます。

### 解く

```bash
cd ../sangeki-scripts
PYTHONPATH=../sangeki-engine python -m selfplay.positions positions/*.json --mm calcG searchL --pc tuned-pc_r2 --samples 3
```

- `--mm`・`--pc`: 試す型（複数可）。片方の問題だけ解くときは、もう一方を空にする（`--mm` だけ書く）
- `--samples N`: 乱数を変えて N 回聞く。**主人公の問題は1回ごとの揺れが大きいので 3 回以上を勧めます**
- `--split tune|hold`: 調整用か確かめ用の問題だけ

出力は問題ごとの ○×、選んだ手、型ごとの正解数です。

### 問題の形

`positions/*.json` は問題の配列です。1問の形:

```json
{
  "id": "d16-l2d1-mm",
  "script": "designed/d16",
  "seed": 64,
  "mm": "positions/games/cvc_d16_s64_mm.jsonl",
  "pc": "positions/games/cvc_d16_s64_pc.jsonl",
  "at": {"loop": 2, "day": 1, "kind": "mm_cards"},
  "good": [[{"card": "INT2", "target": "C34"}, {"card": "INT1", "target": "B:CIT"}]],
  "bad": [[{"card": "INT2", "target": "B:CIT"}]],
  "note": "なぜそれが良い・悪いか",
  "split": "tune"
}
```

| 項目 | 意味 |
|---|---|
| `mm`・`pc` | 両陣営の決定の記録（`play_cli` の決定ファイル） |
| `mm_opp`・`pc_opp` | 片側だけ人が遊んだ記録のとき、相手の自動のプレイヤーの型（記録したときの `--opp`） |
| `at.kind` | `mm_cards`（脚本家の伏せ札）・`incident`（事件の選択）・`pc_cards`（主人公の札） |
| `good` | 組の一覧。**どれか1組の札を全部含めば正解**。省くと「悪手でなければ正解」 |
| `bad` | 組の一覧。どれか1組を全部含めば不正解 |
| `split` | `tune`（調整に使う）・`hold`（確かめに使う。合わせすぎを見る） |

記録をその局面の直前まで再生し、試すロジックは最初から観察者として一緒に見ます（推理や記憶が実戦と同じに育つ）。

### 問題を作る

1. `play_cli` で遊ぶ（または自動どうしの記録を使う）。決定ファイルが残る
2. 教えたい局面の `loop`・`day`・`kind` と、正解・不正解の札の組を書く
3. 片側だけ人が遊んだ記録なら、**相手の手を凍結する**:
   ```bash
   PYTHONPATH=../sangeki-engine python -m selfplay.positions positions/my.json --freeze
   ```
   自動のプレイヤーのコードを変えると、再生の途中で相手の手が変わって局面がずれます。凍結すると、相手の判断を
   `positions/games/frozen_*.pkl` に記録して以後はそれを読み返すので、ロジックを変えても問題が壊れません
4. 解いて、記録どおりの局面に届くかを確かめる

## 5. 重みの自動調整（tune）

次の一手問題の点で、ロジックの重みを CPU だけで探します（(1+λ) 進化戦略）。

```bash
cd ../sangeki-scripts
PYTHONPATH=../sangeki-engine python -m selfplay.tune positions/*.json --side pc --base readlookQ2 \
    --iters 15 --pop 8 --samples 2 --procs 4 --out tune_pc.jsonl
```

| オプション | 意味 |
|---|---|
| `--side mm|pc` | 調整する側（その側の問題だけを使う） |
| `--base 型` | 基準の型。重みはこの型の属性に差し込む |
| `--iters`・`--pop` | 世代数・1世代の候補数 |
| `--sigma` | 揺らぎの大きさ（重みの範囲を 0〜1 に正規化した空間で） |

- 調整する重みと範囲は `selfplay/tune.py` の `SPACE` にあります
- 問題ごとの再生は最初に1回だけ行い、以後は局面の手だけを聞くので速く回ります
- 出力の jsonl は1行1候補（`params`・`tune` の点・`hold` の点）。**`hold`（確かめ用）の点も上がっているか**で合わせすぎを見ます
- 最良の重みを使うには、`tuned/<名前>.json` を次の形で書きます:
  ```json
  {"base": "readlookQ2", "params": {"urgency": 1.0, "info_weight": 0.247}, "tune": 0.67, "hold": 0.45, "source": "tune_pc.jsonl"}
  ```
  以後 `tuned-<名前>` で呼べます。採るかどうかは対戦表（壊れていないか）と、実際に遊んだ結果で決めます

## 6. 負け筋の列挙と逆算（routes・route_calc）

**負け筋の列挙**（`engine/routes.py`）: 盤面から、主人公が負ける道筋（どの役職・事件・ルールで、あと何が足りないか）を数えます。

```python
from engine.routes import enumerate_routes
for r in enumerate_routes(state)[:5]:
    print(r['total'], r['id'], r['via'], r['goal'])   # total が小さいほど近い
```

**逆算**（`engine/route_calc.py`）: 負け筋のカウンター（不安・暗躍）だけを取り出した小さな盤面で、残りの日を後ろ向きに計算します。

```python
from engine.route_calc import forced_plan, block_rates
r = forced_plan(state, days, hidden=True)
r['forced']   # 主人公が伏せ札を知っていて最善に止めても、このループを脚本家が必ず取れるか
r['move']     # 取れるなら今日置く札 [(対象, 札), ...]
r['locked']   # 脚本家が残りの日に何もしなくても取れる（確定）
r['p'], r['mix']  # 伏せ札の中身が見えない主人公に対する、今日の賭けで取れる確率と、札の混ぜ方
block_rates(state, days, mm_targets)  # 主人公から見て: 応手ごとに、今日の後で脚本家に確定される割合
```

扱う範囲と限界:

- 札は種類ごとの枚数制限どおり（脚本家の暗躍+2 はループに1回、不安+1 は2枚、1日3枚・同じ対象に1枚、主人公の暗躍禁止は1日1か所だけ効く、など）
- **数えないもの**: キャラクターの能力、移動、事件でボードに暗躍を置く筋。位置の条件は「今揃っていればそのまま」とみなします
- 賭けの計算は1日分だけです（何日も続けて賭ける値は数えません）。位置の条件を含む筋は、主人公の移動で崩されるので賭けには数えません

## 7. 推理（deduce）

`engine/deduce.py` の `Deduction` は、公開情報と矛盾しない「ルールの組×配役」の仮説を全部持ち、観測のたびに絞ります。

- 観測: 事件の発生・不発、役職の公開、友好能力の拒否、ループの終わり方など
- 読み出し: `marginals()`（キャラクターごとの役職の確率）、`role_entropy()`、`sample()`（仮説を引く）
- 事件の犯人の候補は `incident_candidates` で別に絞ります（犯人は役職と別の情報なので）
- `truth_metrics` は真の脚本と比べた指標（対戦表の `confirmed` などのもと）

主人公の自動のプレイヤーは、ここから仮説（想定する脚本）を数十個引いて、それぞれの世界で手を評価します。

## 8. 脚本の自動生成と評価

```bash
python -m engine.generate --n 5 --seed 1          # 脚本家の書の手順で、ルール上正しい脚本を確率的に作る
python -m selfplay.gen_filter --n 30 --seed 1 --games 12   # 自己対戦でふるいにかけ、遊べる脚本を generated/ に書く
python -m selfplay.script_eval designed/d16      # 脚本の JSON だけで測れる「よい脚本」の項目の採点（脚本 ID を並べる）
python -m selfplay.script_routes                  # ループ1の開始時の負け筋の多さ（要の人物・ボードの種類）
```

`gen_filter` の採る条件: 脚本を知る主人公（`oread`）が一定以上勝てる（盤面で勝つ手がある）、かつ推理する主人公（`read`）が
勝ちすぎない（易しすぎない）。書き出し先は脚本の置き場所（sangeki-scripts があればそこ）の `generated/` です。

## 9. 学習データと模型（試作）

LightGBM で手や局面の良し悪しを学ぶ試みです。いずれも試作で、現役のプレイヤーには使っていません。

| 道具 | 中身 |
|---|---|
| `selfplay/branch_moves.py`・`branch_pc.py` | 同じ局面で候補の手を複製して先まで回し、手ごとの結果を集める |
| `selfplay/train_branch.py`・`train_moves.py` | 集めたデータで、探索の評価値と実際の結果の一致を調べ、LightGBM の順位付けと比べる |
| `selfplay/train_bandit.py` | 脚本家の情報の漏れの重み λ を、ループごとの結果から学ぶ |
| `selfplay/v_model.py` | 主人公の知識と残りのループから、主人公が最終的に勝つ確率を当てる模型 |
| `selfplay/train_difficulty.py` | 脚本の中身だけから難しさを当てる（生成した脚本の選別用） |

## 10. おすすめの進め方

1. 弱点を見つける: 自分で `play_cli` や対AIモードで遊ぶ（相手が何を見落としたかを書き留める）
2. その局面を次の一手問題にする（片側だけの記録なら `--freeze`）
3. ロジックを直し、問題で測る（主人公は `--samples 3` 以上。悪化した問題が無いかも見る）
4. 小さな対戦表（1〜2脚本×数試合）で壊れていないかを確かめる
5. もう一度遊んで、本当に強くなったかを確かめる

片側を強くしたら、もう片側の最新・最強版を相手に測り直してください（交互に強くする）。
