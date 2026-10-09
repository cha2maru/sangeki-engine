"""V（主人公の知識・残りのループ → 主人公が最終的に勝つ確率）の模型（ユーザーとの議論 2026-10-07、メモリ learning-design-knowledge-joseki）。

情報の価値と、ループを打ち切る価値を同じ単位（主人公の最終的な勝ち）で比べるため。既存の対戦表の記録（league_*.json）の
各試合の各ループの終わりの知識（truth[ループ]）と、そのループの結果・最終的な勝敗から学ぶ。枝分かれは要らない。

    cd play && python -m selfplay.v_model selfplay/runs/league_*.json --out selfplay/runs/v_model.txt
"""
import argparse
import glob
import json

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

KF = ('n_hyp', 'role_entropy', 'confirmed', 'confirmed_key_roles', 'combos', 'p_rules', 'culprits_fixed', 'nlog_p_truth', 'mean_true')


def rows(paths):
    out = []
    for f in paths:
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        script = d.get('script') or f
        for pair, v in d.get('pairs', {}).items():
            for gi, g in enumerate(v.get('games', [])):
                tr, lw, res = g.get('truth') or {}, g.get('loops_won') or {}, g.get('result') or {}
                if not isinstance(tr, dict) or not lw:
                    continue
                lw = {str(i + 1): x for i, x in enumerate(lw)} if isinstance(lw, list) else {str(k): x for k, x in lw.items()}
                loops = len(lw) if res.get('loop') == 'final' else None
                pc_win = res.get('winner') == 'protagonists'
                n = len(lw)
                for L in range(1, n + 1):  # ループ L の終わりの知識 → 主人公の最終的な勝ち
                    k = tr.get(str(L))
                    if not isinstance(k, dict) or str(L) not in lw:
                        continue
                    out.append({'game': f'{f}:{pair}:{gi}', 'script': script, 'pair': pair, 'loop': L,
                                'loops_total': loops or n, 'remaining': (loops or n) - L,
                                'won_loop_mm': int(bool(lw[str(L)][0] if isinstance(lw[str(L)], (list, tuple)) else lw[str(L)])), **{x: k.get(x) for x in KF}, 'y': int(pc_win)})
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--out', required=True)
    ap.add_argument('--split', default='game', choices=['game', 'script'], help='script: 脚本ごとに分ける（知らない脚本への汎化を測る）')
    a = ap.parse_args()
    paths = [p for x in a.paths for p in glob.glob(x)]
    df = rows(paths)
    df = df[df['won_loop_mm'] == 1]  # そのループを脚本家が取った後の局面（主人公が勝って終わったループの後には続きが無い）
    df['log_hyp'] = np.log10(df['n_hyp'].astype(float).clip(lower=1))
    feats = ['remaining', 'loop', 'log_hyp', 'role_entropy', 'confirmed', 'confirmed_key_roles', 'combos', 'p_rules', 'culprits_fixed']
    games = df['game'].unique()
    rng = np.random.default_rng(0)
    if a.split == 'script':
        ss = df['script'].unique()
        te = df['script'].isin(set(rng.choice(ss, size=max(1, len(ss) // 4), replace=False)))
    else:
        te = df['game'].isin(set(rng.choice(games, size=len(games) // 4, replace=False)))
    X, y = df[feats].astype(float), df['y']
    m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=40, verbose=-1)
    m.fit(X[~te], y[~te])
    p = m.predict_proba(X[te])[:, 1]
    lines = [f'行 {len(df)}・試合 {len(games)}・主人公の最終的な勝ちの割合 {y.mean():.3f}',
             f'確認用 AUC {roc_auc_score(y[te], p):.3f}',
             f'比較: 残りのループ数だけ AUC {roc_auc_score(y[te], X[te]["remaining"]):.3f}、役職の不確かさだけ AUC {roc_auc_score(y[te], -X[te]["role_entropy"]):.3f}',
             '重要度 ' + json.dumps(dict(sorted(zip(feats, map(int, m.feature_importances_)), key=lambda x: -x[1])), ensure_ascii=False)]
    # 1ビットの価値: 役職の不確かさを1ビット減らしたときの V の増え方（中央値、残りのループ別）
    for r in sorted(df['remaining'].unique()):
        sub = X[te & (df['remaining'] == r)]
        if len(sub) < 30:
            continue
        lo = sub.copy()
        lo['role_entropy'] = lo['role_entropy'] - 1
        d = m.predict_proba(lo)[:, 1] - m.predict_proba(sub)[:, 1]
        lines.append(f'残りのループ {r}: 役職の不確かさ1ビットの価値（V の増え方の中央値）{np.median(d):+.4f}・行 {len(sub)}')
    txt = '\n'.join(lines)
    print(txt)
    open(a.out, 'w', encoding='utf-8').write(txt + '\n')
    m.booster_.save_model(a.out.replace('.txt', '.lgb'))


if __name__ == '__main__':
    main()
