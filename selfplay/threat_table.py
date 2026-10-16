"""脅威の見えやすさと、主人公が止めたかの表（ユーザー「Claude がなぜ止めなかったかの理由を数値化」2026-10-16）。

試合を進めながら（play_game の on_day と観測者）、各日について:
- 脅威: 今日の脚本家の札で近づき、主人公が何もしなかったとき（DUMMY_PC）に残り NEAR 以下になる負け筋
- 見えやすさ vis: その日の始めの公開情報で、主人公の推理の仮説のうち、その筋に要る役職（と事件の犯人）がそろう重みの割合
  （例: キラー＝女子学生 かつ キーパーソン＝黒猫。事件の筋は犯人の候補の中で一様に掛ける）
- 止めた: 実際の主人公の札で、その筋の残りが何もしなかったときより増えた（または消えた）
を1行ずつ出す。人間（Claude）と自動の主人公で、vis に対する止める割合を比べる。

    cd play && python -m selfplay.threat_table <脚本> <脚本家> <主人公> <seed>...   （自動同士で試合を回し、1行1脅威の JSON を出す）
    目隠し対戦の表は selfplay.blind_relog が作る。"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.deduce import ROLES, incident_candidates  # noqa: E402
from engine.resolve import IllegalPlacement, resolve_actions  # noqa: E402
from engine.routes import enumerate_routes  # noqa: E402
from selfplay.players import DUMMY_PC, PublicObserver  # noqa: E402

NEAR = 1
VICTIM = {'キーパーソン': {'KEY', 'FACTOR'}, 'フレンド': {'FRIEND'}, 'ラバーズ': {'LOVERS', 'MAIN_LOVERS'}}
VIA_ROLE = {'KILLER_KEY': {'KILLER'}, 'KILLER_PROT': {'KILLER'}, 'ML_PROT': {'MAIN_LOVERS'}, 'TT_LOSS': {'TT'}}


class Snap:
    """日の始めの推理の写し（仮説の行列と重み、事件の犯人の候補）。"""
    def __init__(self, ob, s):
        ob._ensure(s)
        R, C = ob.ded._sync()
        self.R, self.w = R.copy(), ob.ded._w(C).copy()
        self.combos = [ob.ded.T.combos[int(i)] for i in C]
        self.col = {c: j for j, c in enumerate(ob.ded.chars)}
        alive = [c for c, v in s['chars'].items() if v['alive'] and v.get('present', True)]
        self.cands = incident_candidates(alive, ob.inc_history, ob.culprits)

    def p_roles(self, reqs, rule=None):
        import numpy as np
        if not len(self.w) or self.w.sum() == 0:
            return None
        m = np.ones(len(self.w), bool)
        for c, roles in reqs:
            if c not in self.col:
                return None
            m &= np.isin(self.R[:, self.col[c]], [ROLES.index(r) for r in roles if r in ROLES])
        if rule:
            m &= np.array([rule in cb for cb in self.combos])
        return float(self.w[m].sum() / self.w.sum())

    def p_culprit(self, inc_id, day, via):
        cand = self.cands.get(f'{inc_id}@{day}')
        if cand is None:
            return None
        return (1 / len(cand)) if via in cand else 0.0


def visibility(snap, r, s):
    rid, via, goal = r['id'], r['via'], r['goal']
    vrole = next((v for k, v in VICTIM.items() if f'（{k}）' in goal), None)
    victim = goal.split()[0] if vrole else None
    reqs = [(victim, vrole)] if victim else []
    if rid.startswith('Y_'):
        return snap.p_roles(reqs, rule=rid)
    if rid == 'SK_KILL':
        reqs.append((via, {'PERSON'} if '妄想拡大ウイルス' in r.get('note', '') else {'SK'}))
    elif rid in VIA_ROLE:
        reqs.append((via, VIA_ROLE[rid]))
    pr = snap.p_roles(reqs) if reqs else None
    inc = next((c['detail'] for c in r['conds'] if c['kind'] == 'days' and c['detail'][0] == '事件'), None)
    if inc and via:
        pc = snap.p_culprit(inc[1], inc[2], via)
        if pc is None:  # まだ判定されていない事件: 犯人の手がかりは不安の高さだけ。生存者で一様（ponytail: 不安の見た目は使わない）
            pc = 1 / max(1, sum(1 for v in s['chars'].values() if v['alive'] and v.get('present', True)))
        pr = pc if pr is None else pr * pc
    return pr


class Table:
    """play_game に on_day と観測者（self.ob）で付ける。試合の後で rows(log) を呼ぶ。"""
    def __init__(self):
        self.ob, self.days = PublicObserver(), {}

    def on_day(self, s, *_):
        self.days[(s['loop'], s['day'])] = (copy.deepcopy(s), Snap(self.ob, s))

    def rows(self, log):
        mm, out = {}, []
        for k, rec in log:
            if k == 'decisions' and rec.get('kind') == 'place_cards':
                key = (rec['loop'], rec['day'])
                if rec['who'] == 'M':
                    mm[key] = rec['choice']
                elif key in mm and key in self.days:
                    s, snap = self.days[key]
                    out += _day_rows(s, snap, mm[key], rec['choice'])
        return out


def _day_rows(s, snap, mm, pc):
    try:
        dummy, _ = resolve_actions(s, mm + DUMMY_PC)
        real, _ = resolve_actions(s, mm + pc)
    except IllegalPlacement:
        return []
    key = lambda x: (x['id'], x['via'], x['goal'])  # noqa: E731
    real_d = {key(x): x['total'] for x in enumerate_routes(real)}
    start_d = {key(x): x['total'] for x in enumerate_routes(s)}
    seen, out = set(), []
    for x in enumerate_routes(dummy):
        k = key(x)
        if x['total'] > NEAR or k in seen or '登場前' in x.get('note', '') or start_d.get(k, 99) <= x['total']:
            continue
        seen.add(k)
        v = visibility(snap, x, s)
        out.append({'loop': s['loop'], 'day': s['day'], 'route': k[0], 'via': k[1], 'goal': k[2], 'dist': x['total'],
                    'vis': None if v is None else round(v, 3), 'blocked': real_d.get(k, 99) > x['total']})
    return out


def bands(rows, label=''):
    for lo, hi in [(0, .1), (.1, .25), (.25, .5), (.5, .75), (.75, 1.01)]:
        xs = [x for x in rows if x['vis'] is not None and lo <= x['vis'] < hi]
        if xs:
            print(f"{label}vis {lo:.2f}-{min(hi, 1):.2f}: 脅威 {len(xs):4}  止めた {sum(x['blocked'] for x in xs) / len(xs):.2f}")


def play(sid, mmk, pck, seed):
    import random
    from engine.scripts import by_id
    from selfplay.league import make
    from selfplay.run import Combo, play_game
    sc, rng = by_id(sid), random.Random(int(seed))
    t, log = Table(), []
    play_game(sc, Combo(make(mmk, rng, sc), make(pck, rng, sc)), lambda k, r: log.append((k, r)), observers=(t.ob,), on_day=t.on_day)
    return [dict(x, game=f'{sid}:{seed}') for x in t.rows(log)]


if __name__ == '__main__':
    import json
    sid, mmk, pck, *seeds = sys.argv[1:]
    for sd in seeds:
        for x in play(sid, mmk, pck, sd):
            print(json.dumps(x, ensure_ascii=False))
