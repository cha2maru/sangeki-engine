"""card_read: 型ごとの中身の確率は合計1、候補は手札から重複なく（1ループ1回の札は使ったら出ない）。"""
import math

from engine.scripts import by_id
from selfplay.card_read import TYPES, candidates, dists
from selfplay.script_routes import start_state


def test_candidates_and_dists():
    s = start_state(by_id('s03_bomb'))
    targets = [t for t in ('B:SHR', 'B:CIT', 'B:SCH')]
    cands = candidates(s, targets)
    assert cands and all(len(set(x['cards'])) == 3 or x['cards'].count('PAR+') == 2 for x in cands)
    d = dists(cands)
    assert all(math.isclose(sum(d[k]), 1.0) for k in TYPES)
    s['used']['M'] = ['INT2']
    assert not any('INT2' in x['cards'] for x in candidates(s, targets))
