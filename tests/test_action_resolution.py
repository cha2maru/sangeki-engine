"""行動解決（段1）の試験。

- 境界例: play/tests/cases/action_resolution.json（エンジンのコードを見ない担当が、一次資料から先に書いた期待値）
- 回帰: play/engine/fixtures/toukou001_g1/（実際の対戦記録を構造化したもの）の各日の行動解決
"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine.resolve import IllegalPlacement, resolve_actions  # noqa: E402

CASES = os.path.join(HERE, 'cases', 'action_resolution.json')
FIX = os.path.join(os.path.dirname(HERE), 'engine', 'fixtures', 'toukou001_g1')
pytestmark = pytest.mark.skipif(not os.path.isdir(FIX), reason='toukou_001 の記録（他の方の投稿シナリオ）は公開版に無い')


def _full(state):
    s = copy.deepcopy(state)
    s.setdefault('boards', {})
    for a in ('HOS', 'SHR', 'CIT', 'SCH'):
        s['boards'].setdefault(a, 0)
    s.setdefault('used', {})
    for p in ('M', 'A', 'B', 'C'):
        s['used'].setdefault(p, [])
    for c in s.get('chars', {}).values():
        for k, v in (('alive', True), ('par', 0), ('gw', 0), ('int', 0), ('guard', 0)):
            c.setdefault(k, v)
    return s


def _load(path):
    if not os.path.exists(path):
        return []
    data = json.load(open(path, encoding='utf-8'))
    return [c for c in (data if isinstance(data, list) else data.get('cases', [])) if isinstance(c, dict) and 'id' in c]


@pytest.mark.parametrize('case', _load(CASES), ids=lambda c: c['id'])
def test_boundary(case):
    if case.get('status') == 'not_applicable':
        pytest.skip('この惨劇セットでは起こりえない: ' + case.get('desc', ''))
    if case.get('status') == 'unconfirmed':
        pytest.xfail('一次資料で未確認: ' + case.get('desc', ''))
    state = _full(case['state'])
    exp = case['expect']
    if 'error' in exp:
        with pytest.raises(IllegalPlacement):
            resolve_actions(state, case['input']['placements'], case['input'].get('optional'))
        return
    new, _ = resolve_actions(state, case['input']['placements'], case['input'].get('optional'))
    for cid, want in exp.get('chars', {}).items():
        for k, v in want.items():
            assert new['chars'][cid][k] == v, f'{cid}.{k}: {new["chars"][cid][k]} != {v}  ({case["source"]})'
    for a, v in exp.get('boards', {}).items():
        assert new['boards'][a] == v
    for p, v in exp.get('used', {}).items():
        assert sorted(new['used'][p]) == sorted(v)


def _days():
    inp = os.path.join(FIX, 'inputs.json')
    snap = os.path.join(FIX, 'snapshots.json')
    if not (os.path.exists(inp) and os.path.exists(snap)):
        return []
    return [(d, json.load(open(snap, encoding='utf-8'))) for d in json.load(open(inp, encoding='utf-8'))]


POINTS = {'start': 'start', 'after_actions': 'after_actions'}


def _snap(snaps, loop, day, key):
    for s in (snaps if isinstance(snaps, list) else snaps.get('snapshots', [])):
        if s.get('loop') == loop and s.get('day') == day and s.get('point') == POINTS[key]:
            return s['state']
    return None


@pytest.mark.parametrize('day,snaps', _days(), ids=lambda x: f"L{x['loop']}D{x['day']}" if isinstance(x, dict) and 'loop' in x and 'mm_cards' in x else '')
def test_record_replay(day, snaps):
    start = _snap(snaps, day['loop'], day['day'], 'start')
    after = _snap(snaps, day['loop'], day['day'], 'after_actions')
    assert start and after, 'スナップショットが無い'
    new, _ = resolve_actions(_full(start), day['mm_cards'] + day['pc_cards'])
    for cid, want in after['chars'].items():
        for k in ('area', 'alive', 'par', 'gw', 'int'):
            assert new['chars'][cid][k] == want[k], f"L{day['loop']}D{day['day']} {cid}.{k}: {new['chars'][cid][k]} != {want[k]}"
    for a in ('HOS', 'SHR', 'CIT', 'SCH'):
        assert new['boards'][a] == after['boards'][a]
    for p in ('M', 'A', 'B', 'C'):
        assert sorted(new['used'][p]) == sorted(after['used'][p])
