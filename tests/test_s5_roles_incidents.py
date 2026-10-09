"""段S5 3・4 の境界例（play/tests/cases/s5_roles_incidents.json。エンジンのコードを見ない担当が一次資料から先に書いた期待値）:
タイムトラベラー・ラバーズ・メインラバーズ、病院の事件・遠隔殺人・行方不明・流布・蝶の羽ばたき。"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine import phases as ph  # noqa: E402
from engine.resolve import IllegalPlacement, resolve_actions  # noqa: E402
from test_later_stages import _full  # noqa: E402

CASES = [c for c in json.load(open(os.path.join(HERE, 'cases', 's5_roles_incidents.json'), encoding='utf-8')) if 'id' in c]
REASON = {'キーパーソン死亡': 'KEY_DIED', '主人公死亡': 'PROTAGONIST_DIED', 'TT_LOSS': 'TT_LOSS'}


def _run(case, s):
    ch = case.get('choice')
    out = {'loop_end': None}
    try:
        if case['phase'] == 'incident':
            today = ph.incident_today(s)
            if not today:
                out['occurred'] = False
                return s, out
            inc = today[0]
            out['occurred'] = ph.incident_occurs(s, inc)
            ph.run_incident(s, inc, ch)
        elif case['phase'] == 'turn_end':
            ph.turn_end(s, (ch or {}).get('optional', []))
        elif case['phase'] == 'action':
            s, _ = resolve_actions(s, case['input']['placements'], case['input'].get('optional'))
    except ph.LoopEnd as e:
        out['loop_end'] = {'reason': REASON[e.reason], 'defeat': e.loss}
    return s, out


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_s5b(case):
    if case.get('status') == 'unresolved':
        pytest.xfail('一次資料で決まらない: ' + str(case.get('unresolved', case['desc'])))
    s = _full(case['state'])
    exp = case['expect']
    if 'error' in exp:
        before = copy.deepcopy(s)
        with pytest.raises((IllegalPlacement, KeyError, TypeError)):
            _run(case, s)
        assert s == before, '誤った選択で状態が変わった'
        return
    s, out = _run(case, s)
    for cid, want in exp.get('chars', {}).items():
        for k, v in want.items():
            assert s['chars'][cid][k] == v, f"{cid}.{k}: {s['chars'][cid][k]} != {v}"
    for a, v in exp.get('boards', {}).items():
        assert s['boards'][a] == v, a
    if 'occurred' in exp:
        assert out.get('occurred') == exp['occurred']
    if 'loop_end' in exp:
        assert out['loop_end'] == exp['loop_end'], out
    for rec in exp.get('incident_log', []):
        assert rec in s.get('incident_log', []), rec
