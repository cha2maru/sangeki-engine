"""公開用の射影から隠し情報が出ないこと（計画の決定2、レビュー H3）。"""
import json
import os
import pytest
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine.project import project  # noqa: E402

FIX = os.path.join(os.path.dirname(HERE), 'engine', 'fixtures', 'toukou001_g1')
pytestmark = pytest.mark.skipif(not os.path.isdir(FIX), reason='toukou_001 の記録（他の方の投稿シナリオ）は公開版に無い')


def _full():
    script = json.load(open(os.path.join(FIX, 'script.json'), encoding='utf-8'))
    snap = json.load(open(os.path.join(FIX, 'snapshots.json'), encoding='utf-8'))['snapshots'][0]['state']
    snap['script'] = {k: script[k] for k in ('rules', 'roles', 'incidents')}
    return snap, script


def test_no_hidden_information_in_projection():
    full, script = _full()
    placed = [{'by': 'M', 'target': 'C03', 'card': 'INT1'}, {'by': 'A', 'target': 'C06', 'card': 'GW2'}]
    out = json.dumps(project(full, placed), ensure_ascii=False)
    # 役職・ルールの ID と和名、犯人の記述、脚本家の伏せ札の中身が出ていない
    for rid in list(script['roles'].values()) + script['rules']:
        assert rid not in out, rid
    for word in ('キーパーソン', 'キラー', 'クロマク', 'ミスリーダー', 'パーソン', '殺人計画', '因果の糸', '妄想拡大ウイルス', 'culprit', 'script', '暗躍+1'):
        assert word not in out, word
    # 主人公の札は公開してよい（置いた本人たちの情報）
    assert '友好+2' in out


def test_revealed_cards_show_after_resolution():
    full, _ = _full()
    placed = [{'by': 'M', 'target': 'C03', 'card': 'INT1'}]
    assert project(full, placed, revealed=True)['cards'][0]['card'] == '暗躍+1'
