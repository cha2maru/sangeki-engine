"""段D（主人公側）: 同じ局面・同じ脚本家の伏せ札で、主人公の候補の手を比べるデータ。

対戦の途中、確率 p の日に、脚本家の伏せ札を1つに固定し、主人公の候補（評価の上位 K 手と、下位から無作為の1手）ごとに
ゲームの状態と両陣営を複製して、その手でループの終わりまで回す。won_loop は脚本家がそのループを取ったか（主人公には小さいほど良い）。
--variants で、主人公の知識だけを差し替えた複製（branch_moves.knowledge_variant）でも同じ手を回す
（ユーザーの観点: 同じ配置でも、過去の履歴や割れている情報で最善手が変わる。主人公にも脚本家にも効く）。

    cd play && python -m selfplay.branch_pc --games 20 --script toukou_001 --variants real,blank,reveal --out selfplay/runs/branchpc.jsonl
"""
import argparse
import copy
import json
import multiprocessing
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import phases as ph  # noqa: E402
from engine.features import card_relevance, state_features  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.branch_moves import knowledge_features, knowledge_variant  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.run import Combo, play_day, play_game  # noqa: E402


def pc_move_features(s, pc):
    f = {f'n_{c}': 0 for c in ('PAR+', 'PAR-', 'GW1', 'GW2', 'INTX', 'MVX', 'MV_V', 'MV_H')}
    rel = []
    for x in pc:
        f['n_' + x['card']] = f.get('n_' + x['card'], 0) + 1
        rel.append(card_relevance(s, x['target']))
    rel = sorted(rel) + [99] * (3 - len(rel))
    f.update({'rel_min': rel[0], 'rel_mid': rel[1], 'rel_max': rel[2]})
    return f


def rollout(s, player, loop, day, mm_cards, pc_cards, days, reseed=None, script=None):
    """主人公の今日の札を pc_cards に固定して、ループの終わりまで回す。(脚本家がループを取れば 1, 主人公の推理の真相への近さの伸び)。
    近さの伸び＝ループの終わりの −log p(真相) の減り（ビット）。情報を取る手の値打ちを、勝ち負けと別に測る（ユーザーの観点:
    知識が白紙のときは情報を取る方が強いのでは）。"""
    s2, p2 = copy.deepcopy(s), copy.deepcopy(player)
    if reseed is not None:
        p2.mm.rng = random.Random(f'{reseed}:m')
        p2.pc.rng = p2.rng = random.Random(f'{reseed}:p')
    orig, used = p2.pc.pc_cards, []

    def once(*a, **k):
        if not used:
            used.append(1)
            return [dict(x) for x in pc_cards]
        return orig(*a, **k)
    p2.pc.pc_cards = once
    box = {'s': s2}
    from engine.deduce import truth_metrics
    p2.pc._ensure(s2)
    t0 = truth_metrics(p2.pc.ded, script)['nlog_p_truth'] if script else None

    def gain():
        if not script:
            return None
        t1 = truth_metrics(p2.pc.ded, script)['nlog_p_truth']
        return (t0 - t1) if (t0 is not None and t1 is not None) else None

    def log(kind, rec):
        if kind == 'events' and 'events' in rec:
            p2.observe(box['s'], rec.get('phase'), rec['events'])
    try:
        s2 = play_day(s2, p2, loop, day, log, box, mm_override=mm_cards)
        for d in range(day + 1, days + 1):
            s2['day'] = d
            s2 = play_day(s2, p2, loop, d, log, box)
        return int(bool(ph.loop_end_loss(s2))), gain()
    except ph.LoopEnd as e:
        return int(bool(e.loss) or bool(ph.loop_end_loss(box['s']))), gain()


def _apply_variant_inplace(player, s, script, variant):
    """knowledge_variant と同じ差し替えを、その場の player に行う（再生中の試合を続けるため）。"""
    if variant == 'real':
        return
    v = knowledge_variant(player, s, script, variant)
    player.pc, player.mm.pub = v.pc, v.mm.pub
    player.rng = player.pc.rng


def game_rollout(sid, seed, g, loop, day, variant, mm_cards, pc_cards, reseed=None):
    """同じ種で試合を最初から再生し、(loop, day) で主人公の知識を変種に差し替え、両陣営の札を固定して、ゲームの終わりまで回す。
    主人公がゲームに勝てば 1（ループを守っても、最後の戦いで勝っても同じ価値。ユーザーの観点）。"""
    script = by_id(sid)
    player = Combo(make(MM_KIND, random.Random(f'{seed}:{g}:mm'), script), make(PC_KIND, random.Random(f'{seed}:{g}:pc'), script))

    def on_day(s, pl, lp, d, box):
        if (lp, d) != (loop, day):
            return
        _apply_variant_inplace(pl, s, script, variant)
        for side, cards in (('pc', pc_cards), ('mm', mm_cards)):
            obj = getattr(pl, side)
            name = 'pc_cards' if side == 'pc' else 'mm_cards'
            orig, used = getattr(obj, name), []

            def once(*a, _orig=orig, _used=used, _cards=cards, **k):
                if not _used:
                    _used.append(1)
                    return [dict(x) for x in _cards]
                return _orig(*a, **k)
            setattr(obj, name, once)
        if reseed is not None:  # 分岐の日より後の展開を変える（同じ手を別の展開で回す）
            pl.mm.rng = random.Random(f'{reseed}:m')
            pl.pc.rng = pl.rng = random.Random(f'{reseed}:p')
    r = play_game(script, player, lambda *a: None, on_day=on_day)
    return int(r['winner'] == 'protagonists')


def one(args):
    sid, seed, g, p, k, reps, variants, horizon = args
    script = by_id(sid)
    rng = random.Random(f'{seed}:{g}:branchpc')
    out = []

    def on_day(s, player, loop, day, box):
        if rng.random() >= p:
            return
        mm = copy.deepcopy(player.mm).mm_cards(copy.deepcopy(s))  # この局面の脚本家の伏せ札（本物の脚本家の状態は変えない）
        order = ['A', 'B', 'C']
        i = order.index(s['leader'])
        porder = order[i:] + order[:i]
        sf = state_features(s, script['days'], script['loops'])
        base = f'{sid}:{g}:{loop}:{day}'
        # 候補の手は実際の知識の主人公から作り、全変種で同じ手を回す（変種の間で最善手が変わるかを比べるため。
        # 変種ごとに作り直すと候補の顔ぶれがそもそも違い、比べられなかった）
        pcc = copy.deepcopy(player.pc)
        pcc.debug, pcc.last_debug = True, []
        pcc.pc_cards(copy.deepcopy(s), porder, [x['target'] for x in mm])
        cands = sorted(pcc.last_debug, key=lambda r: r[0])  # 主人公の評価（小さいほど良い）
        if len(cands) < 2:
            return
        picks = list(range(min(k, len(cands))))
        if len(cands) > k:
            picks.append(rng.randrange(k, len(cands)))
        for var in variants:
            pv = player if var == 'real' else knowledge_variant(player, s, script, var)
            shim = type('Shim', (), {'pub': pv.pc})()
            gid = base if var == 'real' else f'{base}:{var}'
            for r in picks:
                val, _, _, cards = cands[r]
                pc = [{'by': b, 'target': t, 'card': c} for t, c, b in cards]
                res = [rollout(s, pv, loop, day, mm, pc, script['days'], reseed=None if n == 0 else f'{gid}:{r}:{n}', script=script)
                       for n in range(reps)]
                gw = None
                if horizon == 'game':
                    gws = [game_rollout(sid, seed, g, loop, day, var, mm, pc, reseed=None if n == 0 else f'{gid}:{r}:{n}') for n in range(reps)]
                    gw = sum(gws) / len(gws)
                wl = [x[0] for x in res]
                tg = [x[1] for x in res if x[1] is not None]
                out.append({'group': gid, 'base_group': base, 'variant': var, 'script': sid, 'loop': loop, 'day': day, 'rank': r,
                            'val': -float(val), 'cards': cards, 'feat': pc_move_features(s, pc), 'sfeat': sf,
                            'kfeat': knowledge_features(s, shim, pc, script), 'won_loop': sum(wl) / len(wl),
                            'util': 1 - sum(wl) / len(wl), 'truth_gain': sum(tg) / len(tg) if tg else None, 'game_win': gw,
                            'won': 0, 'dscore': 0.0, 'leak': 0.0})
    player = Combo(make(MM_KIND, random.Random(f'{seed}:{g}:mm'), script), make(PC_KIND, random.Random(f'{seed}:{g}:pc'), script))
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
    ap.add_argument('--p', type=float, default=0.25)
    ap.add_argument('--k', type=int, default=4)
    ap.add_argument('--reps', type=int, default=2)
    ap.add_argument('--variants', default='real')
    ap.add_argument('--horizon', default='loop', choices=['loop', 'game'], help='game: ゲームの終わりまで回して game_win も付ける（重い）')
    ap.add_argument('--procs', type=int, default=6)
    ap.add_argument('--out', required=True)
    ap.add_argument('--pc', default='read', help='主人公の種類（league.make）')
    ap.add_argument('--mmk', default='search', help='脚本家の種類（league.make）')
    a = ap.parse_args()
    global PC_KIND, MM_KIND
    PC_KIND = a.pc
    MM_KIND = a.mmk
    jobs = [(sid, a.seed, g, a.p, a.k, a.reps, a.variants.split(','), a.horizon) for sid in a.script for g in range(a.games)]
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
                f.write(json.dumps(r, ensure_ascii=False, default=float) + '\n')
                n += 1
            f.flush()
    print('行', n, '→', a.out)


if __name__ == '__main__':
    main()
