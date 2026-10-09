"""脚本の難しさを脚本の中身から当てる（段D・段S4。ユーザーの目標「シナリオの自動生成」）。

データ: selfplay/gen_filter.py --all_out の jsonl（1行＝生成した脚本1本と、先読みする脚本家相手の成績）。
目的変数:
- oread: 脚本を知る主人公の勝率（盤面で勝つ手があるか＝タブー①の目安）
- read: 推理する主人公の勝率（実際の難しさ）
- gap: oread − read（推理の難しさ＝役職を割る手立ての乏しさ。タブー②の目安）
特徴量は脚本だけから作る（ルール・配役の人数・日数・ループ数・事件の種類・情報源の数・犯人の不安臨界など）。
学習できれば、生成器は自己対戦をせずに候補を選べる。

    cd play && python -m selfplay.train_difficulty selfplay/runs/gen_all.jsonl
"""
import json
import sys

import numpy as np
import pandas as pd

from engine.generate import INCIDENTS, INFO
from engine.resolve import CHARS
from engine.scripts import loops_estimate

RULE_IDS = ('Y_MURDER', 'Y_SEAL', 'Y_CONTRACT', 'Y_FUTURE', 'Y_BOMB', 'X_CIRCLE', 'X_LOVE', 'X_KILLER', 'X_RUMOR', 'X_VIRUS',
            'X_THREAD', 'X_FACTOR')
REFUSERS = {'KILLER', 'KUROMAKU', 'FACTOR', 'CULTIST', 'WITCH'}


def features(sc):
    f = {f'rule_{r}': int(r in sc['rules']) for r in RULE_IDS}
    f.update({f'inc_{i}': sum(x['id'] == i for x in sc['incidents']) for i in INCIDENTS})
    chars = sc['characters']
    f['n_chars'] = len(chars)
    f['n_roles'] = len(sc['roles'])
    f['days'] = sc['days']
    f['loops'] = sc['loops']
    f['loops_estimate'] = loops_estimate(sc)
    f['loops_margin'] = sc['loops'] - loops_estimate(sc)
    f['n_incidents'] = len(sc['incidents'])
    f['n_info_chars'] = len(INFO & set(chars))
    f['n_info_refusers'] = sum(1 for c in INFO & set(chars) if sc['roles'].get(c) in REFUSERS)  # 情報源が友好無視（難しくする配役）
    f['culprit_limit_mean'] = np.mean([CHARS[x['culprit']]['limit'] for x in sc['incidents']])
    f['culprit_is_role'] = sum(1 for x in sc['incidents'] if x['culprit'] in sc['roles'])
    f['first_incident_day'] = min(x['day'] for x in sc['incidents'])
    f['girls'] = sum('少女' in CHARS[c]['tags'] for c in chars)
    return f


def main(path):
    import lightgbm as lgb
    from sklearn.model_selection import KFold
    rows = [json.loads(l) for l in open(path, encoding='utf-8')]
    rows = [r for r in rows if not r['stats'].get('errors')]
    X = pd.DataFrame([features(r) for r in rows])
    g = rows[0]['stats']['games']
    Y = pd.DataFrame({'oread': [r['stats']['oread_wins_vs_search'] / g for r in rows],
                      'read': [r['stats']['read_wins_vs_search'] / g for r in rows]})
    Y['gap'] = Y.oread - Y.read
    print(f'脚本 {len(rows)} 本（1本 {g} 戦）。平均 oread {Y.oread.mean():.2f}・read {Y.read.mean():.2f}・gap {Y.gap.mean():.2f}')
    for target in ('oread', 'read', 'gap'):
        pred = np.zeros(len(X))
        for tr, te in KFold(5, shuffle=True, random_state=0).split(X):
            m = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=8, verbose=-1)
            m.fit(X.iloc[tr], Y[target].iloc[tr])
            pred[te] = m.predict(X.iloc[te])
        mae = np.abs(pred - Y[target]).mean()
        base = np.abs(Y[target] - Y[target].mean()).mean()
        corr = np.corrcoef(pred, Y[target])[0, 1]
        m = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=8, verbose=-1).fit(X, Y[target])
        imp = pd.Series(m.booster_.feature_importance('gain'), index=X.columns).sort_values(ascending=False)
        print(f'{target}: 5分割の平均絶対誤差 {mae:.3f}（平均で当てた場合 {base:.3f}）、相関 {corr:.2f}。重要度の上位:',
              {k: round(v) for k, v in imp.head(6).items()})


if __name__ == '__main__':
    main(sys.argv[1])
