"""脚本家の手の良し悪しを、実際の結果で学ぶ（段D。ユーザーの学習目標「脚本家の手の良し悪し」）。

データ: `searchexp`（上位8手から3割で無作為に選ぶ先読みする脚本家）の対戦表。1行 = 脚本家の伏せ札の1手。
結果（ラベル）は、その日の終わりまでに実際に起きたこと:
- won_today: その日にループを取った（キーパーソンの死・主人公の死・最終日の敗北など。ループ終了時の判定は最終日の手に付ける）
- dprog: 翌日の始めの勝ち筋の評価（players.score）− この手の前の評価（ループが終わった日は除く）
- leak: 公開情報の推理の、配役のエントロピーの減り（ビット）
問い:
1. 探索の評価値（val）は実際の結果を当てているか（手の良し悪しの物差しとして妥当か）
2. 局面と手の特徴量から LightGBM で当てると、探索の評価値より当たるか
3. 探索が最善とした手（rank 0）と、無作為に選んだ下位の手で、結果はどれだけ違うか

    cd play && python -m selfplay.train_moves selfplay/runs/league_exp_*_s1001.json
"""
import json
import sys

import numpy as np
import pandas as pd


def rows(paths):
    out = []
    for p in paths:
        d = json.load(open(p))
        for pair, v in d['pairs'].items():
            for g in v['games']:
                days = {}
                for m in g.get('mm_move_log', []):
                    days[(m['loop'], m['day'])] = m
                for m in g.get('mm_move_log', []):
                    lw = g['loops_won'].get(str(m['loop']))
                    won_today = int(bool(lw) and bool(lw[0]) and lw[1] == m['day'])
                    r = {'script': d['script'], 'game': f"{d['script']}:{g['game']}", 'loop': m['loop'], 'day': m['day'],
                         'rank': m['rank'], 'val': m['val'], 'val_gap': m['val_best'] - m['val'], 'won_today': won_today,
                         'dprog': (m['score_after'] - m['score_before']) if 'score_after' in m else np.nan,
                         'leak': (m['ent_before'] - m['ent_after']) if 'ent_after' in m else np.nan}
                    r.update({f'm_{k}': v for k, v in m['feat'].items()})
                    r.update({f's_{k}': v for k, v in m['sfeat'].items()})
                    out.append(r)
    return pd.DataFrame(out)


def main(paths):
    import lightgbm as lgb
    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score
    df = rows(paths)
    df['script'] = df['script'].astype('category')
    print(f'手 {len(df)}（試合 {df.game.nunique()}、脚本 {df.script.nunique()}）、その日にループを取った率 {df.won_today.mean():.3f}')
    # 1. 探索の評価値と結果
    if df.won_today.nunique() > 1:
        print('val で won_today を当てる AUC', round(roc_auc_score(df.won_today, df.val), 3))
    ok = df.dprog.notna()
    print('val と dprog の順位相関', round(spearmanr(df.val[ok], df.dprog[ok]).correlation, 3))
    # 3. 最善手と下位の手の結果の差（探索で選ばれた手の効果）
    g = df.groupby(df['rank'] > 0)[['won_today', 'dprog', 'leak']].mean().round(3)
    g.index = ['最善手（rank 0）', '下位の手（無作為）']
    print(g.to_string())
    # 2. LightGBM（試合単位で分ける）
    feats = [c for c in df.columns if c.startswith(('m_', 's_'))] + ['val', 'script', 'loop', 'day']
    games = df.game.unique()
    rng = np.random.default_rng(0)
    test = set(rng.choice(games, size=len(games) // 4, replace=False))
    tr, te = df[~df.game.isin(test)], df[df.game.isin(test)]
    clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=20, verbose=-1)
    clf.fit(tr[feats], tr.won_today)
    if te.won_today.nunique() > 1:
        print('LightGBM で won_today を当てる AUC（確認用）', round(roc_auc_score(te.won_today, clf.predict_proba(te[feats])[:, 1]), 3),
              '／ val だけ', round(roc_auc_score(te.won_today, te.val), 3))
    imp = pd.Series(clf.booster_.feature_importance('gain'), index=feats).sort_values(ascending=False)
    print('重要度（上位10）:', {k: round(v) for k, v in imp.head(10).items()})
    trd, ted = tr[tr.dprog.notna()], te[te.dprog.notna()]
    reg = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=20, verbose=-1)
    reg.fit(trd[feats], trd.dprog)
    print('dprog の順位相関（確認用） LightGBM', round(spearmanr(reg.predict(ted[feats]), ted.dprog).correlation, 3),
          '／ val だけ', round(spearmanr(ted.val, ted.dprog).correlation, 3))


if __name__ == '__main__':
    main(sys.argv[1:])
