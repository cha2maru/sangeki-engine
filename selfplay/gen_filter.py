"""生成した脚本を自己対戦でふるいにかける（plan/engine-smarter.md 段S4）。

採る条件（脚本家の書のタブーと、易しすぎないことの目安）:
- 盤面で勝つ手がある: 脚本を知る主人公（oread）が、先読みする脚本家（search）相手に games 戦中 min_oracle 勝以上
- 易しすぎない: 推理する主人公（read）の勝ちが max_read 以下
採った脚本は engine/scripts/generated/<id>.json に、成績（stats）を付けて書く。

    cd play && python -m selfplay.gen_filter --n 30 --seed 1 --games 12
    # 目隠しの補充（Claude が対AIモードで遊ぶ脚本）: ルールの組をそろえ、ルールを表示しない
    cd play && python -m selfplay.gen_filter --n 40 --seed 31 --balanced --blind
"""
import argparse
import json
import multiprocessing
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.generate import all_combos, generate  # noqa: E402
from engine.scripts import glob_scripts, script_dirs, to_game  # noqa: E402
from selfplay.league import one_game  # noqa: E402


def run(args):
    sc, pair, seed, g = args
    mm, pc = pair.split(':')
    try:
        r = one_game(to_game(sc), mm, pc, seed, g)
        return sc['id'], pair, r['result']['winner'] == 'protagonists', r['result'].get('loop'), None
    except Exception as e:  # 生成した脚本でエンジンが落ちたら、その脚本を捨てて理由を残す
        return sc['id'], pair, False, None, repr(e)[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=30)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--games', type=int, default=12)
    ap.add_argument('--min_oracle', type=int, default=3)
    ap.add_argument('--max_read', type=int, default=9)
    ap.add_argument('--procs', type=int, default=6)
    ap.add_argument('--all_out', default=None, help='全脚本と成績を1行ずつ書く jsonl（難しさの学習用）')
    ap.add_argument('--balanced', action='store_true', help='ルールの組（105通り）を、在庫に少ない組から順に使う')
    ap.add_argument('--blind', action='store_true', help='ルール・配役を表示しない（Claude が目隠しで遊ぶ脚本の補充）')
    a = ap.parse_args()
    rng = random.Random(a.seed)
    scs = []
    order = []
    if a.balanced:  # 在庫（generated/）に少ない組から。同じ本数なら無作為の順
        import glob
        from collections import Counter
        have = Counter(tuple(sorted(json.load(open(f, encoding='utf-8'))['rules']))
                       for f in glob_scripts('generated/*.json'))
        combos = all_combos()
        rng.shuffle(combos)
        order = sorted(combos, key=lambda c: have[tuple(sorted(c))])
    tries = 0
    while len(scs) < a.n and tries < a.n * 50:
        tries += 1
        sc = generate(rng, rules=order[len(scs) % len(order)] if order else None)
        if sc:
            sc['id'] = f'gen_{"b" if a.blind else "s"}{a.seed}_{len(scs):03d}'
            scs.append(sc)
    jobs = [(sc, pair, a.seed, g) for sc in scs for pair in ('search:oread', 'search:read') for g in range(a.games)]
    with multiprocessing.Pool(a.procs) as pool:
        out = pool.map(run, jobs)
    stats = {sc['id']: {'search:oread': 0, 'search:read': 0, 'errors': []} for sc in scs}
    for sid, pair, win, loop, err in out:
        stats[sid][pair] += int(win)
        if err:
            stats[sid]['errors'].append(err)
    outdir = os.path.join(script_dirs()[-1], 'generated')  # 最後の置き場所（公開版では sangeki-scripts）に書く
    os.makedirs(outdir, exist_ok=True)
    kept = 0
    for sc in scs:
        st = stats[sc['id']]
        ok = not st['errors'] and st['search:oread'] >= a.min_oracle and st['search:read'] <= a.max_read
        if a.blind:  # ルール・配役は出さない（id と採否だけ）
            print(sc['id'], 'KEEP' if ok else 'drop', '（エラー）' if st['errors'] else '')
        else:
            print(sc['id'], 'KEEP' if ok else 'drop', st['search:oread'], st['search:read'], sc['rules'], len(sc['characters']),
                  st['errors'][:1])
        if ok:
            sc['stats'] = {'games': a.games, 'oread_wins_vs_search': st['search:oread'], 'read_wins_vs_search': st['search:read']}
            json.dump(sc, open(os.path.join(outdir, sc['id'] + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            kept += 1
    print(f'採用 {kept}/{len(scs)}')
    if a.all_out:
        with open(a.all_out, 'a', encoding='utf-8') as f:
            for sc in scs:
                st = stats[sc['id']]
                f.write(json.dumps(dict(sc, stats={'games': a.games, 'oread_wins_vs_search': st['search:oread'],
                                                   'read_wins_vs_search': st['search:read'], 'errors': st['errors']}),
                                   ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
