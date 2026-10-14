# 窓口（Env）― 外からエージェントで遊ぶ

対戦エンジンを、**判断を1件ずつ受け取って答える** 形で使うための窓口です。主人公・脚本家のどちらか（または両方）を自分のプログラムで操作し、
残りは自動のプレイヤー（`calcD` など）に任せられます。

> ルールそのものの説明は含みません。惨劇RoopeR の基本ルールを知っている前提です。

- **選べる手（options）はエンジンが出します。** エージェントはその中から1つ返すだけで、反則の手は作れません。
- **主人公には公開の情報しか渡りません。** 役職・犯人・脚本家の伏せ札の中身は、主人公の判断には入りません（試合の終わりに `reveal` で明かされます）。
- 対応しているのは Basic Tragedy X の脚本です（`engine/data/sets/BTX.json`）。

コードは `env.py`、見本は `agents/` にあります。どれも `play/` で実行します（公開版ではリポジトリの根）。

## 1. 使い方は2通り

### Python から（同じプロセス）

```python
from env import Env

env = Env('s03_bomb', mm='calcD')      # 脚本家は自動（calcD）、主人公は外から
d = env.reset(seed=1)
while d['kind'] not in ('end', 'error'):
    d = env.step(d['options'][0])      # options の要素を1つ、そのまま返す
print(d['result'])
env.close()
```

- `Env(script, mm=None, pc=None, replay=None)`: `script` は脚本 ID（`s03_bomb`、`designed/d16` など）。
  `mm`・`pc` に自動のプレイヤーの名前を入れた側は自動、`None` の側が外からの操作になります（両方 `None` なら両方とも外から）。
- `reset(seed)` で試合を始め、最初の判断を返します。`step(choice)` で答え、次の判断を返します。
- 見本: `python agents/random_agent.py --script s03_bomb --mm calcD`

### JSON を1行ずつ（どの言語からでも）

```bash
python env.py --stdio --script s03_bomb --mm calcD
```

- 判断を **1行1つの JSON** で標準出力に出します。
- 標準入力に1行で答えます。**選べる手の番号**（`0`、`3` など）か、**手そのものの JSON**（`{"by":"A","target":"C01","card":"GW1"}`、`null` など）。
- `kind` が `end` か `error` の行を出したら終わります。
- 見本（Node）: `node agents/stdio_agent.mjs s03_bomb`（python の場所は環境変数 `PYTHON`）

コマンドラインで1手ずつ遊ぶ（LLM に遊ばせる）なら `python env.py --script … --mm calcD --file rec.jsonl [--answer 番号]` もあります。
1回の実行で次の判断と番号つきの選べる手を出し、記録を `rec.jsonl` に残します。

## 2. 判断（Decision）

```json
{"seq": 4, "side": "pc", "kind": "pc_card",
 "view": {"board": {…}, "sheet": {…}, "log": ["…"]},
 "options": [{"by": "A", "target": "C01", "card": "PAR+"}, …],
 "context": {"by": "A", "order": ["A", "B", "C"], "chosen": [], "mm_targets": ["C02", "B:CIT", "C04"]},
 "notes": []}
```

| 項目 | 中身 |
|---|---|
| `seq` | 判断の通し番号 |
| `side` | `pc`（主人公）か `mm`（脚本家） |
| `kind` | 判断の種類（3章） |
| `view` | その側に見える盤面と、前の判断から後に起きたこと（4章） |
| `options` | 選べる手。この要素のどれかを返す |
| `context` | 判断の状況（だれの番か、先に選んだ分など。種類ごとに3章） |
| `notes` | （`--stdio` のみ）直前の答えを受け付けなかった理由など |

**選べる手に無い答え**を返すと、同じ判断がもう一度出ます（理由は `env.notes`、`--stdio` では `notes`）。
主人公の札は3枚そろってから置き方を確かめ、反則（同じ場所に2人など）なら、その日の札を最初から選び直します。

試合が終わると `{"kind": "end", "result": {…}, "reveal": {"rules", "roles", "incidents"}, "view": {"log": […]}}` が来ます。
`result` は `{"winner": "protagonists"|"mastermind", "loop": ループ番号|"final"}`。
進行の途中で不具合があれば `{"kind": "error", "error": "…"}` です。

## 3. 判断の種類

### 主人公（`side: "pc"`）

| kind | いつ | 選べる手 | context |
|---|---|---|---|
| `pc_card` | 行動の札を置く。リーダーから1人1枚ずつ、1日に3回 | `{"by", "target", "card"}` | `by`（いまの主人公）・`order`・`chosen`（その日に先に選んだ札）・`mm_targets`（脚本家の伏せ札の置き場所） |
| `ability` | 主人公能力フェイズ。友好能力を1件ずつ宣言し、その場で解決される | `null`（宣言を終える）か `[人物, 能力の番号, 引数]` | なし |
| `final_guess` | 最後の戦い。役職の指摘を1件ずつ | `null`（指摘を終える＝残りは全員「パーソン」）か `{"char", "role"}` | `chosen`（先に指摘した分） |

`ability` の能力の番号は、そのキャラクターの友好能力の上から 0, 1, …（`engine/data/characters_public.json` の順）。
引数は能力ごとで、`{"target": "C03"}`、`{"target": "B:SCH"}` などです。

### 脚本家（`side: "mm"`）

| kind | いつ | 選べる手 | context |
|---|---|---|---|
| `loop_setup` | ループの準備（手先の初期エリア・学者の初期カウンターがある脚本だけ） | `{"init": {…}, "scholar": …}` | なし |
| `mm_card` | 行動の札を置く。1枚ずつ3回 | `{"by": "M", "target", "card"}` | `chosen` |
| `cultist_pick` | 行動解決フェイズ。カルティストが暗躍禁止を無視するか | 能力の使い方のリスト（`[]` は使わない） | なし |
| `mm_ability` | 脚本家能力フェイズ。クロマク・ミスリーダーなどの対象 | `null`（使わない）か対象 | `actor`（能力を持つ人物）・`ability` |
| `mm_rumor` | 脚本家能力フェイズ。不穏な噂（ルールX） | `null` かボード | なし |
| `mm_extra_uses` | 脚本家能力フェイズ。医者[友好2]を脚本家が使う・ご神木の強制 | 使い方のリスト | なし |
| `refuse` | 主人公の友好能力を拒否するか（友好無視の役職） | `false`・`true` | `char` |
| `incident_choice` | 事件の効果の対象 | 事件ごとの形（`{"target"}`、`{"from", "to"}` など） | `incident`（日・事件・犯人） |
| `killer_pick` | ターン終了フェイズの任意能力（キラー・タイムトラベラーなど） | 能力の使い方のリスト（`[]` は使わない） | なし |
| `choose_rule_x` | 情報屋の能力でルールXが2つ残ったとき、どちらを公開するか | ルールX | なし |

## 4. 見えるもの（view）

**主人公**: `{"board", "sheet", "log"}`

- `board`: 盤上の事実だけ。`loop`・`day`・`leader`、`characters`（`name`・`card`（カード番号、ID は `"C" + card`）・`limit`（不安臨界）・`area`・`alive`・
  `paranoia`・`goodwill`・`intrigue`・`guard`）、`boards`（ボードの暗躍）、`used`（使用済みの1ループ1回の札）、
  `incidents`（公開シートの事件予定。日と事件名だけで、犯人は無い）、`cards`（盤上の札。脚本家の札は中身が `null`）。
- `sheet`: 公開シートのうち盤面に無いもの（`set`・`loops`・`days`）。
- `log`: 前の判断から後に起きたことの公開ログ（日本語の文）。

**脚本家**: `{"state", "script", "log"}`。`state` は完全な盤面（役職の変化なども含む）、`script` は非公開シートです。

## 5. 記号

| 種類 | 記号 |
|---|---|
| エリア | `HOS` 病院・`SHR` 神社・`CIT` 都市・`SCH` 学校。ボードは `B:HOS` など |
| キャラクター | `C01`〜（`engine/data/characters_public.json`。盤面の `card` に `C` を付けたもの） |
| 主人公の札 | `PAR+` 不安+1・`PAR-` 不安-1・`GW1` 友好+1・`GW2` 友好+2・`INTX` 暗躍禁止・`MV_V` 移動↑↓・`MV_H` 移動←→・`MVX` 移動禁止 |
| 脚本家の札 | `PAR+`・`PAR-`・`PARX` 不安禁止・`GWX` 友好禁止・`INT1` 暗躍+1・`INT2` 暗躍+2・`MV_V`・`MV_H`・`MV_D` 移動斜め |
| 役職 | `PERSON`・`KEY`・`KILLER`・`KUROMAKU`・`CULTIST`・`TT`・`WITCH`・`FRIEND`・`MISLEADER`・`LOVERS`・`MAIN_LOVERS`・`SK`・`FACTOR` |

ルール・役職・事件の名前は `engine/data/rules.json`・`roles.json`・`incidents.json` にあります。

## 6. 記録と再生

`env.record` は判断の記録（`{"seq", "side", "kind", "choice"}` の並び）です。`Env(…, replay=record)` に渡すと、
記録の分は答えとして使い（自動の側も考えずに返します）、記録が尽きたところから普通に進みます。途中で切った試合の続きから遊べます。
