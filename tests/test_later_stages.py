"""段2・3 の境界例（play/tests/cases/later_stages.json。エンジンのコードを見ない担当が一次資料から先に書いた期待値）。"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine import phases as ph  # noqa: E402
from engine.resolve import IllegalPlacement  # noqa: E402

CASES = [c for c in json.load(open(os.path.join(HERE, 'cases', 'later_stages.json'), encoding='utf-8')) if 'id' in c]
REASON = {'キーパーソン死亡': 'KEY_DIED', '主人公死亡': 'PROTAGONIST_DIED'}


def _full(st):
    s = copy.deepcopy(st)
    s.setdefault('boards', {a: 0 for a in ph.AREAS})
    s.setdefault('used', {p: [] for p in 'MABC'})
    s.setdefault('leader', 'A')
    s.setdefault('script', {'rules': [], 'roles': {}, 'incidents': []})
    for c in s['chars'].values():
        for k, v in (('alive', True), ('par', 0), ('gw', 0), ('int', 0), ('guard', 0)):
            c.setdefault(k, v)
    return s


def _run(case, s):
    ch = case.get('choice') or {}
    out = {'loop_end': None}
    try:
        if case['phase'] == 'turn_end':
            ph.turn_end(s, ch.get('optional', []))
        elif case['phase'] == 'incident':
            today = ph.incident_today(s)
            if not today:  # その日に事件が無ければ何も行わない（印刷p24 4-7）
                out['occurred'] = False
                return out
            inc = today[0]
            out['occurred'] = ph.incident_occurs(s, inc)
            pick = ch
            if 'par' in ch:
                pick = {'par_target': ch['par'], 'int_target': ch['int']}
            ph.run_incident(s, inc, pick or None)
        elif case['phase'] == 'mm_ability':
            ph.mm_phase(s, ch.get('uses', []))
        elif case['phase'] == 'loop_start':
            s2, _ = ph.loop_start(s, ph.default_init(s['chars']))
            s.clear(); s.update(s2)
    except ph.LoopEnd as e:
        out['loop_end'] = {'reason': REASON[e.reason], 'defeat': e.loss}
    return out


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_later(case):
    if case.get('status') == 'not_applicable':
        pytest.skip('この惨劇セットでは起こりえない: ' + case.get('desc', ''))
    if case.get('status') == 'unconfirmed':
        pytest.xfail('一次資料で未確認: ' + case.get('desc', ''))
    s = _full(case['state'])
    exp = case['expect']
    if 'error' in exp:
        with pytest.raises((IllegalPlacement, KeyError, TypeError)):
            _run(case, s)
        return
    out = _run(case, s)
    for cid, want in exp.get('chars', {}).items():
        for k, v in want.items():
            assert s['chars'][cid][k] == v, f"{cid}.{k}: {s['chars'][cid][k]} != {v}  ({case['source'][:80]})"
    for a, v in exp.get('boards', {}).items():
        assert s['boards'][a] == v, a
    if 'occurred' in exp:
        assert out.get('occurred') == exp['occurred']
    if 'loop_end' in exp:
        assert out['loop_end'] == exp['loop_end'], out
    for k in ('loop', 'day'):
        if k in exp:
            assert s[k] == exp[k]
    if 'used' in exp:
        for p, v in exp['used'].items():
            assert sorted(s['used'][p]) == sorted(v)
