"""脚本の難易度と、プレイヤーの強さを測るのに使えるかの分類（ユーザー「脚本自体が弱いと脚本家の能力も主人公の能力も測れない」）。

これまでの対戦表（selfplay/runs/league_*.json）のうち、今の世代の組（脚本家 search*・主人公 read*）の試合を脚本ごとに集める。
- pc_win: 主人公の勝ちの割合 / l1: ループ1で主人公が勝った割合 / final: 最後の戦いまで行った割合
- sens（差の出やすさ）: 同じ対戦表・同じ種で、組を変えたら勝ち負けが入れ替わった試合の割合（組の対ごとの平均）。
  強い版と弱い版で結果が変わる脚本ほど大きい＝プレイヤーの強さを測れる。

    cd play && python -m selfplay.script_difficulty
"""
import glob
import json
import os
from collections import defaultdict
from itertools import combinations

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runs')


def current(pair):
    mm, _, pc = pair.partition(':')
    return mm.startswith('search') and pc.startswith('read')


def collect():
    agg = defaultdict(lambda: {'n': 0, 'pc': 0, 'l1': 0, 'final': 0, 'flip': 0, 'flip_n': 0})
    for f in glob.glob(os.path.join(RUNS, 'league_*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue
        sc = str(d.get('script', '?')).split('/')[-1]
        pairs = {p: v for p, v in (d.get('pairs') or {}).items() if current(p)}
        a = agg[sc]
        res = {}
        for p, v in pairs.items():
            res[p] = {}
            for g in v.get('games', []):
                r = g.get('result') or {}
                win = r.get('winner') == 'protagonists'
                a['n'] += 1
                a['pc'] += win
                a['l1'] += win and r.get('loop') == 1
                a['final'] += r.get('loop') == 'final'
                res[p][g['game']] = win
        for p, q in combinations(res, 2):  # 同じ種の試合で、組を変えたら勝ち負けが変わったか
            common = res[p].keys() & res[q].keys()
            a['flip'] += sum(res[p][g] != res[q][g] for g in common)
            a['flip_n'] += len(common)
    return agg


def classify(r):
    if r['n'] < 30:
        return '試合が少ない'
    if r['pc_win'] < 0.15:
        return '脚本家有利すぎ'
    if r['pc_win'] > 0.85 or r['l1'] > 0.5:
        return '主人公有利すぎ'
    if r['sens'] is not None and r['sens'] < 0.1:
        return '差が出にくい'
    return '測定向き'


def table():
    out = []
    for sc, a in collect().items():
        if not a['n']:
            continue
        r = {'script': sc, 'n': a['n'], 'pc_win': a['pc'] / a['n'], 'l1': a['l1'] / a['n'], 'final': a['final'] / a['n'],
             'sens': a['flip'] / a['flip_n'] if a['flip_n'] else None}
        r['class'] = classify(r)
        out.append(r)
    return sorted(out, key=lambda r: (r['class'], -r['n']))


if __name__ == '__main__':
    for r in table():
        sens = '  —  ' if r['sens'] is None else f"{r['sens']:.2f}"
        print(f"{r['script']:16} n={r['n']:5} 主人公の勝ち={r['pc_win']:.2f} ループ1={r['l1']:.2f} 最後の戦い={r['final']:.2f} 差の出やすさ={sens}  {r['class']}")
