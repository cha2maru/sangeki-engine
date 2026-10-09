"""キャラクター第1陣の友好能力の境界例（play/tests/cases/chars_batch1.json。エンジンのコードを見ない担当が一次資料から先に書いた期待値）:
委員長・異世界人・アイドル・マスコミ・ナース・鑑識官・教師・軍人・女の子。"""
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
from test_abilities import _full, _reveal  # noqa: E402

CASES = [c for c in json.load(open(os.path.join(HERE, 'cases', 'chars_batch1.json'), encoding='utf-8')) if 'id' in c]
REASON = {'キーパーソン死亡': 'KEY_DIED', '主人公死亡': 'PROTAGONIST_DIED', 'TT_LOSS': 'TT_LOSS'}


def _then(case, s, out):
    th = case.get('then')
    if not th:
        return s
    ch = th.get('choice')
    if th['phase'] == 'turn_end':
        ph.turn_end(s, (ch or {}).get('optional', []))
    elif th['phase'] == 'incident':
        inc = ph.incident_today(s)[0]
        out['occurred'] = ph.incident_occurs(s, inc)
        ph.run_incident(s, inc, ch)
    elif th['phase'] == 'action':
        s, _ = resolve_actions(s, th['input']['placements'], th['input'].get('optional'))
    elif th['phase'] == 'loop_start':
        s, _ = ph.loop_start(s, ph.default_init(s['chars']))
    return s


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_chars_batch1(case):
    if case.get('status') == 'unresolved':
        pytest.xfail('一次資料で決まらない: ' + str(case.get('unresolved', case['desc'])))
    s = _full(case['state'])
    s.setdefault('ability_used_today', [])
    s.setdefault('ability_used_loop', [])
    exp = case['expect']
    decl, refs = case['declare'], case.get('refuse') or [False] * len(case['declare'])
    out = {'loop_end': None}
    for i, ((cid, idx, arg), ref) in enumerate(zip(decl, refs)):
        last = i == len(decl) - 1
        if last and 'error' in exp:
            before = copy.deepcopy(s)
            with pytest.raises((IllegalPlacement, KeyError, TypeError)):
                ab.use_ability(s, cid, idx, arg, refuse=ref)
            assert s == before, '誤った宣言で状態が変わった'
            return
        try:
            ev = ab.use_ability(s, cid, idx, arg, refuse=ref)
        except ph.LoopEnd as e:
            out['loop_end'] = {'reason': REASON[e.reason], 'defeat': e.loss}
            ev = e.events
        out['refused'] = any(e.get('kind') == 'refused' for e in ev)
        out['reveal'] = _reveal(ev)
    if out['loop_end'] is None:
        try:
            s = _then(case, s, out)
        except ph.LoopEnd as e:
            out['loop_end'] = {'reason': REASON[e.reason], 'defeat': e.loss}
    for cid, want in exp.get('chars', {}).items():
        for k, v in want.items():
            assert s['chars'][cid].get(k, 0) == v, f"{cid}.{k}: {s['chars'][cid].get(k)} != {v}"
    for a, v in exp.get('boards', {}).items():
        assert s['boards'][a] == v, a
    for p, v in exp.get('used', {}).items():
        assert sorted(s['used'][p]) == sorted(v)
    if 'used_loop' in exp:
        assert sorted(s.get('ability_used_loop', [])) == sorted(exp['used_loop'])
    for k in ('refused', 'occurred', 'loop_end'):
        if k in exp:
            assert out.get(k) == exp[k], (k, out.get(k))
    if 'reveal' in exp:
        assert out['reveal'] == exp['reveal']
    if 'unbound' in exp:
        assert sorted(s.get('unbound', [])) == sorted(exp['unbound'])
    for k, v in exp.get('flags', {}).items():
        assert bool(s.get(k, False)) == v, k
