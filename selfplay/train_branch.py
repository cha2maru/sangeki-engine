"""段D: 同じ局面の候補どうしで、手の良し悪しを学ぶ（branch_moves.py のデータ）。

1局面（group）に、探索の上位4手と下位の1手がある。それぞれを複製した盤面で1日回した結果を持つ。
ここでの手の良さ（util）は、その日にループを取れば 1、取れなければ勝ち筋の評価の伸び × 0.2、から漏れ × lam を引いたもの。
問い:
1. 探索の評価値の順（rank）は、同じ局面の中で実際の良さの順と合っているか
   （局面ごとの順位相関の平均、最善とした手が実際にも最善だった率）
2. LightGBM の lambdarank（局面を群にする）で順位付けすると、探索の評価値より当たるか（確認用の局面で比べる）

    cd play && python -m selfplay.train_branch selfplay/runs/branch_*.jsonl [--lam 0.05]
"""
import argparse
import json

import numpy as np
import pandas as pd


def load(paths, lam, label='day'):
    rows = []
    for p in paths:
        for l in open(p, encoding='utf-8'):
            r = json.loads(l)
            if r.get('group') is None:
                continue
            if label == 'loop':  # ループの終わりまで回した結果（branch_moves --horizon loop）
                if 'won_loop' not in r:
                    continue
                util = float(r['won_loop'])
            else:
                util = 1.0 if r['won'] else 0.2 * (r['dscore'] or 0.0)
            r['util'] = util - lam * r['leak']
            flat = {k: v for k, v in r.items() if k not in ('feat', 'sfeat', 'kfeat', 'cards')}
            flat.update({f'm_{k}': v for k, v in r['feat'].items()})
            flat.update({f's_{k}': v for k, v in r['sfeat'].items()})
            flat.update({f'k_{k}': v for k, v in r.get('kfeat', {}).items()})  # 主人公に割れている情報
            rows.append(flat)
    df = pd.DataFrame(rows)
    return df[df.groupby('group')['group'].transform('size') >= 3]


def group_stats(df, score_col):
    from scipy.stats import spearmanr
    rhos, top = [], []
    for _, g in df.groupby('group'):
        if g.util.nunique() < 2:
            continue
        rho = spearmanr(g[score_col], g.util).correlation
        if not np.isnan(rho):
            rhos.append(rho)
        top.append(float(g.util.iloc[int(np.argmax(g[score_col].values))] >= g.util.max() - 1e-9))
    return (np.mean(rhos) if rhos else np.nan), (np.mean(top) if top else np.nan), len(top)


def main():
    import lightgbm as lgb
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--lam', type=float, default=0.05)
    ap.add_argument('--label', default='day', choices=['day', 'loop'], help='loop: そのループを取ったか（won_loop）を手の良さにする')
    a = ap.parse_args()
    df = load(a.paths, a.lam, a.label)
    print(f'手 {len(df)}、局面 {df.group.nunique()}、脚本 {df.script.nunique()}')
    print(df.groupby(np.where(df['rank'] < 4, df['rank'].astype(str), 'low'))[[c for c in ('won', 'won_loop', 'dscore', 'leak', 'util') if c in df.columns]].mean().round(3).to_string())
    rho, top, n = group_stats(df, 'val')
    print(f'探索の評価値: 局面ごとの順位相関 {rho:.3f}、最善とした手が実際にも最善 {top:.2f}（差のある局面 {n}）')
    feats = [c for c in df.columns if c.startswith(('m_', 's_', 'k_'))] + ['val', 'val_reply', 'val_mean']
    groups = df.group.unique()
    rng = np.random.default_rng(0)
    test = set(rng.choice(groups, size=len(groups) // 4, replace=False))
    tr, te = df[~df.group.isin(test)].sort_values('group'), df[df.group.isin(test)].copy()
    # lambdarank のラベルは局面の中の順位（0〜4、良いほど大きい）
    tr = tr.assign(rel=tr.groupby('group')['util'].rank(method='dense').astype(int) - 1)
    m = lgb.LGBMRanker(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=10, verbose=-1)
    m.fit(tr[feats], tr.rel, group=tr.groupby('group', sort=False).size().loc[tr.group.unique()].values)
    te['pred'] = m.predict(te[feats])
    r1, t1, n1 = group_stats(te, 'val')
    r2, t2, n2 = group_stats(te, 'pred')
    print(f'確認用の局面（{n1}）: 探索の評価値 順位相関 {r1:.3f}・最善一致 {t1:.2f} ／ LightGBM 順位相関 {r2:.3f}・最善一致 {t2:.2f}')
    imp = pd.Series(m.booster_.feature_importance('gain'), index=feats).sort_values(ascending=False)
    print('重要度（上位10）:', {k: round(v) for k, v in imp.head(10).items()})


if __name__ == '__main__':
    main()
