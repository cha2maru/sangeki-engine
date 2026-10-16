"""脚本家の事件の対象選び（RouteMastermind.incident_choice）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.scripts import by_id  # noqa: E402
from selfplay.players import RouteMastermind  # noqa: E402
from selfplay.script_routes import start_state  # noqa: E402


def test_murder_kills_friend_over_person():
    """d12: 都市の殺人事件（犯人 C07）で、候補はパーソン C06・フレンド C14・ミスリーダー C15。
    フレンドの死はループ終了時の敗北なので C14 を選ぶ（以前は「キーパーソン、いなければ候補の先頭」で C06 を殺していた）。"""
    sc = by_id('designed/d12')
    s = start_state(sc)
    s['chars']['C07']['par'], s['day'] = 9, 3
    inc = next(i for i in sc['incidents'] if i['id'] == 'MURDER')
    assert RouteMastermind.incident_choice(None, s, inc) == {'target': 'C14'}
