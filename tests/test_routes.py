"""勝ち筋の列挙（engine/routes.py）。

toukou_001 の脚本家の勝利条件（originals/official_dl/toukou_001.pdf「脚本家の勝利条件」
1 キーパーソンの殺害: キラーの能力、シリアルキラーの能力、殺人事件 / 2 主人公の殺害: キラーの能力）と、
自動で列挙した道筋の種類が一致することを確かめる。
"""
import json
import os
import pytest
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine.routes import enumerate_routes  # noqa: E402

FIX = os.path.join(os.path.dirname(HERE), 'engine', 'fixtures', 'toukou001_g1')
pytestmark = pytest.mark.skipif(not os.path.isdir(FIX), reason='toukou_001 の記録（他の方の投稿シナリオ）は公開版に無い')


def _state(key):
    sc = json.load(open(os.path.join(FIX, 'script.json'), encoding='utf-8'))
    snaps = {(s['loop'], s['day'], s['point']): s['state'] for s in json.load(open(os.path.join(FIX, 'snapshots.json'), encoding='utf-8'))['snapshots']}
    return dict(snaps[key], script={k: sc[k] for k in ('rules', 'roles', 'incidents')})


def test_route_kinds_match_the_script_sheet():
    kinds = {(r['id'], r['goal'].split('（')[-1].rstrip('）') if 'キーパーソン' in r['goal'] else r['goal']) for r in enumerate_routes(_state((1, 1, 'start')))}
    assert kinds == {('KILLER_KEY', 'キーパーソン'), ('SK_KILL', 'キーパーソン'), ('MURDER', 'キーパーソン'), ('KILLER_PROT', '主人公死亡')}


def test_murder_route_is_zero_when_it_happened():
    # L1D7 行動解決の直後: 情報屋（犯人、不安3）とお嬢様が都市 → この日の殺人事件でお嬢様が死亡した
    r = enumerate_routes(_state((1, 7, 'after_actions')))[0]
    assert (r['id'], r['via'], r['total']) == ('MURDER', 'C07', 0)


def test_sealed_thing_routes_match_the_manual_analysis():
    """封印されしモノ（tutorial/scenario-plan.html の非公開シート。出典はあるこじ氏のリプレイ記事＝二次資料）。
    人が分析した敗北の筋: 神社に暗躍2（ループ終了時）/ 病院に暗躍2で病院の事件→主人公死亡 / 都市に暗躍2でファクター（女子学生）が
    キーパーソンの能力を得て、その死亡で敗北（遠隔殺人・病院の事件）。"""
    script = {'rules': ['Y_SEAL', 'X_RUMOR', 'X_FACTOR'], 'roles': {'C07': 'KUROMAKU', 'C14': 'CULTIST', 'C18': 'MISLEADER', 'C02': 'FACTOR'},
              'incidents': [{'day': 2, 'id': 'SPREAD', 'culprit': 'C14'}, {'day': 3, 'id': 'MISSING', 'culprit': 'C03'},
                            {'day': 4, 'id': 'BUTTERFLY', 'culprit': 'C18'}, {'day': 6, 'id': 'HOSPITAL', 'culprit': 'C17'},
                            {'day': 7, 'id': 'REMOTE', 'culprit': 'C06'}]}
    init = {'C06': 'CIT', 'C07': 'CIT', 'C14': 'CIT', 'C18': 'CIT', 'C02': 'SCH', 'C03': 'SCH', 'C17': 'HOS', 'C09': 'HOS', 'C04': 'SHR'}
    s = {'loop': 1, 'day': 1, 'leader': 'A', 'script': script, 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')},
         'used': {p: [] for p in 'MABC'}, 'chars': {c: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0} for c, a in init.items()}}
    got = {(r['id'], r['via']) for r in enumerate_routes(s)}
    # ('Y_SEAL', 'C03'): 行方不明の犯人が神社へ動いて暗躍+1（神社の暗躍の一部を事件で補う筋。暗躍禁止では止まらない）
    assert got == {('Y_SEAL', None), ('Y_SEAL', 'C03'), ('HOSPITAL_PROT', 'C17'), ('HOSPITAL_KILL', 'C17'), ('REMOTE', 'C06')}


def test_unstoppable_supply_counts_but_using_it_now_is_better():
    """行動フェイズより後の増減（不穏な噂）は止められない供給として数える。ただし見込みは半分だけ効くので、
    今日使って実際に置いた方が距離は縮む（満額で数えると脚本家が能力を先送りする）。"""
    import copy
    from engine import routes
    if not routes.UNSTOPPABLE:
        return
    script = {'rules': ['Y_SEAL', 'X_RUMOR', 'X_FACTOR'], 'roles': {'C14': 'CULTIST', 'C07': 'KUROMAKU'}, 'incidents': [], 'days': 5}
    init = {'C07': 'CIT', 'C14': 'CIT', 'C04': 'SHR'}
    s = {'loop': 1, 'day': 1, 'leader': 'A', 'script': script, 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')},
         'used': {p: [] for p in 'MABC'}, 'chars': {c: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0} for c, a in init.items()}}
    s['used']['M'] = ['MV_D']  # クロマク（都市）が1回の移動で神社に入れないようにして、噂の分だけを見る
    seal = lambda st: next(r['total'] for r in enumerate_routes(st) if r['id'] == 'Y_SEAL')  # noqa: E731
    before = seal(s)
    assert before == 1.5  # 神社に暗躍2が必要、噂の1回を半分だけ見込む
    after = copy.deepcopy(s)
    after['boards']['SHR'] = 1
    after['ability_used_loop'] = ['X_RUMOR']
    assert seal(after) < before


def test_kuromaku_one_move_from_the_board_counts_as_supply():
    """クロマクが1回の移動で要のボードに入れるなら、止められない供給として（割り引いて）数える。移動斜めを使い切ると数えない。"""
    import copy
    from engine import routes
    if not routes.UNSTOPPABLE:
        return
    script = {'rules': ['Y_SEAL'], 'roles': {'C07': 'KUROMAKU'}, 'incidents': [], 'days': 5}
    s = {'loop': 1, 'day': 1, 'leader': 'A', 'script': script, 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')},
         'used': {p: [] for p in 'MABC'}, 'chars': {'C07': {'area': 'CIT', 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0}}}
    seal = lambda st: next(r['total'] for r in enumerate_routes(st) if r['id'] == 'Y_SEAL')  # noqa: E731
    far = copy.deepcopy(s)
    far['used']['M'] = ['MV_D']
    assert seal(s) < seal(far) == 2


def test_misleader_one_move_from_the_culprit_counts_as_supply():
    """犯人が1回の移動でミスリーダーのエリアに入れるなら、ミスリーダーの不安+1 を止められない供給として（割り引いて）数える。"""
    import copy
    from engine import routes
    if not routes.UNSTOPPABLE:
        return
    script = {'rules': ['Y_CONTRACT', 'X_RUMOR'], 'roles': {'C02': 'KEY', 'C06': 'MISLEADER'},
              'incidents': [{'day': 5, 'id': 'MURDER', 'culprit': 'C01'}], 'days': 5}
    init = {'C01': 'SCH', 'C02': 'SCH', 'C06': 'CIT'}
    s = {'loop': 1, 'day': 1, 'leader': 'A', 'script': script, 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')},
         'used': {p: [] for p in 'MABC'}, 'chars': {c: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0} for c, a in init.items()}}
    murder = lambda st: next(r['total'] for r in enumerate_routes(st) if r['id'] == 'MURDER')  # noqa: E731
    far = copy.deepcopy(s)
    far['chars']['C06']['area'] = 'HOS'  # 学校から斜め、移動斜めを使い切れば2回
    far['used']['M'] = ['MV_D']
    assert murder(s) < murder(far)


def test_sk_alone_with_key_picks_the_emptier_area():
    """キーを1回動かせばシリアルキラーと2人きり（s08 の実戦: 学校は混んでいて神社は2人きりにできる）。距離は1。"""
    script = {'rules': ['X_KILLER'], 'roles': {'C20': 'SK', 'C11': 'KEY'}, 'incidents': [], 'days': 6}
    ch = lambda a: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0}  # noqa: E731
    s = {'loop': 1, 'day': 2, 'leader': 'A', 'script': script, 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')},
         'used': {p: [] for p in 'MABC'}, 'chars': {'C20': ch('SHR'), 'C11': ch('SCH'), 'C03': ch('SCH'), 'C34': ch('SCH')}}
    assert min(r['total'] for r in enumerate_routes(s) if r['id'] == 'SK_KILL') == 1


def test_surplus_counts_one_extra_on_a_met_board_condition():
    """封印の神社が2なら余り0、3なら1（神格[5] で1つ取られても崩れない）。"""
    from selfplay.search_mm import surplus
    script = {'rules': ['Y_SEAL'], 'roles': {}, 'incidents': [], 'days': 5}
    s = {'loop': 1, 'day': 5, 'leader': 'A', 'script': script, 'boards': {'HOS': 0, 'SHR': 2, 'CIT': 0, 'SCH': 0},
         'used': {p: [] for p in 'MABC'}, 'chars': {}}
    assert surplus(s) == 0
    s['boards']['SHR'] = 3
    assert surplus(s) == 1


def test_hide_after_locked_loop():
    """蝶の羽ばたきが起きた（未来改変の負けが確定）ループでは、hide の脚本家は役職の任意能力を使わない。"""
    import random
    from selfplay.search_mm import SearchMastermind
    script = {'rules': ['Y_FUTURE'], 'roles': {'C06': 'KILLER'}, 'incidents': [], 'days': 5, 'loops': 3}
    ch = lambda a: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 2, 'guard': 0}  # noqa: E731
    s = {'loop': 1, 'day': 3, 'leader': 'A', 'script': script, 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')},
         'used': {p: [] for p in 'MABC'}, 'chars': {'C06': ch('CIT')},
         'incident_log': [{'loop': 1, 'id': 'BUTTERFLY', 'occurred': True}]}
    mm = SearchMastermind(random.Random(0), hide=10)
    assert mm._locked(s) and mm.killer_pick(s) == [] and mm.mm_ability(s, 'C06') is None
    s['incident_log'] = []
    assert not mm._locked(s)


def test_main_lovers_can_kill_in_the_same_turn_end_after_a_serial_killer_kill():
    """ユーザー裁定（2026-09-27）: ターン終了の処理順は脚本家の自由。シリアルキラーがラバーズを殺す→メインラバーズに不安6→
    同じターン終了フェイズにメインラバーズの能力で主人公死亡、ができる。"""
    import pytest
    from engine import phases as ph
    ch = lambda a, i=0: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': i, 'guard': 0}  # noqa: E731
    s = {'loop': 1, 'day': 2, 'leader': 'A', 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')}, 'used': {p: [] for p in 'MABC'},
         'script': {'rules': ['Y_MURDER', 'X_LOVE', 'X_KILLER'], 'roles': {'C01': 'SK', 'C02': 'LOVERS', 'C03': 'MAIN_LOVERS'},
                    'incidents': [], 'days': 5},
         'chars': {'C01': ch('SHR'), 'C02': ch('SHR'), 'C03': ch('SCH', 1), 'C05': ch('CIT')}}
    ph.turn_end_mandatory(s)
    assert not s['chars']['C02']['alive'] and s['chars']['C03']['par'] >= 6
    assert {'char': 'C03', 'ability': 'MAIN_LOVERS'} in ph.killer_options(s)
    with pytest.raises(ph.LoopEnd):
        ph.turn_end_optional(s, [{'char': 'C03', 'ability': 'MAIN_LOVERS'}])
