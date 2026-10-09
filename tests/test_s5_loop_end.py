"""段S5 1〜3 の境界例（play/tests/cases/s5_loop_end.json。エンジンのコードを見ない担当が一次資料から先に書いた期待値）:
ループ終了時の敗北（封印・契約・未来改変・時限爆弾・フレンド）、フレンドのループ開始時の友好、不穏な噂。"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine import phases as ph  # noqa: E402
from engine.resolve import IllegalPlacement  # noqa: E402

CASES = [c for c in json.load(open(os.path.join(HERE, 'cases', 's5_loop_end.json'), encoding='utf-8')) if 'id' in c]


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_s5(case):
    if case.get('status') == 'unresolved':
        pytest.xfail('一次資料で決まらない: ' + case.get('unresolved', case['desc']))
    s = copy.deepcopy(case['state'])
    exp = case['expect']
    if case['phase'] == 'loop_end':
        reasons = ph.loop_end_loss(s)
        assert bool(reasons) == exp['loss'], reasons
        assert set(reasons) == set(exp['reasons'])
        for c, r in exp.get('reveal', {}).items():
            assert s.get('revealed_roles', {}).get(c) == r, (c, s.get('revealed_roles'))
    elif case['phase'] == 'loop_start':
        s2, _ = ph.loop_start(s, ph.default_init(s['chars']))
        for k in ('loop', 'day'):
            if k in exp:
                assert s2[k] == exp[k]
        for cid, want in exp.get('chars', {}).items():
            for k, v in want.items():
                assert s2['chars'][cid][k] == v, (cid, k, s2['chars'][cid][k], v)
        if 'used_loop' in exp:
            assert sorted(s2['ability_used_loop']) == sorted(exp['used_loop'])
    elif case['phase'] == 'mm_ability':
        before = copy.deepcopy(s)
        if 'error' in exp:
            with pytest.raises(IllegalPlacement):
                ph.mm_phase(s, case['choice']['uses'])
            assert s == before, '誤った宣言で状態が変わった'
            return
        ph.mm_phase(s, case['choice']['uses'])
        if 'boards' in exp:
            assert s['boards'] == exp['boards']
        if 'used_loop' in exp:
            assert sorted(s['ability_used_loop']) == sorted(exp['used_loop'])
    else:
        raise AssertionError(case['phase'])
