"""手動モード形式 → エンジンの変換（adapter）で、記録の L1D1 を行動解決できること。"""
import json
import os
import pytest
import sys

HERE = os.path.dirname(__file__)
PLAY = os.path.dirname(HERE)
sys.path.insert(0, PLAY)
from engine.adapter import full_from_public, placements_from_cards_file, placements_from_inbox  # noqa: E402
from engine.project import project  # noqa: E402
from engine.resolve import resolve_actions  # noqa: E402

FIX = os.path.join(PLAY, 'engine', 'fixtures', 'toukou001_g1')
pytestmark = pytest.mark.skipif(not os.path.isdir(FIX), reason='toukou_001 の記録（他の方の投稿シナリオ）は公開版に無い')
REC = os.path.join(PLAY, 'records', '2026-09-23_toukou001')


def test_l1d1_via_manual_formats():
    script = json.load(open(os.path.join(FIX, 'script.json'), encoding='utf-8'))
    sc = {k: script[k] for k in ('rules', 'roles', 'incidents')}
    snaps = {(s['loop'], s['day'], s['point']): s['state'] for s in json.load(open(os.path.join(FIX, 'snapshots.json'), encoding='utf-8'))['snapshots']}
    start = dict(snaps[(1, 1, 'start')], script=sc)
    pub = project(start)                       # エンジン → 公開形式
    full = full_from_public(pub, sc)           # 公開形式 → エンジン（往復）
    assert full['chars'] == start['chars']
    mm = placements_from_cards_file(open(os.path.join(REC, 'revealed', 'cards_L1D1.txt'), encoding='utf-8').read())
    rec = next(json.loads(l) for l in open(os.path.join(REC, 'inbox.jsonl'), encoding='utf-8') if json.loads(l)['seq'] == 10)
    new, _ = resolve_actions(full, mm + placements_from_inbox(rec))
    want = snaps[(1, 1, 'after_actions')]
    for c, v in want['chars'].items():
        for k in ('area', 'par', 'gw', 'int', 'alive'):
            assert new['chars'][c][k] == v[k], (c, k)
