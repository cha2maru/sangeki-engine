"""脚本の自動生成（plan/engine-smarter.md 段S4）。脚本家の書の6段階（wiki/concepts/script-creation.md）を確率的に行う。

1 ルールを選ぶ（Y1・X2） 2 登場人物を選ぶ（7〜10人。情報を大きく出す友好能力の持ち主を2人以上＝p43 の勧め）
3 役職を配役する（ルールの員数どおり。キャラクターカードの制約は scripts.check が見る） 4 日数（5〜7） 5 事件と犯人（3〜5件、犯人は互いに異なる）
6 ループ数（p48 の目安を丸め、3〜5）
生成した脚本は scripts.check の誤りが無いものだけを返す。遊べるか（盤面で勝つ手があるか・易しすぎないか）は自己対戦で選ぶ（selfplay/gen_filter.py）。

    cd play && python -m engine.generate --n 5 --seed 1
"""
import argparse
import json
import random

from .deduce import RULES, roles_for
from .resolve import CHARS
from .scripts import check, loops_estimate

INCIDENTS = ('MURDER', 'SPREAD', 'CORRUPT', 'SUICIDE', 'HOSPITAL', 'REMOTE', 'MISSING', 'RUMOR_SPREAD', 'BUTTERFLY')
# 情報を大きく出す友好能力（役職・犯人・ルールを知る）を持つキャラクター
INFO = {'C04', 'C05', 'C06', 'C07', 'C13', 'C16', 'C21', 'C23', 'C29'}
# ponytail: 配役の決まり方が特別な4人（イレギュラー・コピーキャット・アルバイト/アルバイト？）と上位存在は生成に使わない。
# 使うなら scripts.check の制約に合わせた配役の手順を足す
SKIP = {'C11', 'C28', 'C32', 'C33', 'C35'}


def all_combos():
    """ルールの組（Y 1つ＋X 2つ）の全て（5×21＝105 通り）。"""
    from itertools import combinations
    ys = [r for r, v in RULES.items() if v['type'] == 'Y']
    xs = [r for r, v in RULES.items() if v['type'] == 'X']
    return [(y, *x) for y in ys for x in combinations(xs, 2)]


def generate(rng, n_chars=None, rules=None):
    """rules: ルールの組を指定する（[Y, X, X]）。省略すれば無作為。"""
    ys = [r for r, v in RULES.items() if v['type'] == 'Y']
    xs = [r for r, v in RULES.items() if v['type'] == 'X']
    rules = list(rules) if rules else [rng.choice(ys)] + rng.sample(xs, 2)
    pool = [c for c, v in CHARS.items() if v.get('engine') and c not in SKIP]
    n = n_chars or rng.randint(7, 10)
    for _ in range(200):
        chars = sorted(rng.sample(pool, n))
        if len(INFO & set(chars)) >= 2:
            break
    need = roles_for(tuple(rules))
    slots = [r for r, k in need.items() for _ in range(k)]
    if len(slots) > len(chars):
        return None
    if 'Y_CONTRACT' in rules:  # 【強制：脚本作成時】必ず少女がキーパーソン
        girls = [c for c in chars if '少女' in CHARS[c]['tags']]
        if not girls:
            return None
        key = rng.choice(girls)
        others = [c for c in chars if c != key]
        slots.remove('KEY')
        roles = {key: 'KEY', **dict(zip(rng.sample(others, len(slots)), slots))}
    else:
        roles = dict(zip(rng.sample(chars, len(slots)), slots))
    if 'C22' in chars and 'C22' not in roles:  # A.I. はパーソンにできない
        return None
    days = rng.randint(5, 7)
    k = rng.randint(3, min(5, days - 1))  # 事件は2日目以降に1日1件
    inc_days = sorted(rng.sample(range(2, days + 1), k))
    culprits = rng.sample(chars, k)
    incidents = [{'day': d, 'id': rng.choice(INCIDENTS), 'culprit': c} for d, c in zip(inc_days, culprits)]
    sc = {'id': None, 'title': '自動生成', 'set': 'BTX', 'loops': 4, 'days': days, 'rules': rules, 'roles': roles,
          'characters': chars, 'incidents': incidents, 'use': 'generated'}
    if 'C13' in chars:
        # ループ1からの登場ではギミックにならない（ユーザー、wiki/concepts/good-script.md 8）。ループ数は後で決まるので2に固定
        sc['appear'] = {'C13': {'loop': 2}}
    if 'C24' in chars:
        sc.setdefault('appear', {})['C24'] = {'day': rng.randint(2, 3)}
    if 'C16' in chars:
        sc['territory'] = rng.choice(['HOS', 'SHR', 'CIT', 'SCH'])
    if 'C34' in chars:
        sc['start'] = {'C34': rng.choice(['CIT', 'SCH'])}
    sc['loops'] = max(3, min(5, round(loops_estimate(sc))))
    err, _ = check(sc)
    return None if err else sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=5)
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out = []
    while len(out) < a.n:
        sc = generate(rng)
        if sc:
            sc['id'] = f'gen_s{a.seed}_{len(out):03d}'
            out.append(sc)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
