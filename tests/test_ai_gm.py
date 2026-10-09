"""対AIモードの進行役（ai_gm.py）の、盤面向けの合法な宣言の一覧と、盤面からの宣言の照合。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_gm  # noqa: E402


def _state():
    ch = lambda a, gw=0, par=0: {'area': a, 'alive': True, 'par': par, 'gw': gw, 'int': 0, 'guard': 0}  # noqa: E731
    return {'loop': 1, 'day': 1, 'leader': 'A', 'script': {'rules': [], 'roles': {}, 'incidents': []},
            'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')}, 'used': {p: [] for p in 'MABC'},
            'chars': {'C08': ch('HOS', gw=2), 'C19': ch('HOS', par=1), 'C25': ch('HOS'), 'C21': ch('CIT'), 'C04': ch('SHR', gw=3)},
            'ability_used_today': [], 'ability_used_loop': []}


def test_options_limit_targets_and_allow_no_arg_abilities():
    o = ai_gm.HumanProtagonist._options(_state())
    assert sorted(o['医者#0']['targets']) == ['学者', '軍人']  # 同じエリアの本人以外だけ
    assert '巫女#0' in o and o['巫女#0']['targets'] == []  # 引数の無い能力（以前は None で落ちた）


def test_match_no_arg_ability():
    assert ai_gm.HumanProtagonist._match(_state(), {'char': '巫女', 'ability': 0}) == ('C04', 0, None)


def test_describe_handles_every_event_from_random_games():
    """無作為の自己対戦で出る全ての出来事について、ログの1行が作れる（以前は行方不明の移動で落ちた）。"""
    import random
    from engine.scripts import by_id
    from selfplay.run import Combo, RandomPlayer, play_game
    kinds, errors = set(), []
    for sid in ('s01_seal', 's03_bomb', 's05_future', 's08_misfits', 'generated/gen_s11_029'):
        for g in range(4):
            def log0(kind, rec):
                for e in rec.get('events') or []:
                    if isinstance(e, dict):
                        kinds.add(e.get('kind'))
                        try:
                            ai_gm.describe(e)
                        except Exception as ex:
                            errors.append((e, repr(ex)))
            try:
                play_game(by_id(sid), Combo(RandomPlayer(random.Random(g)), RandomPlayer(random.Random(g + 7))), log0)
            except Exception:
                pass  # 無作為の手が不正になるのは試合側の都合
    assert not errors, errors[:3]
    assert {'move', 'incident', 'par', 'death'} <= kinds, kinds


def test_project_skips_characters_not_yet_on_the_board():
    """登場前のキャラクター（エリアが無い）は盤面に出さない（以前は KeyError で対AIモードが落ちた）。"""
    from engine.project import project
    s = _state()
    s['script'] = {'rules': [], 'roles': {}, 'incidents': []}
    s['chars']['C13'] = {'area': None, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0, 'present': False}
    names = [c['name'] for c in project(s)['characters']]
    assert '神格' not in names and '医者' in names


def test_transfer_ability_options_and_match():
    """鑑識官[2]★（同じエリアの2人の間でカウンター1つを移す）: 盤面に移す元・先・カウンターを出し、盤面の選択をその通りに照合する。
    以前は対象の一覧が空で、照合がエンジンの選択肢の先頭（意図しない向き・カウンター）になっていた（目隠し6本目）。"""
    s = _state()
    s['chars'].update({'C21': dict(s['chars']['C21'], gw=2), 'C27': {'area': 'CIT', 'alive': True, 'par': 0, 'gw': 0, 'int': 2, 'guard': 0},
                       'C18': {'area': 'CIT', 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0}})
    o = ai_gm.HumanProtagonist._options(s)['鑑識官#0']
    a, b = ai_gm.name('C27'), ai_gm.name('C18')
    assert a in o['froms'] and b in o['tos'] and '暗躍' in o['counters']  # カウンターの無い者は移す元にならない
    got = ai_gm.HumanProtagonist._match(s, {'char': '鑑識官', 'ability': 0, 'from': a, 'to': b, 'counter': '暗躍'})
    assert got == ('C21', 0, {'from': 'C27', 'to': 'C18', 'counter': 'int'})


def test_teacher_paranoia_ability_is_offered():
    """教師[3]（同一エリアの学生1人の不安を置く／取り除く）が宣言の候補に出る（Claude が脚本家の対戦で、ユーザーが使えなかった）。"""
    from engine import abilities as ab
    ch = lambda a, gw=0: {'area': a, 'alive': True, 'par': 1, 'gw': gw, 'int': 0, 'guard': 0}  # noqa: E731
    s = {'loop': 1, 'day': 1, 'leader': 'A', 'script': {'rules': [], 'roles': {}, 'incidents': []},
         'chars': {'C23': ch('SCH', 3), 'C01': ch('SCH')}, 'boards': {a: 0 for a in ('HOS', 'SHR', 'CIT', 'SCH')},
         'used': {p: [] for p in 'MABC'}}
    opts = [(c, i, a) for c, i, a in ab.declaration_options(s) if c == 'C23' and i == 0]
    assert {a['mode'] for _, _, a in opts} == {'place', 'remove'} and all(a['target'] == 'C01' for _, _, a in opts)


def test_ability_one_resolves_each_before_the_next():
    """ability_one を持つ主人公は、宣言1件ごとにエンジンが解決し、次の宣言の時点で前の結果が盤面に出ている（ユーザー指摘）。"""
    import random
    from engine.scripts import by_id
    from selfplay.run import Combo, RandomPlayer, play_game
    seen = []

    class OneByOne(RandomPlayer):
        def ability_one(self, s):
            # 同じ日の2回目の呼び出しで、1回目の解決が済んでいることを記録する
            seen.append((s['loop'], s['day'], list(s.get('ability_used_today', []))))
            if len([x for x in seen if x[:2] == (s['loop'], s['day'])]) > 1:
                return None
            from engine import abilities as ab
            for (cid, _), _v in ab.ABILITIES.items():  # 候補を作るため友好を足す（テストの都合）
                if cid in s['chars'] and s['chars'][cid]['alive'] and s['chars'][cid].get('present', True):
                    s['chars'][cid]['gw'] = 5
            c = ab.declaration_options(s)
            return c[0] if c else None
    try:
        play_game(by_id('s03_bomb'), Combo(RandomPlayer(random.Random(1)), OneByOne(random.Random(2))), lambda *a: None)
    except Exception:
        pass
    second = [x for i, x in enumerate(seen) if i and seen[i - 1][:2] == x[:2]]
    assert second and any(x[2] for x in second), seen[:6]


def test_incident_info_counts_bits_of_a_split():
    """犯人の候補4人のうち臨界の者が2人なら、発生・不発の観測で1ビット。全員か誰も臨界でなければ0。"""
    from selfplay.pc_v2 import incident_info
    ch = lambda par: {'area': 'SCH', 'alive': True, 'par': par, 'gw': 0, 'int': 0, 'guard': 0}  # noqa: E731
    after = {'chars': {'C01': ch(2), 'C02': ch(3), 'C10': ch(0), 'C23': ch(0)}}  # 臨界: 男子学生2・女子学生3・委員長2・教師2
    cands = {'MURDER@3': {'C01', 'C02', 'C10', 'C23'}}
    assert abs(incident_info(after, ['MURDER@3'], cands) - 1.0) < 1e-9
    assert incident_info(after, ['MURDER@3'], {'MURDER@3': {'C10', 'C23'}}) == 0.0
