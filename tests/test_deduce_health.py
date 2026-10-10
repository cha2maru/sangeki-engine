"""推理の健全性の記録（Deduction.WATCH・truth_lost_by・empty_by）。対戦表がこれで推理の不具合を数える。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.deduce import Deduction, roles_for  # noqa: E402

CHARS = [f'C0{i}' for i in range(1, 10)]


def truth():
    rules = ('Y_MURDER', 'X_CIRCLE', 'X_LOVE')
    roles, it = {}, iter(CHARS)
    for r, n in roles_for(rules).items():
        for _ in range(n):
            roles[next(it)] = r
    return {'rules': list(rules), 'roles': roles}


def test_truth_lost_and_empty_are_recorded():
    sc = truth()
    key = next(c for c, r in sc['roles'].items() if r == 'KEY')
    Deduction.WATCH = sc
    try:
        d = Deduction(CHARS)
        d.observe('role_revealed', char=key, role='KEY')
        d.n_hyp()
        assert d.truth_lost_by is None and d.empty_by is None
        d.observe('not_role', char=key, roles=('KEY',))   # 真の脚本と矛盾する観測
        d.n_hyp()
        assert d.truth_lost_by[0] == 'not_role' and d.empty_by[0] == 'not_role'
    finally:
        Deduction.WATCH = None


def test_assumption_is_refuted_by_a_hard_observation():
    d = Deduction(CHARS)
    a = d.assume_card('C02', 'INT2', {}, 'L1D1 INT2')     # 「C02 への暗躍は負け筋」＝C02 はキーパーソン・フレンド・キラー・メインラバーズ
    assert a['status'] == 'alive' and a['support'] == 1
    assert d.assume_card('C02', 'INT1', {}, 'L1D2 INT1') is a and a['support'] == 2
    assert [x['name'] for x, _ in d.live_assumptions()] == [a['name']]
    d.observe('not_role', char='C02', roles=('KEY', 'FRIEND', 'KILLER', 'MAIN_LOVERS'))
    d.n_hyp()
    assert a['status'] == 'refuted' and a['refuted_by'][0] == 'not_role'
    assert d.live_assumptions() == []
    assert d.n_hyp() > 0                                   # 仮定が否定されても、推理そのものは残る
