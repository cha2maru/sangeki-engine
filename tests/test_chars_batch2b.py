"""キャラクター第2陣b の境界例（play/tests/cases/chars_batch2b.json。エンジンのコードを見ない担当が一次資料から先に書いた期待値）:
イレギュラー・大物・幻想・コピーキャット・ご神木・アルバイト・アルバイト？・従者（上位存在は対象外）。"""
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
from engine import scripts  # noqa: E402
from test_abilities import _full, _reveal  # noqa: E402

CASES = [c for c in json.load(open(os.path.join(HERE, 'cases', 'chars_batch2b.json'), encoding='utf-8')) if 'id' in c]
REASON = {'キーパーソン死亡': 'KEY_DIED', '主人公死亡': 'PROTAGONIST_DIED', 'TT_LOSS': 'TT_LOSS'}
ERR = (IllegalPlacement, KeyError, TypeError)


def _phase(ph_name, case, s, out, choice):
    if ph_name == 'incident':
        inc = ph.incident_today(s)[0]
        out['occurred'] = ph.incident_occurs(s, inc)
        ph.run_incident(s, inc, choice)
    elif ph_name == 'turn_end':
        ph.turn_end(s, (choice or {}).get('optional', []))
    elif ph_name == 'loop_start':
        s, _ = ph.loop_start(s, ph.default_init(s['chars']), choice)
    elif ph_name == 'turn_start':
        ph.turn_start(s)
    elif ph_name == 'action':
        src = case.get('input') or case['then']['input']
        s, _ = resolve_actions(s, src['placements'], src.get('optional'), src.get('follow'))
    elif ph_name == 'mm_ability':
        ph.mm_phase(s, choice['uses'])
    elif ph_name == 'script_check':
        out['script_ok'] = scripts.check(case['script'])[0] == []
    elif ph_name == 'loop_end':
        reasons = ph.loop_end_loss(s)
        out['loss'], out['reasons'] = bool(reasons), reasons
    elif ph_name == 'final':
        out.update(ab.final_battle(s, case['guesses']))
    return s


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_chars_batch2b(case):
    if case.get('status') == 'not_applicable':
        pytest.skip('この惨劇セットに無い要素: ' + case['desc'])
    if case.get('status') == 'unresolved':
        pytest.xfail('一次資料で決まらない: ' + str(case.get('unresolved', case['desc'])))
    if case.get('phase') == 'script_check':
        ok = scripts.check(case['script'])[0] == []
        assert ok == case['expect']['script_ok'], scripts.check(case['script'])[0]
        return
    s = _full(case['state'])
    s.setdefault('ability_used_today', [])
    s.setdefault('ability_used_loop', [])
    exp = case['expect']
    out = {'loop_end': None}
    before = copy.deepcopy(s)
    try:
        if case.get('declare'):
            refs = case.get('refuse') or [False] * len(case['declare'])
            for i, ((cid, idx, arg), ref) in enumerate(zip(case['declare'], refs)):
                if i == len(case['declare']) - 1 and 'error' in exp:
                    before = copy.deepcopy(s)
                    with pytest.raises(ERR):
                        ab.use_ability(s, cid, idx, arg, refuse=ref)
                    assert s == before, '誤った宣言で状態が変わった'
                    return
                ev = ab.use_ability(s, cid, idx, arg, refuse=ref)
                out['refused'] = any(e.get('kind') == 'refused' for e in ev)
                out['reveal'] = _reveal(ev)
                for e in ev:
                    if 'reveal_same_role' in e:
                        (c, cs), = e['reveal_same_role'].items()
                        out['reveal']['same_role'] = {'char': c, 'chars': sorted(cs)}
            if case.get('then'):
                s = _phase(case['then']['phase'], case, s, out, case['then'].get('choice'))
        else:
            if 'error' in exp:
                with pytest.raises(ERR):
                    _phase(case['phase'], case, s, out, case.get('choice'))
                assert s == before, '誤った処理で状態が変わった'
                return
            s = _phase(case['phase'], case, s, out, case.get('choice'))
            if case.get('then'):
                s = _phase(case['then']['phase'], case, s, out, case['then'].get('choice'))
    except ph.LoopEnd as e:
        out['loop_end'] = {'reason': REASON[e.reason], 'defeat': e.loss}
    for cid, want in exp.get('chars', {}).items():
        for k, v in want.items():
            got = s['chars'][cid].get(k, True if k == 'present' else 0)
            assert got == v, f"{cid}.{k}: {got} != {v}"
    for a, v in exp.get('boards', {}).items():
        assert s['boards'][a] == v, a
    if 'used_loop' in exp:
        assert sorted(s.get('ability_used_loop', [])) == sorted(exp['used_loop'])
    for k in ('refused', 'occurred', 'loop_end', 'loss'):
        if k in exp:
            assert out.get(k) == exp[k], (k, out.get(k))
    if 'reasons' in exp:
        assert set(out['reasons']) == set(exp['reasons'])
    if 'reveal' in exp:
        want = dict(exp['reveal'])
        if 'same_role' in want:
            want['same_role'] = dict(want['same_role'], chars=sorted(want['same_role']['chars']))
        assert out['reveal'] == want
    for k in ('script_ok', 'winner', 'stopped_at'):
        if k in exp:
            assert out.get(k) == exp[k], (k, out.get(k))
    if 'servant_targets' in exp:
        assert sorted(s.get('servant_targets', [])) == sorted(exp['servant_targets'])
    for p, v in exp.get('used', {}).items():
        assert sorted(s['used'][p]) == sorted(v)
    for k in ('loop', 'day'):
        if k in exp:
            assert s[k] == exp[k]
    if 'suppressed_culprits' in exp:
        assert sorted(s.get('suppressed_culprits', [])) == sorted(exp['suppressed_culprits'])
    for rec in exp.get('incident_log', []):
        assert rec in s.get('incident_log', []), rec
    for rec in exp.get('incident_log_excludes', []):
        assert rec not in s.get('incident_log', []), rec
