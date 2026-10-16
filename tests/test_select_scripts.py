"""選定の道具（selfplay.select_scripts）: 試合の記録の読み方と、条件での選び方。"""
import argparse
import json

from selfplay import select_scripts as S


def _game(d, sid, mm, seed, final, ent):
    base = f"{sid.replace('/', '_')}_{mm}_s{seed}"  # 脚本家の名前に '.' が入る（calcGBx@0.3）ので with_suffix は使わない
    (d / (base + '.res')).write_text(json.dumps({'winner': 'mastermind', 'loop': 'final' if final else 2}))
    (d / (base + '.jsonl')).write_text(json.dumps({'k': 'belief', 'loop': 1, 'entropy': ent}) + '\n')


def test_pick_by_reach_and_entropy(tmp_path, capsys):
    good = {'id': 'designed/d02_traits', 'days': 6, 'taboo': False, 'role_ok': 1.0, 'inc_ok': 0.75, 'info': 3, 'diff': '', 'kind': 'pos'}
    flat = dict(good, id='generated/gen_x_001')  # 最後の戦いに届かない
    leaky = dict(good, id='designed/d17')        # 割れすぎ（エントロピーが低い）
    for sd in (1, 2, 3):
        _game(tmp_path, good['id'], 'calcGBx@0.3', sd, True, 8.0)
        _game(tmp_path, flat['id'], 'calcGBx@0.3', sd, sd == 1, 8.0)
        _game(tmp_path, leaky['id'], 'calcGBx@0.3', sd, True, 1.5)
    G = S.load_games(str(tmp_path))
    assert set(G) == {good['id'], flat['id'], leaky['id']} and G[good['id']][0][0] == 'calcGBx@0.3'
    a = argparse.Namespace(elem=0.5, info=2, days_min=5, days_max=7, ent_min=3, ent_max=12)
    assert S.pick([good, flat, leaky], G, a) == [good['id']]


def test_static_counts_supporting_roles_and_incidents():
    """筋の条件に名前が出ない補助の役職（カルティスト・ミスリーダー）と流布も「使われる」と数える（d22: 以前は 役0.33）。"""
    s = S.static_row('designed/d22')
    assert s['role_ok'] >= 0.5 and s['inc_ok'] >= 0.5
