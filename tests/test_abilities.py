"""段4 の境界例（play/tests/cases/abilities.json。エンジンのコードを見ない担当が一次資料から先に書いた期待値）。"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine import abilities as ab  # noqa: E402
from engine import phases as ph  # noqa: E402
from engine.resolve import IllegalPlacement, resolve_actions  # noqa: E402

CASES = [c for c in json.load(open(os.path.join(HERE, 'cases', 'abilities.json'), encoding='utf-8')) if 'id' in c]


def _full(st):
    s = copy.deepcopy(st)
    s.setdefault('boards', {a: 0 for a in ph.AREAS})
    s.setdefault('used', {p: [] for p in 'MABC'})
    s.setdefault('leader', 'A')
    for c in s['chars'].values():
        for k, v in (('alive', True), ('par', 0), ('gw', 0), ('int', 0), ('guard', 0)):
            c.setdefault(k, v)
    return s


def _reveal(events):
    out = {}
    for e in events:
        if 'reveal_role' in e:
            (c, r), = e['reveal_role'].items()
            out['role'] = {'char': c, 'role': r}
        if 'reveal_culprit' in e:
            (i, c), = e['reveal_culprit'].items()
            out['culprit'] = {'incident': i, 'char': c}
        if 'reveal_rule_x' in e:
            x = e['reveal_rule_x']
            if isinstance(x, dict):
                out['rule_x_options'] = x['choose_from']
            elif x is not None:
                out['rule_x'] = x
    return out


def _run(case, s):
    a = case['action']
    out = {}
    if a['type'] == 'ability':
        ev = ab.use_ability(s, a['char'], a['ability'], a.get('arg'), refuse=a.get('refuse', False))
        out['refused'] = any(e['kind'] == 'refused' for e in ev)
        out['reveal'] = _reveal(ev)
    elif a['type'] == 'mm_phase':
        ph.mm_phase(s, a['uses'])
    elif a['type'] == 'move_after':
        new, _ = resolve_actions(s, a['placements'])
        s.clear(); s.update(new)
    elif a['type'] == 'kill':
        ev = []
        try:
            ph.kill(s, a['char'], a['cause'], ev)
            ph.check_key_death(s, ev)
            out['loop_end'] = None
        except ph.LoopEnd as e:
            out['loop_end'] = e.reason
    elif a['type'] == 'final':
        out.update(ab.final_battle(s, a['guesses']))
    return out


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_ability_case(case):
    if case.get('status') == 'not_applicable':
        pytest.skip('この惨劇セットでは起こりえない: ' + case.get('desc', ''))
    if case.get('status') == 'unconfirmed':
        pytest.xfail('一次資料で未確認: ' + case.get('desc', ''))
    s = _full(case['state'])
    exp = case['expect']
    if 'error' in exp:
        before = copy.deepcopy(s)
        with pytest.raises((IllegalPlacement, KeyError, TypeError)):
            _run(case, s)
        return
    out = _run(case, s)
    for cid, want in exp.get('chars', {}).items():
        for k, v in want.items():
            assert s['chars'][cid][k] == v, f"{cid}.{k}: {s['chars'][cid][k]} != {v}  ({case['source'][:80]})"
    for a, v in exp.get('boards', {}).items():
        assert s['boards'][a] == v, a
    if 'refused' in exp:
        assert out['refused'] == exp['refused']
    if 'reveal' in exp:
        assert out['reveal'] == exp['reveal'], out['reveal']
    if 'used_loop' in exp:
        assert sorted(s.get('ability_used_loop', [])) == sorted(exp['used_loop'])
    if 'unbound' in exp:
        assert sorted(s.get('unbound', [])) == sorted(exp['unbound'])
    if 'loop_end' in exp:
        assert (out['loop_end'] is None) == (exp['loop_end'] is None), (out['loop_end'], exp['loop_end'])
    if 'winner' in exp:
        assert out['winner'] == exp['winner'] and out['stopped_at'] == exp['stopped_at']


def test_every_targeted_ability_kind_gets_candidates():
    """宣言の候補（declaration_options）が、対象を取る能力の種類すべてで作られる（大物[5]・幻想[3]・ご神木・従者[4] が抜けていた）。"""
    from engine.abilities import ABILITIES, declaration_options
    ch = lambda a, **k: dict({'area': a, 'alive': True, 'par': 0, 'gw': 5, 'int': 0, 'guard': 0}, **k)  # noqa: E731
    s = {'loop': 2, 'day': 1, 'leader': 'A', 'script': {'rules': [], 'roles': {}, 'incidents': [], 'territory': 'CIT'},
         'chars': {'C16': ch('CIT'), 'C05': ch('CIT'), 'C20': ch('SHR'), 'C04': ch('SHR'), 'C30': ch('SCH', par=1), 'C01': ch('SCH'),
                   'C34': ch('HOS')},
         'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')}, 'used': {p: [] for p in 'MABC'}, 'ability_used_loop': []}
    got = {(c, i) for c, i, _ in declaration_options(s)}
    assert {('C16', 0), ('C20', 0), ('C30', 0), ('C34', 0)} <= got
