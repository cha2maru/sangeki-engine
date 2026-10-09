"""学習（LightGBM）の最初の1周。脚本家の札1枚ごとの行（selfplay/export.py の CSV）から、
「このループを脚本家が取るか」（mm_loop_win）を予測する。試合単位で学習用と確認用に分ける（同じ試合の行が両方に入らない）。

    /workspaces/sangeki/.venv/bin/python -m selfplay.train selfplay/runs/<名前>/mm_cards.csv   （play/ で実行）
"""
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


def main(path, label='mm_loop_win'):
    df = pd.read_csv(path)
    y = df[label].astype(int)
    drop = {'game', 'mm_win', 'mm_loop_win', 'target', 'board_int_HOS_SHR_CIT_SCH'}
    X = df[[c for c in df.columns if c not in drop]].copy()
    for c in ('card', 'target_role', 'target_area'):
        X[c] = X[c].astype('category')
    X = X.apply(lambda s: pd.to_numeric(s, errors='coerce') if s.dtype == object else s)
    games = df['game'].unique()
    rng = np.random.default_rng(0)
    test_games = set(rng.choice(games, size=max(1, len(games) // 4), replace=False))
    te = df['game'].isin(test_games)
    model = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15, min_child_samples=20, verbose=-1)
    model.fit(X[~te], y[~te])
    p = model.predict_proba(X[te])[:, 1]
    print(f'行 {len(df)}（学習 {int((~te).sum())} / 確認 {int(te.sum())}）、試合 {len(games)}、ラベル {label} の正例率 {y.mean():.2f}')
    print(f'確認用の AUC: {roc_auc_score(y[te], p):.3f}（0.5=当てずっぽう、1.0=完全）')
    imp = sorted(zip(model.booster_.feature_importance('gain'), X.columns), reverse=True)[:12]
    print('重要な特徴量（gain 上位）:')
    for g, c in imp:
        print(f'  {c:<22} {g:.0f}')


if __name__ == '__main__':
    main(*sys.argv[1:])
