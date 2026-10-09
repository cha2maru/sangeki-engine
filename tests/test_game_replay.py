"""対戦記録（toukou001_g1）を、エンジンだけで最初から最後まで再生する（段1〜4）。

入力は伏せ札・主人公の札（inputs.json）と、脚本家・主人公の選択（decisions.jsonl）だけ。
外から出来事を注入せず、各日の「ターン終了後」のスナップショットと、ループ終了・勝敗が一致することを確かめる。
"""
import copy
import json
import os
import pytest
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine import phases as ph  # noqa: E402
from engine.abilities import use_ability  # noqa: E402
from engine.resolve import resolve_actions  # noqa: E402

FIX = os.path.join(os.path.dirname(HERE), 'engine', 'fixtures', 'toukou001_g1')
pytestmark = pytest.mark.skipif(not os.path.isdir(FIX), reason='toukou_001 の記録（他の方の投稿シナリオ）は公開版に無い')
CMP = ('area', 'alive', 'par', 'gw', 'int', 'guard')


def _load():
    script = json.load(open(os.path.join(FIX, 'script.json'), encoding='utf-8'))
    inputs = {(d['loop'], d['day']): d for d in json.load(open(os.path.join(FIX, 'inputs.json'), encoding='utf-8'))}
    decs = [json.loads(l) for l in open(os.path.join(FIX, 'decisions.jsonl'), encoding='utf-8')]
    snaps = {(s['loop'], s['day'], s['point']): s['state'] for s in json.load(open(os.path.join(FIX, 'snapshots.json'), encoding='utf-8'))['snapshots']}
    return script, inputs, decs, snaps


def _cmp(got, want, where):
    for c, w in want['chars'].items():
        for k in CMP:
            assert got['chars'][c][k] == w[k], f'{where} {c}.{k}: エンジン {got["chars"][c][k]} / 記録 {w[k]}'
    for a in ph.AREAS:
        assert got['boards'][a] == want['boards'][a], f'{where} board {a}'
    for p in 'MABC':
        assert sorted(got['used'][p]) == sorted(want['used'][p]), f'{where} used {p}'
    assert got['leader'] == want['leader'], f'{where} leader'


def test_full_game_replay():
    script, inputs, decs, snaps = _load()
    order = ['A', 'B', 'C']
    first = snaps[(1, 1, 'start')]
    init = {c: v['area'] for c, v in first['chars'].items()}
    s = copy.deepcopy(first)
    s['script'] = {k: script[k] for k in ('rules', 'roles', 'incidents')}
    s['incident_log'] = []
    results = []
    for loop in (1, 2):
        if loop > 1:
            s, _ = ph.loop_start(s, init)
            _cmp(s, snaps[(loop, 1, 'start')], f'L{loop} 開始')
        s['ability_used_loop'] = []
        try:
            for day in range(1, script['days'] + 1):
                s['day'] = day
                s['ability_used_today'] = []
                d = inputs[(loop, day)]
                s, _ = resolve_actions(s, d['mm_cards'] + d['pc_cards'])
                today = [x for x in decs if x['loop'] == loop and x['day'] == day]
                ph.mm_phase(s, [{'char': x['options']['holder'], 'ability': x['role'], 'target': x['choice']['target']}
                                for x in today if x['kind'] == 'mm_ability' and x['choice'].get('use')])
                for x in today:
                    if x['kind'] == 'ability' and not x['choice'].get('end'):
                        ch = dict(x['choice'])
                        cid, idx = ch.pop('char'), ch.pop('ability')
                        use_ability(s, cid, idx, ch or None, refuse=bool(x.get('refused')))
                for inc in ph.incident_today(s):
                    pick = next((x['choice'] for x in today if x['kind'] == 'incident_target' and x['incident'] == inc['id']), None)
                    if pick and 'par2' in pick:
                        pick = {'par_target': pick['par2'], 'int_target': pick['int1']}
                    ph.run_incident(s, inc, pick)
                s['leader'] = order[(order.index(s['leader']) + 1) % 3]
                ph.turn_end(s)
                _cmp(s, snaps[(loop, day, 'end')], f'L{loop}D{day} 終了')
            results.append(('loop_complete', loop))
            if not ph.loop_end_loss(s):
                results.append(('protagonists_win', loop))
                break
        except ph.LoopEnd as e:
            results.append(('loop_end', loop, day, e.reason))
            _cmp(s, snaps[(loop, day, 'end')], f'L{loop}D{day} ループ終了')
    assert results == [('loop_end', 1, 7, 'キーパーソン死亡'), ('loop_complete', 2), ('protagonists_win', 2)], results
