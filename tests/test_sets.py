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
