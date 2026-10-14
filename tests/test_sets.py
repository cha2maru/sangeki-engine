"""惨劇セットのデータ（engine/data/sets）。First Steps（主人公の書 印刷p29〜31、wiki/sets/first-steps.md）がデータだけで入ること。"""
import copy
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from env import Env  # noqa: E402
from engine.scripts import by_id, check, find, load  # noqa: E402

FS = load(find('s09_first_steps.json'))


def test_first_steps_script_check():
    assert check(FS)[0] == []
    b = copy.deepcopy(FS)
    b['rules'] = ['Y_PROTECT', 'X_RIPPER', 'X_RUMOR']  # ルールXは1つ
    assert check(b)[0]
    b = copy.deepcopy(FS)
    b['roles']['C03'] = 'TT'  # First Steps に無い役職
    assert check(b)[0]
    b = copy.deepcopy(FS)
    b['incidents'][0]['id'] = 'CORRUPT'  # First Steps に無い事件
    assert check(b)[0]
    b = copy.deepcopy(FS)  # 最低の却本: マイナスは0〜2人
    b['rules'] = ['Y_PROTECT', 'X_WORST']
    b['roles'] = {'C02': 'KEY', 'C08': 'CULTIST', 'C05': 'MISLEADER', 'C01': 'FRIEND'}
    for minus in ([], ['C03'], ['C03', 'C04']):
        b['roles'].update({c: 'MINUS' for c in minus})
        assert check(b)[0] == [], minus
    b['roles']['C09'] = 'MINUS'
    assert check(b)[0]


def test_first_steps_no_final_battle():
    """全ループで敗北すれば最後の戦いをせずに脚本家の勝利。"""
    env = Env('s09_first_steps', mm='route')
    d, rng, kinds = env.reset(2), random.Random(2), set()
    while d['kind'] not in ('end', 'error'):
        kinds.add(d['kind'])
        d = env.step(rng.choice(d['options']))
    env.close()
    assert d['kind'] == 'end' and d['result'] == {'winner': 'mastermind', 'loop': by_id('s09_first_steps')['loops']}
    assert 'final_guess' not in kinds


def test_first_steps_deduction_keeps_truth():
    """推理の仮説の表は First Steps のもの（ルールX1つ・最低の却本のマイナス0〜2人）。真の脚本を消さない。"""
    from engine.deduce import Deduction, table
    from selfplay.league import make
    from selfplay.players import PublicObserver
    from selfplay.run import Combo, play_game
    T = table('FS')
    assert all(len(cb) == 2 for cb in T.combos) and T.variants[('Y_PROTECT', 'X_WORST')] == 3
    base = by_id('s09_first_steps')
    for rules, roles in ((['Y_PROTECT', 'X_RIPPER'], base['roles']),
                         (['Y_REVENGE', 'X_WORST'], {'C04': 'KUROMAKU', 'C05': 'MISLEADER', 'C03': 'FRIEND', 'C01': 'MINUS'})):
        sc = dict(copy.deepcopy(base), rules=rules, roles=roles)
        Deduction.WATCH = {'rules': rules, 'roles': roles}
        obs = PublicObserver()
        try:
            play_game(sc, Combo(make('route', random.Random(1), sc), make('read', random.Random(1), sc)), lambda k, rec: None, observers=[obs])
        finally:
            Deduction.WATCH = None
        assert obs.ded.T.id == 'FS' and obs.ded.truth_lost_by is None and obs.ded.empty_by is None and obs.ded.n_hyp() > 0
