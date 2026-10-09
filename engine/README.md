# play/engine ― ルールエンジン（段0〜）

計画: `plan/engine-and-selfplay.md`（末尾の「レビューを受けた修正」節が優先）。

- ルールの処理だけを行う純粋な関数群。判断（どこに札を置くか、任意能力を使うか、事件の対象など）は外の「プレイヤー」から受け取る
- 手動モード（`play/server.mjs`・`board.html`・`gm.py`）はこれに依存しない。`gm.py` から使う場合は関数の中でだけ import する
- 材料は wiki の照合済みページ（frontmatter の verified に「再確認不要」）と originals の一次資料だけ。`data/*.json` の各項目に出典を書く
- 試験は `play/tests/`。実行: `/workspaces/sangeki/.venv/bin/python -m pytest play/tests -q`

## 統一語彙（ID）

表記ゆれ（`不安-1`／`不安−1`／`PAR-` など）をなくすため、エンジン・記録・試験はすべて次の ID を使う。
表示名への対応は `data/*.json` の `name` にある。

| 種類 | ID |
|---|---|
| プレイヤー | `M`（脚本家）、`A` `B` `C`（主人公） |
| エリア | `HOS` 病院、`SHR` 神社、`CIT` 都市、`SCH` 学校 |
| キャラクター | `C01`〜`C35`（カード番号。`chara_cards/character_NN`） |
| 札の対象 | キャラクター ID、または `B:HOS` のようにボード |
| 主人公の札 | `PAR+` 不安+1、`PAR-` 不安−1★、`GW1` 友好+1、`GW2` 友好+2★、`INTX` 暗躍禁止、`MV_V` 移動↑↓、`MV_H` 移動←→、`MVX` 移動禁止★ |
| 脚本家の札 | `PAR+`（2枚）、`PAR-`、`PARX` 不安禁止、`GWX` 友好禁止、`INT1` 暗躍+1、`INT2` 暗躍+2★、`MV_V`、`MV_H`、`MV_D` 移動斜め★ |
| 役職 | `PERSON` `KEY` `KILLER` `KUROMAKU` `CULTIST` `TT` `WITCH` `FRIEND` `MISLEADER` `LOVERS` `MAIN_LOVERS` `SK` `FACTOR` |
| ルール | `Y_MURDER` 殺人計画 … `data/rules.json` |
| 事件 | `MURDER` 殺人事件 … `data/incidents.json` |

★は1ループ1回。

## 決定点の記録（LightGBM 向けの前段）

プレイヤーが選ぶ場面はすべて、次の形で1行ずつ残す（`decisions.jsonl`）。

```json
{"loop":1,"day":2,"phase":"incident","who":"M","kind":"incident_target",
 "options":{...選べた候補...},"choice":{...実際の選択...},"state_ref":"L1D2:after_abilities","source":"log_L1 #6"}
```

`kind` の例: `place_cards`（伏せ札・主人公の札）、`mm_ability`（クロマク・ミスリーダーなど任意能力の使用と対象）、
`incident_target`（事件の効果の対象）、`refuse`（友好無視による拒否）、`ability`（主人公の友好能力の宣言）、`timespiral`、`final_guess`。

## 状態と入力の形（エンジン・試験・記録で共通）

完全な状態（エンジン内部）。公開用の `play/game/state.json` とは別で、射影して書き出す。

```json
{
 "loop": 1, "day": 1, "leader": "A",
 "chars": {"C03": {"area": "SCH", "alive": true, "par": 0, "gw": 0, "int": 0, "guard": 0}},
 "boards": {"HOS": 0, "SHR": 0, "CIT": 0, "SCH": 0},
 "used": {"M": [], "A": [], "B": [], "C": []},
 "script": {"rules": ["Y_MURDER", "X_THREAD", "X_VIRUS"], "roles": {"C01": "KUROMAKU"}, "incidents": [{"day": 2, "id": "SPREAD", "culprit": "C08"}]}
}
```

- `par` 不安、`gw` 友好、`int` 暗躍、`guard` 護衛。`boards` はボードの暗躍の数
- `script.roles` に無いキャラクターはパーソン。妄想拡大ウイルスによる変更は保存せず、毎回導く
- 追加の項目（段S5 で足したもの）:
  - `chars[c].present`: 盤面にいるか（無ければ true）。登場前の神格・転校生・アルバイト？、幻想[4] で取り除かれた幻想は false・`area: null`。
    盤面にいないキャラクターは同一エリアに数えず、札も能力も対象にできない
  - `script.days`（タイムトラベラーの最終日）、`script.appear`（`{"C13": {"loop": N}, "C24": {"day": D}}`）、
    `script.territory`（大物のテリトリー）、`script.start`（`{"C34": "CIT"|"SCH"}`）
  - `init`（公開の初期エリア）、`incident_log`、`revealed_roles`（公開された役職の累積。フレンドの友好）、`ability_used_loop`・`ability_used_today`、
    `unbound`（禁止エリアを失った者）、`suppressed_culprits`（手先[3]）、`servant_targets`（従者[4]）、`protagonists_immortal`（軍人[5]）

主な関数（`phases.py`・`abilities.py`・`resolve.py`）:
- `loop_start(prev, init, choice)` ループの準備。`choice` は脚本家の決定 `{"init": {"C18": エリア}, "scholar": "gw"|"par"|"int"}`。第1ループも「第0ループ」の状態からこれを通す
- `turn_start(state)` 転校生・アルバイト？の登場
- `resolve_actions(state, placements, optional, follow)` 行動解決（`follow` は従者の付いていく相手）
- `mm_phase(state, uses)` 脚本家能力フェイズ（クロマク・ミスリーダー・医者[2]・不穏な噂 `X_RUMOR`・ご神木 `SHINBOKU`）
- `use_ability(state, cid, idx, arg, refuse)` 友好能力。`declaration_options(state)` がいま合法な宣言を列挙する
- `incident_occurs` / `run_incident(state, inc, choice)`（発生の判定と記録）と `incident_effect`（効果だけ。A.I.[3]・教祖の2回解決）
- `turn_end(state, optional)`（シリアルキラー・アルバイトの死亡、キラー・メインラバーズ・タイムトラベラーの任意能力）
- `loop_end_loss(state)` ループ終了時の敗北条件の列（空＝敗北なし）。死亡したフレンドを `revealed_roles` に書き足す
- 誤った入力（`IllegalPlacement`）では、行動解決・事件・能力・脚本家能力フェイズとも状態を変えない
- 脚本（非公開シート）は `engine/scripts/*.json`、整合の確認とループ数の目安は `scripts.py`

行動解決（フェイズ4）の入力:

```json
{"placements": [{"by": "M", "target": "C03", "card": "INT1"}, {"by": "B", "target": "C03", "card": "INTX"}]}
```

出力は新しい状態と、出来事の列（`events`: `{"kind": "move", "char": "C08", "from": "HOS", "to": "SCH", "cards": ["MV_D"]}` など）。
置き方が非合法なら、状態を変えずに `error` を返す。

## 手動モードでの使い方（補助）

手動モード（人間が主人公・Claude が脚本家）でも、行動解決の前にエンジンの結果を見て突き合わせられる。

1. ゲーム開始時に、非公開シートを `play/game/private/script.json`（`engine/fixtures/toukou001_g1/script.json` と同じ形）にも置く
2. 行動解決の前に `python3 play/gm.py preview cards_L1D1.txt <inbox の seq>` を実行する（書き込みはしない）
3. 自分の処理と食い違ったら一次資料に当たり、どちらが正しかったかを記録する（計画「最も独立した検証」）

変換は `engine/adapter.py`、公開用の射影は `engine/project.py`。`gm.py` はエンジンを preview の中でだけ読み込む。

## 現状（2026-09-24）

- 範囲: Basic Tragedy X のルール12・事件9・役職すべて、キャラクター35人のうち34人（上位存在は希望・絶望カウンターを使うので対象外）
- 試験: 572 合格・33 xfail（裁定待ち）・3 skip（`play/tests/`）。境界例は約600件で、どれもエンジンのコードを見ない別担当が一次資料から先に書いたもの
  （`tests/cases/`: action_resolution・later_stages・abilities・s5_loop_end・s5_roles_incidents・chars_batch1・chars_batch2a・chars_batch2b）
- 試験用の脚本9本（`scripts/`）。無作為のプレイヤーどうしで最後まで通ることを確かめてある
- 対戦記録（toukou001_g1）は、札と選択だけからエンジンで最初から最後まで再現できる

### 一次資料で決着がつかず、ユーザーが裁定した点（2026-09-23、wiki/notes/rulings.md）

| 箇所 | 裁定（エンジンの扱い） |
|---|---|
| シリアルキラーの「同一エリアに1人だけ」に自身を含むか | 含めない（FAQ Ch02 の相打ちと整合） |
| ループをまたぐリーダー | 前のループのまま |
| ルールXが1つの脚本で、そのXを宣言した情報屋 | Basic Tragedy X ではルールXが必ず2つなので起こりえない（試験は skip） |
| 絶対友好無視（カルティスト）の医者を脚本家が使えるか | 使える。拒否は主人公能力フェイズの処理で、他のフェイズではチェックされない（BakaFire 回答） |
| 拒否された非★能力を同じフェイズにもう一度宣言できるか | できない |
| 護衛が2つあるときの死亡 | 1つ取り除いて1つ残る |
| 最後の戦いで全員分を宣言しないで終えられるか | できない |

同じ方向の移動札の重なり（1回だけ移動）と、無効化された1ループ1回の札も使用済みになることは、原文（印刷p42・43、p22）で確定した。
