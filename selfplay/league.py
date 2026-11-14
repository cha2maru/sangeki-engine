"""対戦表（plan/engine-smarter.md 段A1）。脚本家×主人公の組を、種を固定して N 戦ずつ回し、強さの指標を出す。

強さは勝率ではなく「公開情報が真相をどこまで証明したか」（理想の観測者、engine/deduce.truth_metrics）と
「主人公が実際にどこまで当てたか」で測る（ユーザー指示: 勝率は脚本で主人公有利・脚本家有利がある）。

    cd play && /workspaces/sangeki/.venv/bin/python -m selfplay.league --games 10 --seed 31 --pairs route:blind hidden:blind
結果は selfplay/runs/league_<名前>.json。--compare で前回の結果との差を出す。
"""
import argparse
import json
import multiprocessing
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.deduce import truth_metrics  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.players import PublicObserver  # noqa: E402
from selfplay.run import HERE, Combo, play_game  # noqa: E402


class IdealObserver(PublicObserver):
    """公開の出来事をすべて推理に入れる観測者。ループの終わりと最後の戦いの直前に truth_metrics を記録する。"""
    def __init__(self, script):
        super().__init__()
        self.script, self.snaps = script, {}

    def loop_done(self, s, loop):
        self._ensure(s)
        self.snaps[str(loop)] = truth_metrics(self.ded, self.script, self.inc_history, self.culprits)


def make(kind, rng, script=None):
    if kind.startswith('tuned-'):  # tuned-<名前>（対戦表の組は「脚本家:主人公」なので : は使えない）: tuned/<名前>.json（tune.py の最良の重み）を基準の型に差し込む
        import json as _j
        from engine.scripts import find, tuned_dirs
        tp = find(kind[6:] + '.json', tuned_dirs())
        if tp is None:
            raise FileNotFoundError(f'調整した重み {kind[6:]}.json が見つからない（置き場所: {tuned_dirs()}。sangeki-scripts の tuned/ にある）')
        cfg = _j.load(open(tp, encoding='utf-8'))
        p = make(cfg['base'], rng, script)
        for k, v in cfg['params'].items():
            setattr(p, k, v)
        return p
    return _make(kind, rng, script)


def _make(kind, rng, script=None):
    from selfplay import players as P
    from selfplay.run import RandomPlayer
    from selfplay.search_mm import SearchMastermind
    if kind == 'oread':
        from selfplay.pc_v2 import OracleReader
        return OracleReader(rng, null_cost=0.1, model_mm=True)
    if kind == 'oreadc':  # osolve＋守れる応手が無い日は伏せ札を思考の型で読む（selfplay/card_read.reader_pc、2026-10-23）
        from selfplay.card_read import reader_pc
        return reader_pc(rng)
    if kind == 'plan_read2k':  # plan_read2＋今日のターン終了にキラーがキーパーソンを殺しうる形を先に止める（A-5、2026-11-14）
        from selfplay.pc_plan import plan_pc
        p = plan_pc(rng, script, base_kind='read2', fair=True)
        p.today_first = True
        return p
    if kind.startswith('plan_'):  # 脚本を知らない主人公＋予約（推理の世界ごとに作って多数決）。plan_read2 など（2026-10-30）
        from selfplay.pc_plan import plan_pc
        return plan_pc(rng, script, base_kind=kind[5:], fair=True)
    if kind == 'oplan':  # oreadKQLm＋資源の割り当て（予約）。selfplay/pc_plan（2026-10-29）
        from selfplay.pc_plan import plan_pc
        return plan_pc(rng, script)
    if kind == 'oreadKQLm':  # oreadKQL＋先読みに主人公の友好能力・移動で友好能力の射程に入れる候補（2026-10-29、d30 の Claude の手順から）
        p = make('oreadKQL', rng, script)
        p.look_pcab, p.move_abil = True, True
        return p
    if kind in ('oreadKQ', 'oreadKQL', 'oreadK0w', 'oreadK0h'):  # 脚本を知る主人公の仮説の検証（ユーザー「複数の仮説をたて検証」2026-10-17）
        from selfplay.pc_v2 import OracleReader
        p = OracleReader(rng, null_cost=0.1, model_mm=True, calc_block=True, info_weight=0.0, info_adapt=0.0, reveal=False)
        if kind in ('oreadKQ', 'oreadKQL'):  # H2: 応手表で絞った中で今日の後の勝ち目（_safe）。L は H1: その日の事件まで進めた盤面で見る
            p.prio = 0.02
            p.lookday = kind == 'oreadKQL'
        if kind == 'oreadK0w':  # H3: 応手表を大きく（対象8・筋30）
            p.table_targets, p.table_routes = 8, 30
        if kind == 'oreadK0h':  # H4: 一度きりの札の温存の費用を上げる
            p.hold = 0.5
        return p
    if kind == 'read2':
        from selfplay.pc_v2 import ReadingBlocker2
        return ReadingBlocker2(rng)
    if kind.startswith('lock'):  # lockB1・lockP2・lockBP: 調整した主人公（tuned/pc_r2）＋脚本家の逆算を主人公の側から（B: 確定を許す応手を減点、P: 確定したループは情報に）
        p = make('tuned-pc_r2', rng, script)
        rest = kind[4:]
        if rest.startswith('A'):  # lockA0.5: 仮定ごとに世界を枝分かれ（足す世界の割合 0.5。ユーザーの案）
            p.assume = float(rest[1:] or 0.5)
        elif rest == 'QC':
            p.prio, p.card_lr = 0.02, 3.0
        elif rest == 'QB':  # lockQB: 優先順＋ブラフか真かの推理（伏せ札・どこからともなく置かれたカウンター。bluff_read）
            p.prio, p.bluff_read = 0.02, True
        elif rest in ('QA', 'QCA', 'QAX'):  # lockQA: 優先順＋どこからともなく置かれたカウンターの置き先を手がかりに（ability_lr 3）。QCA は札の手がかり（card_lr 3）も。QAX は守りの判定の計算量に上限なし（比べる用）
            p.prio, p.ability_lr = 0.02, 3.0
            if rest == 'QAX':
                p.safe_work = 10**9
            if rest == 'QCA':
                p.card_lr = 3.0
        elif rest == 'KC':  # lockKC: 知っている筋だけ守る（0.3・0.4）＋脚本家が置いたカウンターで推理を更新（card_lr 3）
            p.split, p.split_known, p.card_lr = 0.3, 0.4, 3.0
        elif rest.startswith('K'):  # lockK0.3 / lockK0.3_0.4: lockS＋知っている筋だけ（役職が推理で 0.7（_の後の値）以上・犯人が1人に絞れた筋）
            a, _, b = rest[1:].partition('_')
            p.split, p.split_known = float(a or 0.3), (float(b) if b else True)
        elif rest.startswith('S'):  # lockS0.3: 途中で終わる筋だけ守り、届く世界が 0.3 未満なら情報（ユーザーの案）
            p.split = float(rest[1:] or 0.3)
        elif rest in ('G', 'GC'):  # lockG・lockGC: 優先順に門（従来の手が勝ち目を 0.05 超えて落とすときだけ乗り換え）
            p.prio, p.prio_gate = 0.02, 0.05
            if rest == 'GC':
                p.card_lr = 3.0
        elif rest.startswith('Q'):  # lockQ0.02: 優先順（このループの勝ち目 → 従来の評価、勝ち目が無ければ情報）
            p.prio = float(rest[1:] or 0.02)
        elif rest == 'CB':  # 推理の更新（暗躍の札）＋確定を許す応手の減点
            p.card_lr, p.lock_block = 3.0, 1.0
        elif rest == 'BP':
            p.lock_block, p.lock_probe = 1.0, 2.0
        elif rest.startswith('C'):  # lockC2: 公開された暗躍の札を尤度に（card_lr）
            p.card_lr = float(rest[1:] or 2)
        elif rest.startswith('D'):
            p.lock_danger = float(rest[1:] or 1)
        elif rest.startswith('B'):
            p.lock_block = float(rest[1:] or 1)
        elif rest.startswith('P'):
            p.lock_probe = float(rest[1:] or 2)
        return p
    if kind.startswith('readlookQ'):  # readlookQ2: readlookI1＋負けが成立したループは情報に使う（probe=2、広い条件）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, defplan=True, lookday=True, reach_info=1.0, board_waste=1.0, probe=float(kind[9:] or 2), probe_broad=True, obs=0.5)
    if kind == 'readlook':  # readdefplan＋その日の事件まで進めた盤面で手を評価
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, defplan=True, lookday=True)
    if kind.startswith('read'):  # read / read1.0（情報を得る能力に近づく重み info_weight）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, info_weight=float(kind[4:] or 0.3))
    if kind == 'calcA':  # calcG＋脚本家の能力（クロマク・ミスリーダー）で毎日入る+1 を逆算と賭けに入れる（route_calc v2.2）
        p = make('calcG', rng, script)
        p.calc_abil = True
        return p
    if kind in ('calcD', 'calcAD'):  # 二重の脅威の候補（double）。calcAD は calcA にも
        p = make('calcA' if kind == 'calcAD' else 'calcG', rng, script)
        p.double = True
        return p
    if kind == 'calcGBi':  # calcGB＋事件の損得（逆算の不安+1 の確かめ・事件の門番。search_mm._drop_harmful_pushes・_harmful_today、runs/inc_help_2026-10-16）
        p = make('calcGB', rng, script)
        p.push_check, p.inc_guard = True, True
        return p
    if kind in ('calcGBxhdKA', 'calcGBxhdKAM'):  # calcGBxhdK＋割れた役職の脚本家能力（ミスリーダーなど）を逆算と賭けに数える（search_mm._abil、2026-10-28）
        p = make('calcGBxhdK', rng, script)
        p.calc_abil = 'known'
        p.ability_moves = kind == 'calcGBxhdKAM'  # M: 持ち主と対象を移動で同じエリアにそろえる候補（search_mm._ability_moves）
        return p
    if kind in ('calcGBxhdKL', 'calcGBxhdKRL'):  # 決め手を仕込む候補（1枚が通ると翌朝に必ず取れる札）を探索に入れる。search_mm._lock_moves、2026-10-24
        p = make(kind[:-1], rng, script)
        p.lock_moves, p.lock_w, p.once_w, p.lock_k = True, 3.0, 1.5, 4
        # 二重の脅威の候補は KL にだけ入れる（4本で読む主人公の勝ち 30→24・読まない主人公 14→12）。KRL で写しの枠を2組回すと
        # 21→30 に悪化した（d13・d18 で決め手の見直しが減った）ので、KRL は決め手の枠4組のまま（runs/lock_mm_2026-10-25、lock4_*）
        if kind == 'calcGBxhdKL':
            p.double = True  # ponytail: 重み3は1位と決め手の差（d18 初日 5.6 対 1.9）を越える程度。調整は対戦表で
        return p
    if kind in ('calcGBxhdKD', 'calcGBxhdKRD'):  # 見せ札の二重の脅威（本命と同じくらい危なく見える所に弱い札）を候補に。search_mm._decoy_moves、2026-10-24
        p = make(kind[:-1], rng, script)
        p.decoy = True
        return p
    if kind == 'calcGBxhdKR':  # calcGBxhdK＋読む主人公の写し（上位の置き場所に写しの応手を出させて評価を直す。search_mm._read_rerank、2026-10-24）
        from selfplay.card_read import reader_pc
        p = make('calcGBxhdK', rng, script)
        p.reader = reader_pc(random.Random(f"{script['id']}:reader"))
        orig = p.mm_cards

        def mm_cards(s):
            mm = orig(s)
            i = 'ABC'.index(s['leader'])
            p._reader_reply(s, list('ABC'[i:] + 'ABC'[:i]), [x['target'] for x in mm], keep_state=True)  # 写しにも実際の置き場所を見せる
            return mm
        p.mm_cards = mm_cards
        return p
    if kind == 'calcGBxhdKNim':  # 比べ: KNi から move_dt だけを外したもの（kni19）
        p = make('calcGBxhdKNi', rng, script)
        p.move_dt = False
        return p
    if kind == 'calcGBxhdKNih':  # 比べ: KNi から alone_hidden だけを外したもの（kni20）
        p = make('calcGBxhdKNi', rng, script)
        p.alone_hidden = False
        return p
    if kind == 'calcGBxhdKNiv':  # 比べ: KNi から illusion_dodge だけを外したもの（runs/intent_mm_2026-10-31 の kni17）
        p = make('calcGBxhdKNi', rng, script)
        p.illusion_dodge = False
        return p
    if kind in ('calcGBxhdKNi0', 'calcGBxhdKNia', 'calcGBxhdKNir'):  # 比べ: 0＝alone_extras・reach なし、a＝alone_extras だけ、r＝reach だけ（d04_bomb の調べ）
        p = make('calcGBxhdKNi', rng, script)
        p.alone_extras = kind[-1] == 'a'
        p.calc_pcab = 'reach' if kind[-1] == 'r' else True
        return p
    if kind == 'calcGBxhdKNc':  # KNq＋逆算に、いま使える委員長[2]（使用済みの不安−1 を戻す）も数える（calc_pcab='c10'、目隠し gen_s61_000 L2D3〜D5）
        p = make('calcGBxhdKNq', rng, script)
        p.calc_pcab = 'c10'
        return p
    if kind == 'calcGBxhdKNq':  # KNh＋評価に主人公能力フェイズの近似（近い筋のカウンターを主人公の能力で 1 つ減らしてから数える。ユーザー決定 2026-11-14）
        p = make('calcGBxhdKNh', rng, script)
        p.pc_abil_val = True
        return p
    if kind == 'calcGBxhdKNh':  # KNt＋主人公の能力の持ち主を射程の外へ動かす候補・最終日の残り 0 の筋を主人公の能力で崩せるなら割り引く（2026-11-14、目隠し gen_s61_000 L2D6）
        # 土台は KNw にしない: abil_win は軍人[5]（主人公は死亡しない）を数えず、gen_b31_022 L4D3 の友好禁止をやめた
        p = make('calcGBxhdKNt', rng, script)
        p.holder_away = True
        p.pcab_val = True
        return p
    if kind == 'calcGBxhdKNs':  # KNt＋主人公の模型を plan_read2（友好能力の持ち主の射程へ寄せる守り・予約を持つ）に（2026-11-14、目隠し gen_s61_000・gen_s61_012）
        import random as _r
        from selfplay.pc_plan import plan_pc
        p = make('calcGBxhdKNt', rng, script)
        if p.shadow is not None:
            p.shadow = plan_pc(_r.Random(rng.random()), script, base_kind='read2', fair=True)
        return p
    if kind == 'calcGBxhdKNp':  # KNt＋最終日の残り 0 の筋を、主人公の友好能力で崩せるなら勝ちと数えない（2026-11-14、目隠し gen_s61_000 L2D6）
        p = make('calcGBxhdKNt', rng, script)
        p.pcab_val = True
        return p
    if kind == 'calcGBxhdKNo':  # KNt＋前のループの同じ日と同じ手（置き先・札）に小さな罰（役職を漏らさない、2026-11-14）
        p = make('calcGBxhdKNt', rng, script)
        p.vary_open = 0.3
        return p
    if kind == 'calcGBxhdKNw':  # KNt＋評価で脚本家能力フェイズの 1 手まで見て、ターン終了の殺害を毎日その日の勝ちに数える（2026-11-14）
        p = make('calcGBxhdKNt', rng, script)
        p.abil_win = True
        return p
    if kind == 'calcGBxhdKNt':  # Guignol 3（KNi）＋止める確率 q を、実際の主人公が筋の種類ごとに止めた割合に（A-6 の当て直し、2026-11-14）
        p = make('calcGBxhdKNi', rng, script)
        p.stop_table = True
        return p
    if kind == 'calcGBxhdKNr':  # Guignol 3（KNi）＋主人公が模型の読みより止め損なった割合で、止める確率（human_block）を下げる（C-4 の軽い版、2026-11-14）
        p = make('calcGBxhdKNi', rng, script)
        p.read_miss = True
        return p
    if kind == 'calcGBxhdKNm':  # Guignol 3（KNi）＋位置の 1 日の読み合い（route_calc.move_game の混ぜ方の手を探索の候補に。作業計画 A-3c、2026-11-14）
        p = make('calcGBxhdKNi', rng, script)
        p.move_mg = True
        return p
    if kind == 'calcGBxhdKNi':  # calcGBxhdKN＋意図のある札（候補の置き先・札を無作為に引かず、筋・事件・不安臨界の順と、押す・不安を積む・妨害の札で作る。2026-10-31）
        p = make('calcGBxhdKN', rng, script)
        p.intent_cards = True
        p.holder_move = 1.2  # クロマク・カルティストを封印されしモノ・巨大時限爆弾Xのエリアへ動かす手（目隠し対戦 gen_s11_006）
        p.keep_forced = True  # 逆算の必ず取れる手の札を _fill_idle で替えない（目隠し対戦 gen_s11_023）
        p.immortal_check = True  # 軍人[5] があと1歩なら主人公死亡の筋に頼る逆算の手に従わない（目隠し対戦 gen_b31_022）
        p.setup_area = True  # 手先（カルティスト等）を近い負け筋の対象のあるエリアへ（目隠し対戦 gen_b31_019）
        p.inc_reach = 1.0  # 効果の後あと1手でそろう2段の筋も決め手に数える（目隠し対戦 gen_s11_007 L4 の行方不明＝フレンド）
        p.inc_push = 0.5  # 決め手になる事件の犯人を間に合う日から積む（目隠し対戦 gen_s11_008 L3 の行方不明＝シリアルキラー）
        p.advance_ab = 1.0  # 一番近い負け筋を近づける能力は漏れと比べずに使う（目隠し対戦 gen_s11_022 L2 のクロマク）
        p.alone_extras = True  # 2人きりの筋で、同じエリアのほかの人物を移動で追い出す手も候補に（目隠し対戦 gen_s11_002 L3D6）
        p.fill_eve = True  # ボードの効かない札を、守りの能力が今日閾値に届く持ち主への友好禁止に（目隠し対戦 gen_b31_035 L3D4）
        p.gwx_hide = True  # 友好禁止を、負け筋に効かない持ち主から閾値に届くカルティスト・ウィッチへ（目隠し対戦 gen_s11_003 L3D5）
        p.gwx_live = True  # 友好禁止を、ループに1回の能力の持ち主から、負け筋に効く毎日使える能力の持ち主へ（目隠し対戦 gen_s11_014 L1D1）
        # alone_hidden（狙われる人物の役職が読まれていなければ移動禁止が残っていても追い出しを候補に）は外した: kni20 で主人公に守られたループが
        # 3本で 6 → 9 に増えた（gen_s11_031・gen_s11_016・gen_s11_002。runs/intent_mm_2026-10-31 の README）
        p.move_dt = True  # 移動の二重の脅威（主人公の応手を全部当てて、どれにも位置の筋が通る移動札の組。目隠し対戦 gen_s11_009 L3D6）
        p.illusion_dodge = True  # 幻想のボードに押す札が要る逆算を「必ず取れる」にしない（主人公は移動札でかわせる。目隠し対戦 gen_s11_005 L3）
        p.tt_gwx_guard = True  # 最終日・友好を積まれた後のタイムトラベラーへの友好禁止は不安禁止に（目隠し対戦 gen_s11_000 L1D5）
        # calc_pcab='reach'（友好を積めば届く能力も逆算に数える）は外した: d04_bomb で「必ず取れる」を取り下げたのに能力の持ち主を友好禁止で
        # 封じず、ループを落とした（runs/intent_mm_2026-10-31 の README）。持ち主を封じる手とつなげてから入れる
        return p
    if kind == 'calcGBxhdKN':  # calcGBxhdK＋逆算と確定の判定に主人公の友好能力（calc_pcab）。d30 L1D6 でナース[2] を数えず確定と誤って隠した（2026-10-29）
        p = make('calcGBxhdK', rng, script)
        p.calc_pcab = True
        return p
    if kind == 'calcGBxhdK':  # calcGBxhd＋鍵の持ち主への先回り（必勝を崩す主人公の手の持ち主に友好禁止。search_mm._key_preempt、2026-10-23）
        p = make('calcGBxhd', rng, script)
        p.key_guard, p.key_script = True, script
        return p
    if kind.startswith('calcGBxh'):  # calcGBx＋人間の止め方の模型（止める確率 0.25+0.4·vis。threat_table の Claude・read2 の表から）
        p = make('calcGBx', rng, script)
        p.human_block = (0.25, 0.4)
        p.last_day_now = kind == 'calcGBxhd'  # d: 最終日は今日成り立つ筋を最優先（人間の模型と組で）
        if kind == 'calcGBxhd':  # 脚本家の中の主人公の模型を軽く（世界4・無作為の候補20）。2手目以降が約4倍速く、
            # 23本×seed4〜6 で最後の戦いへの到達 61→63/68（強さは落ちない。2026-10-17）。前の重さは世界16・候補40（ReadingBlocker の既定）
            p.shadow.nh, p.shadow.min_worlds, p.shadow.samples = 4, 4, 20
        return p
    if kind.startswith('calcGBxr'):  # calcGBx（λ=0.3）＋ルールの漏れを残りのループに比例して数える（例 calcGBxr0.5）
        p = make('calcGBx', rng, script)
        p.lam, p.rule_leak = 0.3, float(kind[len('calcGBxr'):] or 0.5)
        return p
    if kind.startswith('calcGBx@'):  # 漏れの重み λ を変えた calcGBx（例 calcGBx@0.3。既定 0.1）
        p = make('calcGBx', rng, script)
        p.lam = float(kind.split('@')[1])
        return p
    if kind == 'calcGBx':  # 2026-10-16 の直しをまとめたもの: 移動斜めの門・事件の損得・必勝が無い日の仕込み（目隠しの Claude 戦で採否を見る）
        p = make('calcGB', rng, script)
        p.diag_gate, p.push_check, p.inc_guard, p.setup_k = True, True, True, 6
        p.cult_feasible, p.sk_leak = True, 1.0  # gen_b31_031 の目隠し対戦から（カルティストの無駄な無視、シリアルキラーがタイムトラベラーに当たる配置）
        return p
    if kind == 'calcGB':  # calcG＋確定したループで偽の負け筋に押す（型1。plan/mastermind_algorithm_evolution_plan.md P2）
        p = make('calcG', rng, script)
        p.lock_bluff = True
        p.hide = 10.0  # 確定したループでは任意能力（キラー・メインラバーズ）・脚本家能力を使わない（目隠し対戦: 確定後のメインラバーズで3人が割れた）
        return p
    if kind == 'calcP':  # calcG＋位置の筋（同じエリア・2人きり・病院にいる）を逆算に入れる（route_calc v2.1）
        p = make('calcG', rng, script)
        p.calc_pos = True
        return p
    if kind in ('calcL', 'calcG'):  # calcG: calcL＋今日の賭け（確率 0.5 以上の二択）。calcL: searchL＋負け筋の逆算（取れるループはその札を最優先。plan/route-calc.md）
        p = make('searchL', rng, script)
        p.calc, p.gwx_eve = True, 1.0
        if kind == 'calcG':
            p.gamble = 0.5
        return p
    if kind.startswith('searchL'):  # searchL: searchP1＋その日の脚本家能力・事件まで読んで手を評価（lookday）
        p = make('searchP1', rng, script)
        p.lookday = True
        if kind[7:]:
            p.cut_mm = float(kind[7:])
        return p
    if kind.startswith('searchP'):  # searchP1: searchJO（定石＋主人公の模型）＋ persist（消しにくい暗躍の価値）＋定石の対象を全負け筋から
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], opp=16, triples=8, joseki=True,
                                persist=float(kind[7:] or 1), joseki_top=99, gwx_eve=1.0, hide_culprit=1.0)
    if kind.startswith('search'):  # search / search0.3（λ）
        return SearchMastermind(rng, lam=float(kind[6:] or 0.1), days=script['days'], loops=script['loops'])
    return {'route': P.RouteMastermind, 'hidden': P.HiddenMastermind, 'blind': P.DeductiveBlocker, 'oracle': P.OracleBlocker,
            'deduce': P.DeductiveProtagonist, 'random': RandomPlayer}[kind](rng)


def card_use(before, mm_cards, events):
    """脚本家の札1枚ごとの使われ方（ユーザー「無駄な札を数える」2026-10-11）: 'effect'（カウンター・移動・主人公の札を打ち消した）、
    'drew'（暗躍禁止・移動禁止で止められた＝止め札を使わせた）、'surplus'（暗躍が乗ったが、要る数を満たし取り除かれもしない所）、
    'guard'（友好禁止・不安禁止で何も打ち消さなかった＝保険・ブラフ。ユーザー「不安禁止は負け筋を確定させる保険にもブラフにもなる、
    友好禁止は友好無視のキャラに置いて誤認させるブラフにもなる」）、'idle'（何も起きなかった）。before: 行動解決の前の盤面。"""
    from selfplay.search_mm import SearchMastermind
    try:
        wasted = SearchMastermind._wasted_int(None, before)
    except Exception:
        wasted = set()
    out = []
    for x in mm_cards:
        t, c = x['target'], x['card']
        hit = lambda k, **kw: any(e.get('kind') == k and all(e.get(a) == b for a, b in kw.items()) for e in events)  # noqa: E731
        if c in ('PAR+', 'PAR-'):
            u = 'effect' if hit('par', target=t, card=c) else 'idle'
        elif c in ('INT1', 'INT2'):
            u = ('surplus' if t in wasted else 'effect') if hit('int', target=t, card=c) else 'drew' if hit('nullified', target=t, by='INTX') else 'idle'
        elif c in ('GWX', 'PARX'):
            u = 'effect' if hit('nullified', target=t, by=c) else 'guard' if not t.startswith('B:') else 'idle'
        elif c.startswith('MV_'):
            moved = any(e.get('kind') == 'move' and e.get('char') == t and c in (e.get('cards') or []) for e in events)
            u = 'effect' if moved else 'drew' if any(e.get('kind') == 'move_blocked' and e.get('char') == t for e in events) else 'idle'
        else:
            u = 'idle'
        out.append(u)
    return out


def one_game(script, mm, pc, seed, g):
    """乱数は陣営ごと・試合ごとに分ける（敵対的レビュー4: 片方の探索を変えても、相手の手と他の試合が変わらないように）。"""
    rm, rp = random.Random(f'{seed}:{g}:mm'), random.Random(f'{seed}:{g}:pc')
    obs = IdealObserver(script)
    fb = {}

    loops_won = {}
    use = {'effect': 0, 'drew': 0, 'guard': 0, 'surplus': 0, 'idle': 0}
    pending = {}

    def log(kind, rec):
        if kind == 'decisions' and rec.get('kind') == 'place_cards' and rec.get('who') == 'M' and rec.get('state'):
            pending[(rec['loop'], rec['day'])] = (rec['state'], rec['choice'])
        if kind == 'events' and rec.get('phase') == 'actions' and (rec.get('loop'), rec.get('day')) in pending:
            st, ch = pending.pop((rec['loop'], rec['day']))
            before = dict(st, script={**script, 'days': script['days']}, loop=rec['loop'], day=rec['day'],
                          ability_used_loop=[], ability_used_today=[], incident_log=[], leader='A')
            for u in card_use(before, ch, rec.get('events') or []):
                use[u] += 1
        if rec.get('phase') == 'final_battle':
            fb.update(rec)
        if kind == 'events' and rec.get('phase') in ('loop_end', 'loop_end_check'):
            for e in rec.get('events', []):
                if e.get('kind') == 'loop_end':  # 日の途中の終わりは主人公の負けだけ（以前は 'loss' が無く記録から漏れていた）
                    loops_won[rec['loop']] = (e.get('loss', True), rec['day'])
                if e.get('kind') == 'loop_end_check':
                    loops_won.setdefault(rec['loop'], (e['loss'], rec['day']))
    t0 = time.time()
    mmp = make(mm, rm, script)
    pcp = make(pc, rp, script)
    if mm.startswith('searchoracle'):  # 主人公の応手を完全に読める脚本家（上限の測定用）
        mmp.oracle_pc = pcp
    on_day = None
    if mm.startswith('searchroll'):  # ロールアウト型: 日の始めに候補をループの終わりまで回して手を決める（上限の近似）
        def on_day(s, player, loop, day, box):
            mmp.plan_day(s, player, loop, day)
    if pc.startswith('oreadroll'):  # 主人公の先読み（ループ1に勝ち筋があるかの上限の近似）
        from selfplay.pc_roll import plan_day as pc_plan

        def on_day(s, player, loop, day, box):
            pc_plan(s, player, loop, day, script)
    from engine.deduce import Deduction
    Deduction.WATCH = {'rules': script['rules'], 'roles': script['roles']}  # 推理が真の脚本を消したら記録する
    try:
        r = play_game(script, Combo(mmp, pcp), log, observers=[obs], on_day=on_day)
    finally:
        Deduction.WATCH = None
    out = {'game': g, 'result': r, 'sec': round(time.time() - t0, 1), 'truth': obs.snaps,
           'loops_won': {str(k): v for k, v in loops_won.items()}, 'card_use': use}
    # 推理の健全性（主人公自身の推理・脚本家が持つ公開情報の推理）: 仮説が全部消えた／真の脚本を消した最初の観測
    for side, ded in (('pc', getattr(pcp, 'ded', None)), ('mm', getattr(getattr(mmp, 'pub', None), 'ded', None))):
        if ded is None:
            continue
        for k in ('empty_by', 'truth_lost_by'):
            if getattr(ded, k, None):
                out[f'{side}_{k}'] = [ded.__dict__[k][0], json.dumps(ded.__dict__[k][1], ensure_ascii=False, default=str)[:200]]
                print(f"[推理の健全性] {script.get('id')} 試合{g} {side}: {k} = {out[f'{side}_{k}']}", file=sys.stderr)
    if getattr(mmp, 'loop_log', None):
        out['mm_loop_log'] = mmp.loop_log
    if getattr(mmp, 'move_log', None):
        out['mm_move_log'] = mmp.move_log
    if fb:
        # 2層目: 主人公の宣言のうち、外すまでに当てた人数と、公開情報で確定していたのに外したか
        final = obs.snaps.get('final', {})
        stop = r.get('final_stopped_at')
        out['declared_ok'] = len(fb['guesses']) if stop is None else stop
        if stop is not None:
            miss = fb['guesses'][stop]['char']
            out['missed_confirmed'] = miss in _confirmed_chars(obs)
        out['confirmed_at_final'] = final.get('confirmed')
        # 最後の戦いの直前に、公開情報だけで真の配役が確定していたか（0 ビット＝推理で当てられる、>0＝勘の勝負。ユーザーの観点:
        # 勘に頼らせるのは脚本家の目指す状況）
        out['final_bits'] = final.get('nlog_p_truth')
    return out


def _confirmed_chars(obs):
    m, tot = obs.ded.marginals()
    return {c for c, d in m.items() if tot and max(d.values(), default=0) >= tot * (1 - 1e-9)}


def _bits(g):
    """最後の戦いの直前に公開情報で残っていた、真の配役の不確かさ（ビット）。記録の無い古い試合は truth['final'] から。"""
    b = g.get('final_bits')
    if b is None:
        b = (g.get('truth', {}).get('final') or {}).get('nlog_p_truth')
    return 99.0 if b is None else b


def summarize(games):
    n = len(games)
    fin = [g for g in games if g['result']['loop'] == 'final']
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    at = lambda g: g['truth'].get('final') or g['truth'][max(g['truth'])]  # noqa: E731
    return {
        'games': n,
        'pc_wins': sum(g['result']['winner'] == 'protagonists' for g in games),
        'pc_loop_wins': sum(g['result']['winner'] == 'protagonists' and g['result']['loop'] != 'final' for g in games),
        'finals': len(fin),
        'final_pc_wins': sum(g['result']['winner'] == 'protagonists' for g in fin),
        # 最後の戦いの勝ちの内訳: 推理で確定して勝った／勘で勝った。脚本家は勘の勝負に追い込めた数（final_guess）を増やしたい
        # 線引きは過去の対戦表 21,253 回の最後の戦いの分布から（1ビット未満 勝率0.97以上／1〜3ビット 0.47〜0.64／3ビット以上 0.10以下）。
        # データが増えたら見直す（ユーザーの観点: しきい値は分類を進めてから決める）
        'final_wins_deduced': sum(g['result']['winner'] == 'protagonists' and _bits(g) < 1 for g in fin),
        'final_wins_guessed': sum(g['result']['winner'] == 'protagonists' and _bits(g) >= 1 for g in fin),
        'final_guess': sum(1 <= _bits(g) < 3 for g in fin),      # 勘の勝負に追い込めた
        'final_hidden': sum(_bits(g) >= 3 for g in fin),         # 勘でもほぼ届かない所まで隠せた
        'final_bits': avg([_bits(g) for g in fin]),
        # 1層目（最後の戦いの直前。そこまで行かなかった試合は最後のループの終わり）
        'confirmed': avg([at(g)['confirmed'] for g in games]),
        'confirmed_key_roles': avg([at(g)['confirmed_key_roles'] for g in games]),
        'mean_true': avg([at(g)['mean_true'] for g in games]),
        'nlog_p_truth': avg([at(g)['nlog_p_truth'] for g in games if at(g)['nlog_p_truth'] is not None]),
        'truth_lost': sum(not at(g)['truth_alive'] for g in games),  # 0 でなければ推理の健全性の破れ
        # 主人公自身の推理・脚本家の公開情報の推理が、真の脚本を消した／仮説が全部消えた試合の数（0 でなければ不具合。理由は試合の記録に）
        'pc_truth_lost': sum(bool(g.get('pc_truth_lost_by')) for g in games),
        'pc_empty': sum(bool(g.get('pc_empty_by')) for g in games),
        'mm_truth_lost': sum(bool(g.get('mm_truth_lost_by')) for g in games),
        # 2層目
        'declared_ok': avg([g['declared_ok'] for g in fin]),
        'missed_confirmed': sum(bool(g.get('missed_confirmed')) for g in fin),
        'sec_per_game': avg([g['sec'] for g in games]),
        # 脚本家の札の使われ方（card_use）: 無駄（上乗せ＋何も起きなかった）の割合
        'mm_waste': (lambda u: round((u['surplus'] + u['idle']) / max(1, sum(u.values())), 3))(
            {k: sum(g.get('card_use', {}).get(k, 0) for g in games) for k in ('effect', 'drew', 'guard', 'surplus', 'idle')}),
        'card_use': {k: sum(g.get('card_use', {}).get(k, 0) for g in games) for k in ('effect', 'drew', 'guard', 'surplus', 'idle')},
        # ループ k の終わりの値（そのループまで進んだ全試合。最後の戦いに届いたかで選ばない＝敵対的レビュー3）
        'by_loop': {k: {'n': len(v), 'confirmed': avg([x['confirmed'] for x in v]),
                        'confirmed_key_roles': avg([x['confirmed_key_roles'] for x in v]),
                        'role_entropy': avg([x['role_entropy'] for x in v])}
                    for k in ('1', '2', '3', 'final') for v in [[g['truth'][k] for g in games if k in g['truth']]] if v},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--games', type=int, default=10)
    ap.add_argument('--seed', type=int, default=31)
    ap.add_argument('--pairs', nargs='+', default=['route:blind', 'hidden:blind'])
    ap.add_argument('--name', default=None)
    ap.add_argument('--script', default='s03_bomb', help='engine/scripts/<id>.json')
    ap.add_argument('--procs', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument('--compare', default=None, help='前回の league_*.json')
    a = ap.parse_args()
    res = {}
    script = by_id(a.script)
    jobs = [(script, *pair.split(':'), a.seed, g) for pair in a.pairs for g in range(a.games)]
    # ponytail: 1試合1プロセスの素朴な並列。メモリは1プロセス数百MB（仮説の行列）なので、--procs で抑える
    with multiprocessing.Pool(a.procs) as pool:
        out = pool.starmap(one_game, jobs)
    for pair in a.pairs:
        games = [o for j, o in zip(jobs, out) if f'{j[1]}:{j[2]}' == pair]
        res[pair] = {'summary': summarize(games), 'games': games}
        print(pair, json.dumps(res[pair]['summary'], ensure_ascii=False))
    name = a.name or time.strftime('%Y%m%d_%H%M')
    path = os.path.join(HERE, 'runs', f'league_{name}.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({'seed': a.seed, 'games': a.games, 'script': a.script, 'pairs': res}, open(path, 'w'), ensure_ascii=False, indent=1)
    print('→', path)
    if a.compare:
        old = json.load(open(a.compare))['pairs']
        for pair, v in res.items():
            if pair in old:
                o = old[pair]['summary']
                print(pair, {k: (o.get(k), x) for k, x in v['summary'].items() if o.get(k) != x})


if __name__ == '__main__':
    main()
