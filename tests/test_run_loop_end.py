"""ループが1日の途中で終わったとき、その日の変化を含む状態で次のループを準備する（因果の糸は前のループ終了時の友好を見る）。"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import phases as ph  # noqa: E402
from selfplay import run  # noqa: E402


def test_mid_day_loop_end_keeps_that_days_changes(monkeypatch):
    script = {'rules': ['Y_BOMB', 'X_THREAD', 'X_LOVE'], 'roles': {'C06': 'WITCH', 'C04': 'MAIN_LOVERS', 'C07': 'LOVERS'},
              'incidents': [], 'loops': 2, 'days': 3, 'init': {'C04': 'SHR', 'C06': 'CIT', 'C07': 'CIT'}}
    seen = {}

    def fake_day(s, player, loop, day, log, box, mm_override=None):
        import copy
        s = copy.deepcopy(s)
        box['s'] = s
        if loop == 1 and day == 2:
            s['chars']['C07']['gw'] = 2  # その日のうちに置かれた友好
            raise ph.LoopEnd('主人公死亡', loss=True)
        if loop == 2 and day == 1:
            seen['par'] = s['chars']['C07']['par']
            raise ph.LoopEnd('主人公死亡', loss=True)
        return s
    monkeypatch.setattr(run, 'play_day', fake_day)
    player = run.Combo(run.RandomPlayer(random.Random(0)), run.RandomPlayer(random.Random(1)))
    try:
        run.play_game(script, player, lambda *a: None)
    except Exception:
        pass
    assert seen.get('par') == 2  # 因果の糸: 前のループの終わり（2日目の途中）に友好があった → 不安2


def test_loop_end_records_the_phase(monkeypatch):
    """どのフェイズでループが終わったかは公開情報（ユーザー指摘）。loop_end の出来事に at_phase を付ける。"""
    script = {'rules': ['Y_BOMB', 'X_THREAD', 'X_LOVE'], 'roles': {'C06': 'WITCH'}, 'incidents': [], 'loops': 1, 'days': 2,
              'init': {'C06': 'CIT', 'C07': 'CIT'}}
    got = []

    def fake_day(s, player, loop, day, log, box, mm_override=None):
        box['s'] = s
        log('decisions', {'loop': loop, 'day': day, 'phase': 'turn_end', 'who': 'M', 'kind': 'killer', 'choice': []})
        raise ph.LoopEnd('主人公死亡', loss=True)
    monkeypatch.setattr(run, 'play_day', fake_day)

    def log0(kind, rec):
        if rec.get('phase') == 'loop_end':
            got.extend(e for e in rec['events'] if e.get('kind') == 'loop_end')
    player = run.Combo(run.RandomPlayer(random.Random(0)), run.RandomPlayer(random.Random(1)))
    try:
        run.play_game(script, player, log0)
    except Exception:
        pass
    assert got and got[0]['at_phase'] == 'turn_end'


def test_observer_uses_loop_end_phase_and_tt_reason():
    """終わったフェイズ（at_phase）で主人公死亡の原因を分け、タイムトラベラーの敗北（エンジンの理由 'TT_LOSS'）も観測する。"""
    from selfplay.players import PublicObserver
    chars = {c: {'area': 'CIT', 'alive': True, 'par': 3, 'gw': 1, 'int': 1, 'guard': 0} for c in ('C01', 'C02', 'C03')}
    s = {'chars': chars, 'boards': {'HOS': 0, 'SHR': 0, 'CIT': 0, 'SCH': 0}, 'script': {'incidents': []}}
    seen = []
    for ev, want in (([{'kind': 'loop_end', 'reason': '主人公死亡', 'at_phase': 'turn_end'}], 'prot_death_turn_end'),
                     ([{'kind': 'loop_end', 'reason': '主人公死亡', 'at_phase': 'incident'}], None),
                     ([{'kind': 'loop_end', 'reason': 'TT_LOSS', 'at_phase': 'turn_end'}], 'tt_loss')):
        o = PublicObserver()
        o._ensure(s)
        got = []
        o.ded.observe = lambda kind, **kw: got.append(kind)
        o.observe(s, 'loop_end', ev)
        seen.append((want, want in got if want else not got))
    assert all(ok for _, ok in seen), seen


def test_dead_factor_gaining_key_ability_does_not_end_loop():
    """キーパーソン【強制: 死亡時】。先に死んだファクターが、後で都市の暗躍2以上でキーパーソンの能力を得ても、
    別の人物の死亡でループは終わらない（d16）。ファクターがその場で死ねば終わる。"""
    st = {'script': {'roles': {'C03': 'FACTOR'}, 'rules': []}, 'boards': {'CIT': 2, 'SCH': 0, 'HOS': 0, 'SHR': 0},
          'chars': {'C03': {'alive': False, 'par': 0}, 'C20': {'alive': False, 'par': 0}}}
    ph.check_key_death(st, [{'kind': 'death', 'char': 'C20'}])  # 終わらない
    try:
        ph.check_key_death(st, [{'kind': 'death', 'char': 'C03'}])
        assert False, 'ファクターの死亡でループが終わるはず'
    except ph.LoopEnd:
        pass
