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


def test_record_replays_to_the_same_game(tmp_path):
    """リプレイの短い URL（#p=）: 主人公の操作と脚本家の判断の記録（record.json）から再生すると、同じ公開ログ・盤面になる。"""
    import json
    import random
    from types import SimpleNamespace
    import ai_gm
    from engine.project import CARD_UI

    class SimTable(ai_gm.Table):  # 人の代わり（無作為の3人に友好+1・不安+1・移動←→、能力・指摘はしない）
        rng = random.Random(5)

        bad = True

        def wait(self, types):
            if 'cards' in types and self.bad:  # 最初に1回、反則の置き方（同じ人物に2枚）を送る＝受け付けなかった操作の記録
                self.bad = False
                return {'type': 'cards', 'phaseId': self.phase_id, 'placements': [
                    {'actor': a, 'target': ai_gm.name('C01'), 'card': CARD_UI['GW1']} for a in 'ABC']}
            if 'cards' in types:
                alive = [c for c, v in self.s['chars'].items() if v['alive'] and v.get('present', True) and c != 'C20']
                return {'type': 'cards', 'phaseId': self.phase_id, 'placements': [
                    {'actor': a, 'target': ai_gm.name(t), 'card': CARD_UI[k]} for a, t, k in zip('ABC', self.rng.sample(alive, 3), ('GW1', 'PAR+', 'MV_H'))]}
            return {'type': 'option', 'phaseId': self.phase_id, 'option': 'end'}
    d1, d2 = str(tmp_path / 'a'), str(tmp_path / 'b')
    ai_gm.run(SimpleNamespace(script='s01_seal', mm='route', seed=3, blind=False, dir=d1, code=''), SimTable)
    rec = json.load(open(d1 + '/record.json'))
    ai_gm.run(SimpleNamespace(script='', mm='', seed=None, blind=False, dir=d2, code='', replay=rec))
    log = lambda d: [json.loads(x)['text'] for x in open(d + '/log.jsonl')]  # noqa: E731
    assert log(d1) == log(d2) and json.load(open(d2 + '/state.json'))['waiting']['ok'] is True
    rej = lambda d: json.load(open(d + '/rejected.json'))  # noqa: E731
    assert len(rej(d1)) == 1 and rej(d1) == rej(d2) and rej(d1)[0]['what'].startswith('札:')
    bel = [json.loads(x) for x in open(d1 + '/beliefs.jsonl')]  # ループ終了ごとに推理エンジンの割合を残す（主人公の印は notes.json があれば）
    assert [b['loop'] for b in bel] == list(range(1, len(bel) + 1)) and all(b['deduce'] for b in bel)


def test_resume_from_the_middle(tmp_path):
    """途中から再開する URL: 記録を公開ログの n 行目の手前で切って再開すると、そこまでは同じ試合で、その先は普通に続く（盤面からの操作を待つ）。"""
    import json
    import random
    from types import SimpleNamespace
    import ai_gm
    from engine.project import CARD_UI

    class SimTable(ai_gm.Table):
        rng = random.Random(5)

        def wait(self, types):
            if 'cards' in types:
                alive = [c for c, v in self.s['chars'].items() if v['alive'] and v.get('present', True) and c != 'C20']
                return {'type': 'cards', 'phaseId': self.phase_id, 'placements': [
                    {'actor': a, 'target': ai_gm.name(t), 'card': CARD_UI[k]} for a, t, k in zip('ABC', self.rng.sample(alive, 3), ('GW1', 'PAR+', 'MV_H'))]}
            return {'type': 'option', 'phaseId': self.phase_id, 'option': 'end'}
    d1, d2 = str(tmp_path / 'a'), str(tmp_path / 'b')
    ai_gm.run(SimpleNamespace(script='s01_seal', mm='route', seed=3, blind=False, dir=d1, code=''), SimTable)
    rec = json.load(open(d1 + '/record.json'))
    cut = rec['h'][4]['n']  # 5回目の操作の時点
    part = dict(rec, h=[x for x in rec['h'] if x['n'] < cut], m=[x for x in rec['m'] if x[0] < cut])
    SimTable.rng = random.Random(99)
    ai_gm.run(SimpleNamespace(script='', mm='', seed=1, blind=False, dir=d2, code='', replay=part, resume=True), SimTable)
    log = lambda d: [json.loads(x)['text'] for x in open(d + '/log.jsonl')]  # noqa: E731
    assert log(d1)[:cut] == log(d2)[:cut] and 'ゲーム終了' in json.load(open(d2 + '/state.json'))['phaseName']
    assert len(json.load(open(d2 + '/record.json'))['h']) > len(part['h'])  # 再開した後の操作も記録に続く


def test_class_rep_returns_the_chosen_card():
    """委員長[2]: 盤面で選んだ札（表示名）が戻る。使用済みが2枚のとき、以前は先頭の札（移動禁止）が戻っていた。"""
    s = _state()
    s['chars']['C10'] = {'area': 'SCH', 'alive': True, 'par': 0, 'gw': 2, 'int': 0, 'guard': 0}
    s['used']['A'] = ['MVX', 'PAR-']
    rec = {'type': 'ability', 'char': '委員長', 'ability': 0, 'card': '不安-1'}
    assert ai_gm.HumanProtagonist._match(s, rec) == ('C10', 0, {'card': 'PAR-'})
    assert ai_gm.HumanProtagonist._match(s, dict(rec, card='移動禁止')) == ('C10', 0, {'card': 'MVX'})
    assert '不安-1 を手札に戻した' in ai_gm.describe({'kind': 'ability', 'char': 'C10', 'ability': 0, 'returned': 'PAR-'})


def test_list_scripts_puts_recommended_first_without_private():
    """開始画面の一覧: おすすめ（選定した設計の脚本）が先頭のまとまり。非公開の脚本（投稿・有志サイト）は出さない。"""
    import ai_gm
    rows = ai_gm.list_scripts()
    rec = [r['id'] for r in rows if r['group'] == 'おすすめ']
    assert rec == [i for i in ai_gm.RECOMMENDED if i in {r['id'] for r in rows}] and rows[0]['group'] == 'おすすめ'
    assert len(rec) == len({r['id'] for r in rows})  - len([r for r in rows if r['group'] != 'おすすめ'])  # 二重に出ない
    assert not any(r['id'].startswith(('posted/', 'bgw/')) for r in rows)


def test_loop_end_phase_header_comes_before_the_death(tmp_path):
    """ループがターン終了フェイズで終わったとき、その見出しを死亡より前に出す（報告 2026-10-17、d02_traits:
    遠隔殺人で従者がお嬢様の身代わりに死に、ターン終了に教師＝シリアルキラーがお嬢様を殺した。見出しが無く、同時に死んだように見えた）。"""
    import json
    import os
    import pytest
    import ai_gm
    from engine.scripts import glob_scripts
    if not glob_scripts('designed/d02_traits.json'):
        pytest.skip('設計した脚本（sangeki-scripts）が無い')
    rec = os.path.join(os.path.dirname(__file__), 'cases', 'replay_d02_servant_sk.json')
    argv = sys.argv
    try:
        sys.argv = ['ai_gm.py', '--replay', rec, '--dir', str(tmp_path)]
        ai_gm.main()
    finally:
        sys.argv = argv
    log = [json.loads(x)['text'] for x in open(tmp_path / 'log.jsonl', encoding='utf-8')]
    i = log.index('従者 が死亡した')
    assert log[i + 1] == '── L1D5 ターン終了フェイズ ──' and log[i + 2] == 'お嬢様 が死亡した'


def test_pick_blind_prefers_unplayed_recommended(tmp_path, monkeypatch):
    """伏せて選ぶ（開始画面）は、おすすめのうち遊んでいない脚本から。遊び尽くしたら自動生成から（ユーザー 2026-10-17）。"""
    import json
    import random
    import pytest
    import ai_gm
    from engine.scripts import glob_scripts
    rec = [i for i in ai_gm.RECOMMENDED if glob_scripts(i + '.json')]
    if not rec or not glob_scripts('generated/*.json'):
        pytest.skip('設計・生成した脚本（sangeki-scripts）が無い')
    hist = tmp_path / 'h.jsonl'
    monkeypatch.setattr(ai_gm, 'HISTORY', str(hist))
    hist.write_text(''.join(json.dumps({'id': i.split('/')[-1], 'rules': []}) + '\n' for i in rec[1:]))
    assert ai_gm.pick_blind(random.Random(0)) == (rec[0], 1)          # 遊んでいないおすすめは1本だけ
    with open(hist, 'a') as f:
        f.write(json.dumps({'id': rec[0].split('/')[-1], 'rules': []}) + '\n')
    assert ai_gm.pick_blind(random.Random(0))[0].startswith('generated/')  # 遊び尽くしたら自動生成
    assert ai_gm.pick_blind(random.Random(0), pool='generated')[0].startswith('generated/')
