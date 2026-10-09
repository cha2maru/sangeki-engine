"""次の一手問題の点数で、ロジックの重みを CPU だけで調整する（ユーザー「できる限りはやくトークンではなくCPUで学習ができる環境を整えたい」2026-10-07）。

1. 問題ごとに、その局面まで再生して育てたロジック（基準の型）を一度だけ作って取っておく（再生は重いので1回だけ）。
2. 重みの組を変えるたびに、その複製に重みを属性として差し込み、局面の手だけを聞いて採点する。
3. 重みの組を (1+λ) の進化戦略で探す（正規化した空間でガウスの揺らぎ）。調整用（tune）の点で選び、確かめ用（hold）の点も記録する。

    cd play && python -m selfplay.tune positions/*.json --side mm --base search --iters 20 --pop 8 --procs 8 --out selfplay/runs/tune_mm.jsonl
"""
import argparse
import copy
import json
import multiprocessing
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from selfplay import positions as P  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402

# 調整する重み（属性名: (下限, 上限)）。属性名は SearchMastermind / ReadingBlocker の self.<名前>
SPACE = {
    'mm': {'worst': (0.0, 1.0), 'stuck': (0.0, 1.0), 'force': (0.0, 2.0), 'early': (1.0, 3.0), 'lam': (0.02, 0.5),
           'urgency': (0.0, 2.0), 'hold': (0.0, 0.5), 'bluff': (0.0, 1.0), 'recall': (0.0, 1.0), 'margin': (0.0, 1.0),
           'gwx_eve': (0.0, 3.0), 'hide_culprit': (0.0, 3.0), 'persist': (0.0, 3.0), 'cut_mm': (0.0, 3.0)},
    'pc': {'urgency': (0.0, 1.0), 'hold': (0.0, 0.5), 'null_cost': (0.0, 0.5), 'nulled_w': (0.0, 1.0), 'info_weight': (0.0, 2.0),
           'safe': (0.0, 1.0), 'zero': (0.0, 1.0), 'obs': (0.0, 1.0), 'defense': (0.0, 1.0), 'intent': (0.0, 1.0), 'recall_mm': (0.0, 2.0), 'probe': (0.0, 4.0), 'reach_info': (0.0, 2.0), 'board_waste': (0.0, 2.0), 'cut_w': (0.0, 3.0)},
}
SNAP = {}  # 問題 id → (ロジック, 盤面, 引数)。fork で子プロセスに引き継ぐ


def snapshot(prob, base, seed):
    script, mm, pc = P.replay_to(prob)
    logic = make(base, random.Random(seed), script)
    try:
        play_game(script, Combo(mm, pc), lambda *a: None, observers=[logic])
    except P.Reached as r:
        return logic, copy.deepcopy(r.s), r.args
    raise RuntimeError(f"{prob['id']}: 局面に届かなかった")


def answer(prob, logic, s, args):
    k = prob['at']['kind']
    if k == 'mm_cards':
        return [{'target': x['target'], 'card': x['card']} for x in logic.mm_cards(s)]
    if k == 'incident':
        ch = logic.incident_choice(s, args[0])
        return [ch] if ch else []
    order, targets = args
    return [{'target': x['target'], 'card': x['card']} for x in logic.pc_cards(s, order, targets)]


def solve(prob, params, samples):
    ok = 0
    for i in range(samples):
        logic, s, args = copy.deepcopy(SNAP[prob['id']])
        for k, v in params.items():
            setattr(logic, k, v)
        logic.rng = random.Random(f"{prob['id']}:{i}")
        mv = answer(prob, logic, s, args)
        good = P.match(mv, prob['good']) if prob.get('good') else True
        ok += good and not P.match(mv, prob.get('bad', []))
    return ok / samples


def score(params, probs, samples):
    return sum(solve(p, params, samples) for p in probs) / max(1, len(probs))


def _eval(job):
    params, tune, hold, samples = job
    return params, score(params, tune, samples), score(params, hold, samples) if hold else None


def _snap(job):
    p, base = job
    return p['id'], snapshot(p, base, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--side', choices=['mm', 'pc'], required=True)
    ap.add_argument('--base', required=True, help='基準のロジックの型（league.make）')
    ap.add_argument('--iters', type=int, default=20)
    ap.add_argument('--pop', type=int, default=8)
    ap.add_argument('--sigma', type=float, default=0.2, help='正規化した空間での揺らぎの大きさ')
    ap.add_argument('--samples', type=int, default=2)
    ap.add_argument('--procs', type=int, default=4)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    probs = [p for f in a.files for p in json.load(open(f, encoding='utf-8'))
             if (p['at']['kind'] == 'pc_cards') == (a.side == 'pc')]
    tune = [p for p in probs if p.get('split', 'tune') == 'tune']
    hold = [p for p in probs if p.get('split') == 'hold']
    print(f'問題 調整用 {len(tune)}・確かめ用 {len(hold)}', flush=True)
    with multiprocessing.Pool(a.procs) as pool:
        for pid, snap in pool.map(_snap, [(p, a.base) for p in probs]):
            SNAP[pid] = snap
    space = SPACE[a.side]
    base_logic = next(iter(SNAP.values()))[0]
    cur = {k: float(getattr(base_logic, k)) for k in space if getattr(base_logic, k, None) is not None
           and isinstance(getattr(base_logic, k), (int, float))}
    space = {k: v for k, v in space.items() if k in cur}
    rng = random.Random(a.seed)
    out = open(a.out, 'a', encoding='utf-8')

    def propose(c):
        q = {}
        for k, (lo, hi) in space.items():
            x = (c[k] - lo) / (hi - lo) + rng.gauss(0, a.sigma)
            q[k] = round(lo + min(1, max(0, x)) * (hi - lo), 4)
        return q
    with multiprocessing.Pool(a.procs) as pool:  # 再生の結果（SNAP）を持った状態で fork し直す
        _, best, best_hold = _eval((cur, tune, hold, a.samples))
        print(f'基準 tune {best:.3f} hold {best_hold}', cur, flush=True)
        out.write(json.dumps({'iter': 0, 'params': cur, 'tune': best, 'hold': best_hold}, ensure_ascii=False) + '\n')
        for it in range(1, a.iters + 1):
            res = pool.map(_eval, [(propose(cur), tune, hold, a.samples) for _ in range(a.pop)])
            for params, sc, sh in res:
                out.write(json.dumps({'iter': it, 'params': params, 'tune': sc, 'hold': sh}, ensure_ascii=False) + '\n')
            params, sc, sh = max(res, key=lambda r: r[1])
            if sc >= best:
                cur, best, best_hold = params, sc, sh
            out.flush()
            print(f'{it:3} tune {best:.3f} hold {best_hold}', cur, flush=True)


if __name__ == '__main__':
    main()
