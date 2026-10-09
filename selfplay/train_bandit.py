"""段D（文脈付きバンディット）: ループごとに無作為に選んだ λ と、そのループの結果から、
「この局面でこの λ を使うと、ループを取れる確率と漏れる情報はどうなるか」を LightGBM で学ぶ。

    cd play && /workspaces/sangeki/.venv/bin/python -m selfplay.train_bandit selfplay/runs/league_bandit_*_s303.json

1行 = 1試合の1ループ。特徴量 = ループ開始時の局面（engine/features.state_features）＋公開情報の推理のエントロピー＋λ＋脚本。
ラベル:
- won: そのループを脚本家が取った
- leak: そのループで減った配役のエントロピー（ループ開始時の脚本家側の推理 → ループ終了時の理想の観測者）
試合単位で分けて確認用を作る（同じ試合のループを学習と確認にまたがせない）。
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
                for rec in g.get('mm_loop_log', []):
                    lp = str(rec['loop'])
                    if lp not in g['truth'] or lp not in g['loops_won']:
                        continue
                    r = {k: v for k, v in rec.items() if k != 'loop'}
                    r.update(script=d['script'], game=f"{d['script']}:{g['game']}", loop=rec['loop'],
                             won=int(bool(g['loops_won'][lp][0])),
                             leak=rec['ent_start'] - g['truth'][lp]['role_entropy'])
                    out.append(r)
    return pd.DataFrame(out)


def main(paths):
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score, mean_absolute_error
    df = rows(paths)
    df['script'] = df['script'].astype('category')
    feats = [c for c in df.columns if c.startswith('f_')] + ['ent_start', 'n_hyp', 'lam', 'loop', 'script']
    print(f'行 {len(df)}（試合 {df.game.nunique()}）、ループを取った率 {df.won.mean():.2f}')
    print(df.groupby('lam')[['won', 'leak']].mean().round(3).to_string())
    games = df.game.unique()
    rng = np.random.default_rng(0)
    test = set(rng.choice(games, size=len(games) // 4, replace=False))
    tr, te = df[~df.game.isin(test)], df[df.game.isin(test)]
    clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=20, verbose=-1)
    clf.fit(tr[feats], tr.won)
    reg = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=20, verbose=-1)
    reg.fit(tr[feats], tr.leak)
    if te.won.nunique() > 1:
        print('won の AUC（確認用）', round(roc_auc_score(te.won, clf.predict_proba(te[feats])[:, 1]), 3))
    print('leak の平均絶対誤差（確認用）', round(mean_absolute_error(te.leak, reg.predict(te[feats])), 3), '／ 平均で当てた場合',
          round(mean_absolute_error(te.leak, np.full(len(te), tr.leak.mean())), 3))
    imp = pd.Series(clf.booster_.feature_importance('gain'), index=feats).sort_values(ascending=False)
    print('won の重要度（上位10）:', imp.head(10).round(0).to_dict())
    # 方策: 確認用の各局面で λ を差し替えて予測し、λ ごとの予測の平均と、最良の λ の分布を出す
    lams = sorted(df.lam.unique())
    pw = np.stack([clf.predict_proba(te[feats].assign(lam=l))[:, 1] for l in lams], axis=1)
    pl = np.stack([reg.predict(te[feats].assign(lam=l)) for l in lams], axis=1)
    print('λ ごとの予測（確認用の局面で差し替え）: P(ループを取る)', dict(zip(lams, pw.mean(0).round(3))),
          '漏れ', dict(zip(lams, pl.mean(0).round(2))))
    for mu in (0.0, 0.02, 0.05):
        best = np.argmax(pw - mu * pl, axis=1)
        print(f'  効用 = P(取る) − {mu}×漏れ を最大にする λ の分布:', dict(zip(lams, np.bincount(best, minlength=len(lams)))))


if __name__ == '__main__':
    main(sys.argv[1:])
