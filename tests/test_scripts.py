"""脚本の整合の確認（engine/scripts.py）: 正しい脚本は通り、ルール上ありえない脚本は誤りになる。"""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine import scripts  # noqa: E402

_P = scripts.find('toukou_001.json')
pytestmark = pytest.mark.skipif(_P is None, reason='toukou_001（他の方の投稿シナリオ）は公開版に無い。変えて確かめる元の脚本に使っている')
BASE = scripts.load(_P) if _P else None


def errs(**change):
    sc = copy.deepcopy(BASE)
    sc.update(change)
    return scripts.check(sc)[0]


def test_all_scripts_are_consistent():
    for sc in scripts.load_all():
        assert scripts.check(sc)[0] == [], sc['id']


def test_toukou001_is_playable_and_estimate():
    assert scripts.unsupported(BASE) == []
    # 殺人計画1.8 + 因果の糸0.5 + ウイルス0 + 事件4つ(±0) + 8日(±0) = 2.3（実際は3ループ）
    assert scripts.loops_estimate(BASE) == 2.3


def test_violations():
    assert errs(rules=['Y_MURDER', 'X_THREAD', 'X_THREAD'])
    assert errs(roles={'C01': 'KUROMAKU', 'C03': 'KEY', 'C08': 'KILLER'})  # ミスリーダーが足りない
    assert errs(incidents=BASE['incidents'] + [{'day': 8, 'id': 'SUICIDE', 'culprit': 'C08'}])  # 犯人の重複
    assert errs(rules=['Y_CONTRACT', 'X_THREAD', 'X_VIRUS'], roles={'C06': 'KEY', 'C05': 'MISLEADER'})  # 少女でない
    assert not errs(rules=['Y_CONTRACT', 'X_THREAD', 'X_VIRUS'], roles={'C03': 'KEY', 'C05': 'MISLEADER'})
    assert errs(characters=BASE['characters'] + ['C24'])  # 転校生の登場日が無い


def test_generated_scripts_are_consistent():
    """生成器の出す脚本は、どれも脚本の検査を通る（engine/generate.py）。採用済みの生成脚本も同じ。"""
    import glob
    import json
    import random
    from engine.generate import generate
    rng = random.Random(5)
    n = 0
    while n < 30:
        sc = generate(rng)
        if sc:
            assert scripts.check(sc)[0] == [], sc
            n += 1
    for p in glob.glob(os.path.join(os.path.dirname(scripts.__file__), 'scripts', 'generated', '*.json')):
        sc = json.load(open(p, encoding='utf-8'))
        assert scripts.check(sc)[0] == [], p


def test_user_code_roundtrip_and_bad_input():
    """シナリオエディタのコード: 作って読めば同じ脚本、壊れた・形の違う入力は ValueError か誤りの一覧。"""
    import pytest
    from engine import scripts as S
    sc = {'title': 't', 'loops': 3, 'days': 5, 'rules': ['Y_MURDER', 'X_CIRCLE', 'X_LOVE'],
          'characters': ['C01', 'C02', 'C03', 'C04', 'C05', 'C06'], 'roles': {'C01': 'KEY', 'C02': 'KILLER', 'C03': 'KUROMAKU'},
          'incidents': [{'day': 2, 'id': 'MURDER', 'culprit': 'C04'}]}
    d = S.decode(S.encode(sc))
    assert d['rules'] == sc['rules'] and d['roles'] == sc['roles'] and d['incidents'] == sc['incidents']
    for bad in ('x', 's1.AAAA', 's1.' + '!' * 10):
        with pytest.raises(ValueError):
            S.decode(bad)
    assert S.report({'loops': 3})['errors'] and S.report(dict(sc, roles={}))['errors']
