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
    if kind.startswith('oreadroll'):  # 脚本を知る主人公＋ループ1の日ごとの先読み（selfplay/pc_roll.py、one_game の on_day）
        from selfplay.pc_v2 import OracleReader
        return OracleReader(rng, null_cost=0.1, model_mm=True)
    if kind == 'oread':
        from selfplay.pc_v2 import OracleReader
        return OracleReader(rng, null_cost=0.1, model_mm=True)
    if kind == 'read2':
        from selfplay.pc_v2 import ReadingBlocker2
        return ReadingBlocker2(rng)
    if kind in ('readpair', 'read1p'):  # readpair は既定の read と同じ。read1p は止める札を1枚ずつしか試さない旧版
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, pairs=(kind == 'readpair'))
    if kind == 'readnourg':  # 締め切りの加点を既定にする前の read
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, urgency=0.0)
    if kind == 'readchain':  # 事件の連鎖（前の事件で後の事件の犯人を臨界にする）の前の事件の犯人も止める
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, chain=True)
    if kind.startswith('readsafe'):  # readsafe0.8: 伏せ札が通ると負け筋が届く世界の割合×0.8 だけ情報の重みを弱める
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, safe=float(kind[8:] or 0.8))
    if kind.startswith('readlookR'):  # readlook＋公開された脚本家の札の履歴で伏せ札の中身を見積もる（readlookR1: recall_mm=1）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, defplan=True, lookday=True, recall_mm=float(kind[9:] or 1))
    if kind.startswith('lock'):  # lockB1・lockP2・lockBP: 調整した主人公（tuned/pc_r2）＋脚本家の逆算を主人公の側から（B: 確定を許す応手を減点、P: 確定したループは情報に）
        p = make('tuned-pc_r2', rng, script)
        rest = kind[4:]
        if rest == 'QC':
            p.prio, p.card_lr = 0.02, 3.0
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
    if kind.startswith('tunedcut'):  # tunedcut1: 調整した主人公（tuned/pc_r2）＋ループの途中で終わる脅威の上乗せ cut_w
        p = make('tuned-pc_r2', rng, script)
        p.cut_w = float(kind[8:] or 1)
        return p
    if kind.startswith('readlookQ'):  # readlookQ2: readlookI1＋負けが成立したループは情報に使う（probe=2、広い条件）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, defplan=True, lookday=True, reach_info=1.0, board_waste=1.0, probe=float(kind[9:] or 2), probe_broad=True, obs=0.5)
    if kind.startswith('readlookI'):  # readlookI0.5: readlook＋今日届く情報の能力を取りに行く＋ボードへの無駄な札を減点
        from selfplay.pc_v2 import ReadingBlocker
        w = float(kind[9:] or 0.5)
        return ReadingBlocker(rng, defplan=True, lookday=True, reach_info=w, board_waste=w)
    if kind == 'readlook':  # readdefplan＋その日の事件まで進めた盤面で手を評価
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, defplan=True, lookday=True)
    if kind == 'readdefplan':  # 役職がほぼ確定した人物を、カウンターを取り除く友好能力で守る計画（ループ単位で持ち主に友好を集中）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, defplan=True)
    if kind == 'readnonulled':  # readnulled を既定にする前の主人公
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, nulled=0.0)
    if kind == 'readguard':  # 守りを不安−1 の枚数に頼らない: 臨界に届いた対象も止める（crit）＋ボードを空ける（clear）＋守りの友好能力に友好（defense0.3）
        from selfplay.pc_v2 import ReadingBlocker  # 中身を知る Claude が脚本家の d27: 学者に毎日不安+1 で不安−1 が尽き、学者[3]・ナース[2] や不安拡大の犯人を止める手を打たなかった
        return ReadingBlocker(rng, crit=True, clear=True, defense=0.3)
    if kind.startswith('readnulled'):  # readnulled0.3: 打ち消された (対象, 禁止) へ不安±・友好を置く手を減点
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, nulled=float(kind[10:] or 0.3))
    if kind == 'readnoreveal':  # readreveal を既定にする前の主人公
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, reveal=False)
    if kind == 'readreveal':  # 役職を公開する能力の持ち主に友好を集中する計画（最終ループ以外）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, reveal=True)
    if kind.startswith('readprobe'):  # readprobe3: 負けが確定したループは情報の重みを3倍・事件の観測1（最終ループを除く）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, probe=float(kind[9:] or 3))
    if kind == 'readit':  # 情報の友好能力への友好を、調べられる相手の推理のエントロピーで重み付け
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, itarget=True)
    if kind.startswith('readcd'):  # readcd0.3: readclear＋守りの友好能力に友好を積む価値×0.3
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, clear=True, defense=float(kind[6:] or 0.3))
    if kind == 'readclear':  # 伏せ札のあるボードに暗躍禁止＋そこにいる人物を移動で出す組を候補に足す
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, clear=True)
    if kind.startswith('readobs'):  # readobs0.3: 事件の発生・不発で犯人の候補が割れる量（ビット）×0.3 を価値に足す
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, obs=float(kind[7:] or 0.3))
    if kind.startswith('readzero'):  # readzero2: 札の解決後に距離0の負け筋が残る世界に +2。readzero2a6: 情報の重みの基準 info_adapt=6 も
        from selfplay.pc_v2 import ReadingBlocker
        z, _, a = kind[8:].partition('a')
        return ReadingBlocker(rng, zero=float(z or 2), **({'info_adapt': float(a)} if a else {}))
    if kind == 'readmv':  # 今日1回の移動でそろう筋（キラーとキー・シリアルキラーと2人きり）の人物への移動禁止に加点
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, reach_mv=True)
    if kind == 'readnoadapt':  # バランス版を既定にする前の主人公（情報の重み一定、最終ループは0）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, info_adapt=0.0, info_last=0.0)
    if kind.startswith('readadaptbal'):  # readadaptbal12: readadapt を最終ループにも効かせる（守りつつ情報も取るバランス版。ユーザーの観点）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, info_adapt=float(kind[12:] or 12), info_last=None)
    if kind.startswith('readadapt'):  # readadapt12: 情報の重み×(配役のエントロピー/12)（0.3〜3倍）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, info_adapt=float(kind[9:] or 12), info_last=0.0)  # 最終ループは守りだけ（バランス版との違い）
    if kind == 'readcrit':  # 臨界に届いている対象（不足0）にも止める札の加点
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, crit=True)
    if kind == 'readnoreach':  # reach を既定にする前の主人公
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, reach=False)
    if kind == 'readreach':  # 脚本家が届く対象（不足 ≤ 置ける日数）に今日伏せ札が置かれたら止める札に加点
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, reach=True)
    if kind.startswith('readurg'):  # readurg0.3: 締め切りの迫った対象を止める札に加点する
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, urgency=float(kind[7:] or 0.3))
    if kind.startswith('read2intent'):  # read2intent0.6: 伏せ札の中身を読む read2 に、意図で世界を引き直すことを足す
        from selfplay.pc_v2 import ReadingBlocker2
        return ReadingBlocker2(rng, intent=float(kind[11:] or 0.6), info_last=None)  # mm2 の read2 と比べるため最終ループの既定は変えない
    if kind.startswith('readintent'):  # readintent0.3: 脚本家の過去の伏せ札の対象で世界を引き直す
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, intent=float(kind[10:] or 0.3))
    if kind == 'readfinal0':  # 情報の重みは既定のまま、最終ループだけ0（readsched の効き目の切り分け。旧 read の設定に固定）
        from selfplay.pc_v2 import NULL_COST, ReadingBlocker
        return ReadingBlocker(rng, info_last=0.0, loops=script['loops'], model_mm=False, null_cost=NULL_COST)
    if kind == 'readold':  # read2 を既定にする前の read（伏せ札の中身を一様に引く）
        from selfplay.pc_v2 import NULL_COST, ReadingBlocker
        return ReadingBlocker(rng, model_mm=False, null_cost=NULL_COST, info_last=None)
    if kind.startswith('readlast'):  # readlast1.0: 最終ループだけ情報の重みをこの値にする（最後の戦いに賭ける）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, info_last=float(kind[8:] or 1.0), loops=script['loops'])
    if kind.startswith('readsched'):  # readsched0.3: 情報の重みを序盤のループで重く、最終ループで0にする
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, info_weight=float(kind[9:] or 0.3), info_sched=True, loops=script['loops'])
    if kind.startswith('readmem'):  # readmem0.5: 前のループの伏せ札の対象を見込む
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, memory=float(kind[7:] or 0.5))
    if kind.startswith('readsoft'):  # readsoft0.3: 伏せ札の対象で役職を弱く読む
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, soft_gamma=float(kind[8:] or 0.3))
    if kind == 'readtt0':  # タイムトラベラーの友好の項を切った比較用
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, tt_weight=0.0)
    if kind.startswith('readh'):  # readh80: 仮説の世界の数を変えた ReadingBlocker
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, hyps=int(kind[5:]))
    if kind.startswith('read'):  # read / read1.0（情報を得る能力に近づく重み info_weight）
        from selfplay.pc_v2 import ReadingBlocker
        return ReadingBlocker(rng, info_weight=float(kind[4:] or 0.3))
    if kind == 'searchexp':  # 段D: 上位8手から3割で無作為に選び、手と結果を記録する
        return SearchMastermind(rng, days=script['days'], loops=script['loops'], explore=0.3, move_log=True)
    if kind == 'searchbandit':  # 段D: ループごとに λ を無作為に選ぶ
        return SearchMastermind(rng, days=script['days'], loops=script['loops'], lam_choices=[0.0, 0.03, 0.1, 0.3])
    if kind == 'searchroll':  # 既定の脚本家＋日の始めに上位4手を2回ずつループの終わりまで回して選ぶ（one_game の on_day）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'])
    if kind == 'searchoracle':  # 既定の脚本家＋主人公の応手を完全に読む（one_game で oracle_pc を渡す）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'])
    if kind == 'searchnoearly':  # early を既定にする前の脚本家
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], early=1.0)
    if kind.startswith('searchearly'):  # searchearly2.0: ループ1だけ押し切りの加点を2倍
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], early=float(kind[11:] or 2.0))
    if kind == 'searchguide':  # 脚本ごとの指針（engine/scripts/guides/<id>.json）を読む脚本家。指針が無い脚本では search と同じ
        import json as _j
        from engine.scripts import find
        gp = find(f"guides/{script['id']}.json")
        guide = _j.load(open(gp, encoding='utf-8')) if gp else None
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], guide=guide)
    if kind == 'searchguideS':  # searchguide＋伏せ札の所を止める応手を必ず含め（stop_replies）、最悪の応手を重く（worst 0.8）
        import json as _j
        from engine.scripts import find
        gp = find(f"guides/{script['id']}.json")
        guide = _j.load(open(gp, encoding='utf-8')) if gp else None
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], guide=guide, stop_replies=True, worst=0.8)
    if kind == 'searchJ':  # 定石（二枚押し）を候補に必ず入れる。応手は止める手を含め最悪の応手を重く（searchS8 と同じ）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], stop_replies=True, worst=0.8, joseki=True)
    if kind == 'searchJO':  # 定石の候補（主人公から脅威に見える所も対象）＋主人公の模型（世界16個）で応手を読む
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], opp=16, triples=8, joseki=True)
    if kind.startswith('searchE'):  # searchE0.5: search に「閾値の前日に友好禁止」と「確定後に犯人を止めて教えない」を足す（重みは同じ値）
        w = float(kind[7:] or 0.5)
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], gwx_eve=w, hide_culprit=w, hide=0.0)
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
    if kind.startswith('searchC'):  # searchC1: searchP1＋途中で終わる負け筋を重く（cut_mm）
        p = make('searchP1', rng, script)
        p.cut_mm = float(kind[7:] or 1)
        return p
    if kind.startswith('searchP'):  # searchP1: searchJO（定石＋主人公の模型）＋ persist（消しにくい暗躍の価値）＋定石の対象を全負け筋から
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], opp=16, triples=8, joseki=True,
                                persist=float(kind[7:] or 1), joseki_top=99, gwx_eve=1.0, hide_culprit=1.0)
    if kind == 'searchJ5':  # 定石の候補だけ足す（応手の扱いは search のまま）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], joseki=True)
    if kind == 'searchS8':  # 指針なしで、止める応手を必ず含め最悪の応手を重く
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], stop_replies=True, worst=0.8)
    if kind in ('searchtempoL', 'searchtempoM'):  # ループ単位: 仕上げの日（最終日／中ほど）までは間に合う距離より近づけても評価しない
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], tempo='last' if kind[-1] == 'L' else 'mid')
    if kind.startswith('searchknown'):  # searchknown0.5: 主人公に割れた人物が要の筋の重み（<1 避ける、>1 割れた筋で勝つ）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], known=float(kind[11:] or 0.5))
    if kind.startswith('searchrecall'):  # searchrecall0.3: 前のループの同じ日に止められた (対象, 種類) を避ける
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], recall=float(kind[12:] or 0.3))
    if kind.startswith('searchsecond'):  # searchsecond0.3: 2番目に近い負け筋（最も近いものと別の筋）を本命に
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], plan=float(kind[12:] or 0.3), plan_rank=1)
    if kind.startswith('searchplan'):  # searchplan0.3: ループの本命の負け筋に関わる札へ加点
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], plan=float(kind[10:] or 0.3))
    if kind.startswith('searchenable'):  # searchenable1: 補給の事件の犯人を臨界へ押す不安+1 に加点
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], enable=float(kind[12:] or 1))
    if kind.startswith('searchgate'):  # searchgate1: 役職の能力は、使った後の負け筋の距離が1以下になる日だけ
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], gate=float(kind[10:] or 1))
    if kind.startswith('searchchain'):  # searchchain0.2: 事件の犯人への脚本家能力の漏れの罰を0.2倍
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], chain_ab=float(kind[11:] or 0.2))
    if kind.startswith('searchhide'):  # searchhide10: ループの勝ちが確定したら漏れの重みを10倍、役職の任意能力を使わない
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], hide=float(kind[10:] or 10))
    if kind.startswith('searchmargin'):  # searchmargin0.3: 距離0の筋の条件を1つ余分に満たす手に加点（友好能力で1つ取られても崩れない）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], margin=float(kind[12:] or 0.3))
    if kind.startswith('searchstand'):  # searchstand1.0: 立っている脅威＋伏せ札の脅威で主人公の3枚を埋める手に加点
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], stand=float(kind[11:] or 1.0))
    if kind.startswith('searchconsp'):  # searchconsp0.2: 役職持ちに見える対象への札を減点（目立ち方）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], consp=float(kind[11:] or 0.2))
    if kind.startswith('searchlook'):  # searchlook4: 上位4手を2日先読みで並べ直す
        k, _, g = kind[10:].partition('g')  # searchlook8g1.0: 8手を gamma 1.0 で
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], look=int(k or 4), gamma=float(g or 0.5))
    if kind.startswith('searchbait'):  # searchbait0.3: 主人公の不安-1 を効かない札で先に使わせる
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], bait=float(kind[10:] or 0.3))
    if kind == 'searchnofit':  # fit を既定にする前の探索（札の割り当ては無作為）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], fit=False)
    if kind == 'searchfit':  # ボードに置く札を暗躍の札に寄せる（既定の v3 に足す）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], fit=True)
    if kind.startswith('searchconceal'):  # searchconceal0.1: 同じループで何度も狙った対象への札を減点
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], conceal=float(kind[13:] or 0.1))
    if kind.startswith('searchbluff'):  # searchbluff0.3: 効くおとりに加点（既定の v3 に足す）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], bluff=float(kind[11:] or 0.3))
    if kind == 'searchv2':  # v3 を既定にする前の探索（押し切りまで）
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], v3=False)
    if kind == 'searchv3':  # 押し切り＋タイムトラベラーの候補全員の友好の手間＋禁止エリアへの移動札の減点
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], v3=True)
    if kind.startswith('searchnoforce'):  # searchnoforce0.1: 押し切りの加点・固執の減点を既定にする前の探索（λ）
        return SearchMastermind(rng, lam=float(kind[13:] or 0.1), days=script['days'], loops=script['loops'], force=0.0, stuck=0.0)
    if kind.startswith('searchforce'):  # searchforce1.0: 止め手の枠を超える脅威に加点し、止められ続けている対象への固執を減点
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], force=float(kind[11:] or 1.0), stuck=0.3)
    if kind.startswith('searchnourg'):  # searchnourg0.1: 締め切りの加点を既定にする前の探索（λ）
        return SearchMastermind(rng, lam=float(kind[11:] or 0.1), days=script['days'], loops=script['loops'], urgency=0.0)
    if kind.startswith('searchurg'):  # searchurg0.3（加点）、searchurg1.0l0.3（加点と λ）: 今日置かないと間に合わない札に加点する
        u, _, lam = kind[9:].partition('l')
        return SearchMastermind(rng, lam=float(lam or 0.1), days=script['days'], loops=script['loops'], urgency=float(u or 0.3))
    if kind == 'searchshadow':  # 既定の脚本家＋影の主人公（既定の主人公と同じ手順・世界40、公開情報だけ）で応手を予想。oracle の読みを正当に近づける
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], opp=40)
    if kind.startswith('searchopp'):  # searchopp16: 主人公の模型（世界16個）で応手を予想する。置き場所の組は8つに絞る
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], opp=int(kind[9:] or 16), triples=8)
    if kind == 'searchs':  # 伏せ札の対象に止める札を置く応手を必ず見込む比較用
        return SearchMastermind(rng, lam=0.1, days=script['days'], loops=script['loops'], stop_replies=True)
    if kind.startswith('search'):  # search / search0.3（λ）
        return SearchMastermind(rng, lam=float(kind[6:] or 0.1), days=script['days'], loops=script['loops'])
    return {'route': P.RouteMastermind, 'hidden': P.HiddenMastermind, 'blind': P.DeductiveBlocker, 'oracle': P.OracleBlocker,
            'deduce': P.DeductiveProtagonist, 'random': RandomPlayer}[kind](rng)


def one_game(script, mm, pc, seed, g):
    """乱数は陣営ごと・試合ごとに分ける（敵対的レビュー4: 片方の探索を変えても、相手の手と他の試合が変わらないように）。"""
    rm, rp = random.Random(f'{seed}:{g}:mm'), random.Random(f'{seed}:{g}:pc')
    obs = IdealObserver(script)
    fb = {}

    loops_won = {}

    def log(kind, rec):
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
    r = play_game(script, Combo(mmp, pcp), log, observers=[obs], on_day=on_day)
    out = {'game': g, 'result': r, 'sec': round(time.time() - t0, 1), 'truth': obs.snaps,
           'loops_won': {str(k): v for k, v in loops_won.items()}}
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
        # 2層目
        'declared_ok': avg([g['declared_ok'] for g in fin]),
        'missed_confirmed': sum(bool(g.get('missed_confirmed')) for g in fin),
        'sec_per_game': avg([g['sec'] for g in games]),
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
