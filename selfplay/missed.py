"""「取れたはずの日」の候補（作業計画 A-1c）: 目隠し対戦の全記録（private/full_log.jsonl）の各日で、主人公の実際の札と能力を固定し、
脚本家の候補の手を1つずつ当てて1日を進め、脚本家がその日に勝つか（最終日はループ終了時の判定も）、翌朝の逆算が「必ず取れる」に入るかを見る。

後知恵（主人公の手を知っている）なので「取れた」の証明ではない。見逃しの候補を出す道具で、原因は人が analysis で読む。
候補の手: 型（--kind）の探索の上位（rank_moves）と、筋に関わる置き先×手札の無作為の組。脚本家の能力・事件・ターン終了の選択はその型に任せる。
止め手の残り（作業計画 C-1）: その朝の主人公の移動禁止・不安−1 の残り枚数も出す。

    cd play && python -m selfplay.missed selfplay/runs/blind_calcGBxhdKNi_2026-11-09f [--loop 3] [--kind calcGBxhdKNi] [--rand 150]
"""
import argparse
import copy
import itertools
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import phases as ph  # noqa: E402
from engine.resolve import CARDS, ONCE  # noqa: E402
from engine.route_calc import forced_plan  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.run import Combo, play_day  # noqa: E402


class Fixed:
    """主人公: 記録どおりの札と能力の宣言を返す。"""
    def __init__(self, pc, abilities):
        self.rng, self._pc, self._ab = random.Random(0), pc, abilities

    def pc_cards(self, s, order, targets=None):
        return copy.deepcopy(self._pc)

    def abilities_for_day(self, s):
        return [tuple(x) for x in self._ab]

    def final_guesses(self, s):
        return {}


def days_of(path):
    """全記録から日ごとに (朝の盤面, 脚本家の実際の札, 主人公の札, 主人公の能力の宣言)。"""
    out, cur = [], None
    for line in open(path, encoding='utf-8'):
        r = json.loads(line)
        if r.get('k') == 'state':
            cur = {'s': r['state'], 'mm': None, 'pc': None, 'ab': []}
            out.append(cur)
        elif cur and r.get('k') == 'decisions' and r.get('kind') == 'place_cards':
            cur['mm' if r['who'] == 'M' else 'pc'] = r['choice']
        elif cur and r.get('k') == 'decisions' and r.get('phase') == 'ability' and r.get('kind') == 'ability':
            c = r['choice']
            cur['ab'].append((c['char'], c['ability'], c.get('arg')))
    return [d for d in out if d['mm'] is not None and d['pc'] is not None]


def outcome(s0, mm, d, mk):
    """mm を置いた1日の結果: 'win'（その日に脚本家の勝ち）・'forced'（翌朝に必ず取れる）・'-'・'illegal'。"""
    s = copy.deepcopy(s0)
    player = Combo(mk(), Fixed(d['pc'], d['ab']))
    box = {}
    try:
        after = play_day(s, player, s['loop'], s['day'], lambda *a: None, box, mm_override=copy.deepcopy(mm))
    except ph.LoopEnd as e:
        return 'win' if e.loss else '-'
    except ph.IllegalPlacement:
        return 'illegal'
    days = after['script']['days']
    if s0['day'] >= days:
        return 'win' if ph.loop_end_loss(after) else '-'
    nxt = dict(after, day=s0['day'] + 1)
    try:
        return 'forced' if forced_plan(nxt, days, pcab=True)['forced'] else '-'
    except Exception:
        return '-'


def candidates(s, mmk, script_id, n_rank, n_rand, seed):
    """探索の上位と、置き先×手札の無作為の組。"""
    mm = mmk()
    hand = [c for c in CARDS['mastermind_hand'] if not (c in ONCE['M'] and c in s['used']['M'])]
    out = []
    try:
        mm._gbonus, mm._gp, mm._reply_of = {}, {}, {}  # mm_cards の入口で作るもの（rank_moves を直接呼ぶため）
        if hasattr(mm, '_start_loop'):
            mm._start_loop(s)
        for _v, cards, *_ in mm.rank_moves(copy.deepcopy(s))[:n_rank]:
            out.append([{'by': 'M', 'target': x['target'], 'card': x['card']} for x in cards])
    except Exception as e:  # 探索が履歴なしで動かないとき（推理の記憶を持たないため）は無作為の組だけ
        print(f'  （rank_moves を使えなかった: {type(e).__name__}）')
    targets = mm._targets(s)
    r = random.Random(seed)
    for _ in range(n_rand):
        ts = r.sample(targets, min(3, len(targets)))
        cs = r.sample(hand, len(ts))
        out.append([{'by': 'M', 'target': t, 'card': c} for t, c in zip(ts, cs)])
    seen, uniq = set(), []
    for m in out:
        k = frozenset((x['target'], x['card']) for x in m)
        if k not in seen:
            seen.add(k)
            uniq.append(m)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('--kind', default='calcGBxhdKNi')
    ap.add_argument('--loop', type=int, default=None)
    ap.add_argument('--rank', type=int, default=20)
    ap.add_argument('--rand', type=int, default=150)
    a = ap.parse_args()
    sid = open(os.path.join(a.run, 'script_id.txt')).read().strip()
    from engine.scripts import by_id
    script = by_id(sid)
    mk = lambda: make(a.kind, random.Random(1), script)  # noqa: E731
    for d in days_of(os.path.join(a.run, 'private', 'full_log.jsonl')):
        s = d['s']
        if a.loop and s['loop'] != a.loop:
            continue
        s.setdefault('script', {})['id'] = script['id']
        left = {c: sum(1 for p in 'ABC' if c not in s['used'][p]) for c in ('MVX', 'PAR-')}
        actual = outcome(s, d['mm'], d, mk)
        res = {}
        for m in candidates(s, mk, sid, a.rank, a.rand, f"{s['loop']}:{s['day']}"):
            o = outcome(s, m, d, mk)
            res.setdefault(o, []).append(m)
        wins, forced = res.get('win', []), res.get('forced', [])
        fmt = lambda m: ' '.join(f"{x['target']}={x['card']}" for x in m)  # noqa: E731
        print(f"L{s['loop']}D{s['day']} 実際 {actual:6} 移動禁止の残り {left['MVX']} 不安−1 の残り {left['PAR-']}  "
              f"その日に勝つ候補 {len(wins)}・翌朝に必ず取れる候補 {len(forced)}（{sum(len(v) for v in res.values())} 候補中）  実際の手: {fmt(d['mm'])}")
        for m in (wins + forced)[:3]:
            print(f"    {'勝ち' if m in wins else '必勝'}: {fmt(m)}")
        sys.stdout.flush()


if __name__ == '__main__':
    main()
