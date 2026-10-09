"""段D: 同じ局面で候補の手を比べるデータ（手の良し悪しを、局面の良し悪しから切り離して学ぶため）。

対戦（先読みする脚本家 対 伏せ札を読む主人公）の途中、確率 p の日に、脚本家の候補の上位 K 手と、下位から無作為の1手について、
ゲームの状態と両陣営のプレイヤーを複製し、その手でその日の終わりまで回す（play_day）。手ごとに結果を記録する:
- won: その日にループを取った
- dscore: その日の終わりの勝ち筋の評価（players.score）− 始めの評価
- leak: 脚本家が持つ公開情報の推理の、配役のエントロピーの減り（ビット）
本当の対戦は分岐とは別に、ふだんどおり進む。

    cd play && python -m selfplay.branch_moves --games 20 --script toukou_001 --out selfplay/runs/branch_toukou.jsonl
"""
import argparse
import copy
import json
import math
import multiprocessing
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import phases as ph  # noqa: E402
from engine.features import state_features  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.players import score  # noqa: E402
from selfplay.run import Combo, play_day, play_game  # noqa: E402


def knowledge_features(s, mm, cards, script):
    """判断の時点で主人公に割れている情報（脚本家が持つ公開情報の推理 mm.pub.ded から）。
    同じ盤面でも、何が割れているかでそのループの正解が変わる（ユーザーの観点、2026-09-25）。
    - 全体: 真の配役・ルールの組・ルールY・ルールX に主人公が置く確率、配役のエントロピー、確定した役職の数
    - 勝ち筋: 近い勝ち筋（上位3）の要（経由するキャラクターの真の役職、無ければ筋のルール）を主人公が見抜いている確率
    - 手: 伏せ札の対象が役職持ちに見える確率（主人公から見た目立ち方）"""
    import numpy as np
    from engine.deduce import COMBOS, RC, ROLES, truth_metrics
    from engine.routes import enumerate_routes
    ded = mm.pub.ded
    tm = truth_metrics(ded, script)
    R, C = ded._sync()
    M, tot = ded._marg(R, C)
    w = ded._w(C)
    tot = tot or 1.0
    idx = {c: j for j, c in enumerate(ded.chars)}

    def p_rule(rule):
        ci = [i for i, cb in enumerate(COMBOS) if rule in cb]
        return float(w[np.isin(C, ci)].sum() / tot)

    def p_role(c, role):
        return float(M[idx[c], RC[role]] / tot) if c in idx else 0.0
    rules = script['rules']
    f = {'n_hyp_log': math.log2(max(1, tm['n_hyp'])), 'p_truth_log': -(tm['nlog_p_truth'] or 40.0), 'mean_true': tm['mean_true'],
         'role_ent': tm['role_entropy'], 'confirmed': tm['confirmed'], 'p_rules': tm['p_rules'],
         'p_Y': p_rule(rules[0]), 'p_X_min': min([p_rule(r) for r in rules[1:]], default=1.0)}
    vis = []
    for r in enumerate_routes(s)[:3]:
        via = r.get('via')
        role = script['roles'].get(via) if via else None
        vis.append(p_role(via, role) if role else p_rule(r['id']) if r['id'] in rules else 1.0)
    f['route_vis_min'] = min(vis, default=1.0)
    f['route_vis_mean'] = sum(vis) / len(vis) if vis else 1.0
    tg = [x['target'] for x in cards if not x['target'].startswith('B:')]
    f['tgt_nonperson'] = sum(1 - p_role(t, 'PERSON') for t in tg) / len(tg) if tg else 0.0
    return f


def knowledge_variant(player, s, script, variant):
    """同じ盤面で、主人公の知識だけを差し替えた複製（ユーザーの観点: 同じ配置でも、過去の履歴や割れている情報で最善手が変わる）。
    主人公自身（pc）と、脚本家が見積もる主人公の頭の中（mm.pub）の両方を同じように差し替える。
    - blank: 過去の履歴を知らない（推理・公開された役職・事件の記録・伏せ札の履歴を空にする。公開シートだけ）
    - reveal: 近い勝ち筋の要（経由するキャラクター）の真の役職が割れている"""
    p = copy.deepcopy(player)
    obs = [p.pc, p.mm.pub]
    if variant == 'blank':
        for o in obs:
            o.ded, o.revealed, o.culprits, o.inc_history = None, {}, {}, []
            o._ensure(s)
        for attr in ('history', 'targeted'):
            if hasattr(p.pc, attr):
                setattr(p.pc, attr, {})
    elif variant == 'reveal':
        from engine.routes import enumerate_routes
        vias = []
        for r in enumerate_routes(s)[:3]:
            v = r.get('via')
            if v and script['roles'].get(v) and v not in vias:
                vias.append(v)
        for o in obs:
            o._ensure(s)
            for v in vias[:2]:
                o.ded.observe('role_revealed', char=v, role=script['roles'][v])
                o.revealed[v] = script['roles'][v]
    return p


def branch(s, player, loop, day, mm_cards, days=None, reseed=None):
    """複製した状態とプレイヤーで、その手のまま1日を回し、結果を返す。
    days を渡すと、続けてループの終わりまで回し、脚本家がそのループを取ったか（won_loop: 途中で終わった・ループ終了時の敗北）も返す
    （ラベルを1日ではなくループの終わりまでの結果にする。段D の結果の節の結論）。"""
    s2, p2 = copy.deepcopy(s), copy.deepcopy(player)
    if reseed is not None:  # 同じ手を別の展開で回す（ラベルの揺れを平均で抑える）
        p2.mm.rng = random.Random(f'{reseed}:m')
        p2.pc.rng = p2.rng = random.Random(f'{reseed}:p')
    box = {'s': s2}

    def log(kind, rec):
        if kind == 'events' and 'events' in rec:
            p2.observe(box['s'], rec.get('phase'), rec['events'])
    p2.mm.pub._ensure(s2)
    ent0, sc0 = p2.mm.pub.ded.role_entropy(), score(s2)
    won = 0
    try:
        s2 = play_day(s2, p2, loop, day, log, box, mm_override=mm_cards)
    except ph.LoopEnd as e:
        won = int(bool(e.loss))
        s2 = box['s']
    out = {'won': won, 'dscore': (score(s2) - sc0) if not won else None, 'leak': ent0 - p2.mm.pub.ded.role_entropy()}
    if days:
        won_loop = won
        if not won:
            try:
                for d in range(day + 1, days + 1):
                    s2['day'] = d
                    s2 = play_day(s2, p2, loop, d, log, box)
                won_loop = int(bool(ph.loop_end_loss(s2)))
            except ph.LoopEnd as e:
                won_loop = int(bool(e.loss) or bool(ph.loop_end_loss(box['s'])))
        out['won_loop'] = won_loop
    return out


def one(args):
    sid, seed, g, p, k, horizon, mmk, reps, variants = args
    script = by_id(sid)
    rng = random.Random(f'{seed}:{g}:branch')
    out = []

    def on_day(s, player, loop, day, box):
        if rng.random() >= p:
            return
        mm = player.mm
        scored = mm.rank_moves(s)
        if len(scored) < 2:
            return
        picks = list(range(min(k, len(scored))))
        if len(scored) > k:
            picks.append(rng.randrange(k, len(scored)))  # 下位から無作為に1手
        sf = state_features(s, script['days'], script['loops'])
        mm.pub._ensure(s)
        base = f'{sid}:{g}:{loop}:{day}'
        for var in variants:
            pv = player if var == 'real' else knowledge_variant(player, s, script, var)
            gid = base if var == 'real' else f'{base}:{var}'
            for r in picks:
                v, cards, v_reply, v_mean = scored[r]
                res = branch(s, pv, loop, day, cards, script['days'] if horizon == 'loop' else None)
                if horizon == 'loop' and reps > 1:
                    wl = [res['won_loop']] + [branch(s, pv, loop, day, cards, script['days'], reseed=f'{gid}:{r}:{i}')['won_loop']
                                              for i in range(1, reps)]
                    res['won_loop'] = sum(wl) / len(wl)
                out.append({'group': gid, 'base_group': base, 'variant': var, 'script': sid, 'loop': loop, 'day': day, 'rank': r, 'val': v,
                            'val_reply': v_reply, 'val_mean': v_mean, 'cards': [(x['target'], x['card']) for x in cards],
                            'feat': mm._move_features(s, cards), 'sfeat': sf, 'kfeat': knowledge_features(s, pv.mm, cards, script), **res})
    player = Combo(make(mmk, random.Random(f'{seed}:{g}:mm'), script), make(PC_KIND, random.Random(f'{seed}:{g}:pc'), script))
    try:
        play_game(script, player, lambda *a: None, on_day=on_day)
    except Exception as e:  # 1試合の失敗で全体を止めない
        out.append({'group': None, 'error': repr(e)[:200]})
    out.append({'group': None, 'done': f'{sid}:{seed}:{g}'})  # 中断・再開用の印（同じ出力に続きから書く）
    return out


PC_KIND, MM_KIND = 'read', 'search'  # --pc・--mm（fork で子プロセスに引き継ぐ）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--games', type=int, default=20)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--script', nargs='+', default=['s03_bomb'])
    ap.add_argument('--p', type=float, default=0.35, help='分岐する日の割合')
    ap.add_argument('--k', type=int, default=4, help='上位何手を分岐するか')
    ap.add_argument('--procs', type=int, default=6)
    ap.add_argument('--horizon', default='day', choices=['day', 'loop'], help='loop: ループの終わりまで回して won_loop を付ける')
    ap.add_argument('--reps', type=int, default=1, help='loop のとき、同じ手をループの終わりまで回す回数（won_loop はその平均）')
    ap.add_argument('--variants', default='real', help='主人公の知識の変種（カンマ区切り）: real,blank,reveal')
    ap.add_argument('--mm', default='search0.1', help='脚本家の種類（league.make）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--pc', default='read', help='主人公の種類（league.make）')
    a = ap.parse_args()
    global PC_KIND, MM_KIND
    PC_KIND = a.pc
    jobs = [(sid, a.seed, g, a.p, a.k, a.horizon, a.mm, a.reps, a.variants.split(',')) for sid in a.script for g in range(a.games)]
    # 中断・再開: 出力に「済み」の印がある試合は飛ばす。--procs を変えて同じ --out で起動し直せば続きから（ユーザーの観点）
    done = set()
    if os.path.exists(a.out):
        for line in open(a.out, encoding='utf-8'):
            if '"done"' in line:
                done.add(json.loads(line)['done'])
    jobs = [j for j in jobs if f'{j[0]}:{j[1]}:{j[2]}' not in done]
    print('残りの試合', len(jobs), '（済み', len(done), '）', flush=True)
    with multiprocessing.Pool(a.procs) as pool, open(a.out, 'a', encoding='utf-8') as f:
        n = 0
        for rows in pool.imap_unordered(one, jobs):
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
                n += 1
            f.flush()
    print('行', n, '→', a.out)


if __name__ == '__main__':
    main()
