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


def test_auto_protagonist_game_and_replay(tmp_path):
    """自動同士（--pc）で最後まで進み、記録（主人公の種類と乱数の種）から同じ試合を作り直せる（ユーザー 2026-10-17）。"""
    import json
    import ai_gm
    argv = sys.argv

    def run(*args):
        try:
            sys.argv = ['ai_gm.py', *args]
            ai_gm.main()
        finally:
            sys.argv = argv
    run('--pc', 'read', '--mm', 'search', '--script', 's03_bomb', '--seed', '5', '--dir', str(tmp_path / 'a'))
    rec = json.load(open(tmp_path / 'a' / 'record.json'))
    assert rec['pc'] == 'read' and rec['seed'] == 5 and rec['h'] == []
    run('--replay', str(tmp_path / 'a' / 'record.json'), '--dir', str(tmp_path / 'b'))
    log = lambda d: [json.loads(x)['text'] for x in open(tmp_path / d / 'log.jsonl', encoding='utf-8')]  # noqa: E731
    assert log('a') == log('b') and any(t.startswith('ゲーム終了') for t in log('a'))


def test_final_battle_judges_each_declaration(tmp_path):
    """最後の戦いは1人ずつ宣言 → その場で判定 → 正解なら次、外れたらそこで終わり（主人公の書 印刷p25 §6。不具合 B11）。"""
    import json
    import random
    from types import SimpleNamespace
    import ai_gm
    from engine.project import CARD_UI

    class SimTable(ai_gm.Table):
        rng = random.Random(5)
        said = 0

        def wait(self, types):
            if 'cards' in types:
                alive = [c for c, v in self.s['chars'].items() if v['alive'] and v.get('present', True) and c != 'C20']
                return {'type': 'cards', 'phaseId': self.phase_id, 'placements': [
                    {'actor': a, 'target': ai_gm.name(t), 'card': CARD_UI[k]} for a, t, k in zip('ABC', self.rng.sample(alive, 3), ('PAR+', 'PAR+', 'MV_H'))]}
            if 'final' in types:
                roles = self.s['script']['roles']
                c = sorted(roles)[0]
                if self.said == 0:  # 1人目は正解、2人目はわざと外す
                    self.said = 1
                    return {'type': 'final', 'phaseId': self.phase_id, 'char': ai_gm.name(c), 'role': ai_gm.ROLE_JA[roles[c]]}
                other = next(x for x in self.s['chars'] if x != c and roles.get(x, 'PERSON') == 'PERSON')
                return {'type': 'final', 'phaseId': self.phase_id, 'char': ai_gm.name(other), 'role': ai_gm.ROLE_JA[roles[c]]}
            return {'type': 'option', 'phaseId': self.phase_id, 'option': 'end'}
    d = str(tmp_path / 'a')
    ai_gm.run(SimpleNamespace(script='s01_seal', mm='route', seed=3, blind=False, dir=d, code=''), SimTable)
    log = [json.loads(x)['text'] for x in open(d + '/log.jsonl')]
    said = [t for t in log if t.startswith('宣言: ')]
    if not said:  # 主人公がループを守りきった（最後の戦いにならない）
        return
    assert said[0].endswith('― 正解') and said[1].endswith('― 不正解') and len(said) == 2
    assert any(t == '最後の戦い: 2 人目の宣言で外れた。脚本家の勝ち' for t in log)


def test_time_gap_can_skip_to_final_battle(tmp_path):
    """時の狭間で残りのループを飛ばして最後の戦いへ進める（主人公の書 印刷p20）。記録から再生しても同じ。"""
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
                    {'actor': a, 'target': ai_gm.name(t), 'card': CARD_UI[k]} for a, t, k in zip('ABC', self.rng.sample(alive, 3), ('PAR+', 'PAR+', 'MV_H'))]}
            if (self.waiting or {}).get('kind') == 'time_gap':
                return {'type': 'option', 'phaseId': self.phase_id, 'choice': ai_gm.GAP_FINAL}
            return {'type': 'option', 'phaseId': self.phase_id, 'option': 'end'}
    d1, d2 = str(tmp_path / 'a'), str(tmp_path / 'b')
    ai_gm.run(SimpleNamespace(script='s01_seal', mm='route', seed=3, blind=False, dir=d1, code=''), SimTable)
    log = lambda d: [json.loads(x)['text'] for x in open(d + '/log.jsonl')]  # noqa: E731
    if not any(t.startswith('時の狭間: ') for t in log(d1)):  # ループ1を守りきった
        return
    assert not any(t.startswith('── L2D1') for t in log(d1)) and any(t.startswith('最後の戦い: ') for t in log(d1))
    rec = json.load(open(d1 + '/record.json'))
    ai_gm.run(SimpleNamespace(script='', mm='', seed=None, blind=False, dir=d2, code='', replay=rec))
    assert log(d1) == log(d2)


def test_public_ev_has_no_secret_fields(tmp_path):
    """公開ログの ev（機械が読める出来事）に、役職の持ち主・理由などの秘匿の項目が載らない（設計資料 段階3）。"""
    import json
    from types import SimpleNamespace
    import ai_gm
    d = str(tmp_path / 'a')
    ai_gm.run(SimpleNamespace(script='s03_bomb', mm='search', pc='read', seed=7, blind=False, dir=d, code=''))
    evs = [json.loads(x).get('ev') for x in open(d + '/log.jsonl')]
    evs = [e for e in evs if e]
    assert evs and {e['kind'] for e in evs} >= {'move', 'incident'}
    for e in evs:
        assert not ({'actor', 'cause', 'by', 'culprit', 'reason'} & set(e)), e
        assert 'role' not in e or e['kind'] == 'role_public', e
        assert not any(isinstance(v, str) and len(v) == 3 and v[0] == 'C' and v[1:].isdigit() for v in e.values()), e  # カード番号でなく名前


def test_appeared_character_stays_on_the_sheet(tmp_path):
    """一度登場した人物（転校生など）は、次のループでボードから居なくなっても seenChars で推理の欄に残る（ユーザー 2026-10-22）。"""
    import json
    t = ai_gm.Table(str(tmp_path), {'loops': 3, 'days': 4, 'set': 'BTX', 'rules': [], 'roles': {}, 'incidents': []})
    s = _state()
    s['chars']['C13'] = {'area': 'SCH', 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0}
    t.show(s=s)
    st = lambda: json.load(open(tmp_path / 'state.json', encoding='utf-8'))  # noqa: E731
    assert st()['seenChars'] == []
    s['chars']['C13'] = dict(s['chars']['C13'], area=None, present=False)
    t.show(s=s)
    assert [c['name'] for c in st()['seenChars']] == ['神格'] and st()['seenChars'][0]['absent']


def test_ai_incident_choices_are_listed_and_matched():
    """A.I.[3]: 事件効果でリーダーが決めること（殺人事件の対象・不安拡大の2人・行方不明の先）を盤面に出し、選んだ通りに照合する。
    以前は殺人事件が対象名なしで同じ表示のまま並び（照合は先頭の対象）、不安拡大などは候補に無かった（ユーザー 2026-10-22）。"""
    s = _state()
    s['chars']['C22'] = {'area': 'CIT', 'alive': True, 'par': 0, 'gw': 3, 'int': 0, 'guard': 0}
    s['chars']['C27'] = {'area': 'CIT', 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0}
    s['script']['incidents'] = [{'id': 'MURDER', 'day': 3, 'culprit': 'C08'}, {'id': 'SPREAD', 'day': 2, 'culprit': 'C08'},
                                {'id': 'MISSING', 'day': 4, 'culprit': 'C08'}]
    ch = ai_gm.HumanProtagonist._options(s)['A.I.#0']['choices']
    labs = [c['label'] for c in ch]
    assert len(labs) == len(set(labs))  # 同じ表示が並ばない
    murder = [c for c in ch if '殺人事件' in c['label']]
    assert sorted(c['send']['choice']['target'] for c in murder) == ['C21', 'C27']
    assert any('不安拡大' in x for x in labs)
    assert [c['send']['choice'] for c in ch if '行方不明' in c['label']] == [{'board': 'CIT'}]  # A.I. の禁止エリアへは出さない
    pick = murder[-1]['send']
    got = ai_gm.HumanProtagonist._match(s, {'char': 'A.I.', 'ability': 0, **pick})
    assert got[2]['choice'] == pick['choice']


def test_every_ability_choice_round_trips():
    """どの友好能力でも、盤面に出す1件（表示名と送る中身）を選ぶと、照合がその宣言そのものに戻り、同じ能力の中で表示名が重ならない。
    A.I.[3] の事件の選択（2026-10-22）・妹[5] の使わせる能力のように、エンジンの引数の一部を送っていない能力を見つける水平展開。"""
    import random
    from engine import abilities as ab
    from engine.resolve import CHARS
    areas = ['HOS', 'SHR', 'CIT', 'SCH']
    seen = set()
    for seed in range(40):
        r = random.Random(seed)
        cast = r.sample(sorted(c for c in CHARS if c not in ('C33', 'C22', 'C31')), 8) + ['C22', 'C31']  # A.I.・妹は毎回入れる
        s = {'loop': 1, 'day': 2, 'leader': 'A',
             'script': {'rules': ['Y_SEAL', 'X_FRIENDS'], 'roles': {}, 'territory': 'CIT', 'set': 'BTX',
                        'incidents': [{'id': i, 'day': d, 'culprit': cast[0]} for d, i in enumerate(['MURDER', 'SPREAD', 'MISSING', 'REMOTE', 'RUMOR_SPREAD', 'BUTTERFLY', 'HOSPITAL'], 1)]},
             'boards': {a: r.randint(0, 2) for a in areas}, 'used': {p: ['PAR+1', 'GW+1'] for p in 'MABC'},
             'chars': {c: {'area': r.choice(areas[:2]), 'alive': r.random() > .15, 'par': r.randint(0, 3), 'gw': 8, 'int': r.randint(0, 3), 'guard': r.randint(0, 1)} for c in cast},
             'ability_used_today': [], 'ability_used_loop': []}
        labels = {}
        for c, i, a in ab.declaration_options(s, full=True):
            send, lab = ai_gm._lab_send(a or {})
            key = (c, i)
            assert '・'.join(lab) not in labels.setdefault(key, set()), (seed, ai_gm.name(c), i, lab)
            labels[key].add('・'.join(lab))
            got = ai_gm.HumanProtagonist._match(s, {'char': ai_gm.name(c), 'ability': i, **send})
            assert got == (c, i, a), (seed, ai_gm.name(c), i, a, got)
            seen.add(key)
    assert ('C22', 0) in seen and ('C31', 0) in seen and len(seen) > 30
    # 妹[5] は大人の友好能力1つを使わせる（カード）。対象を取らない能力（情報屋のルールの宣言など）も候補に出る
    s['chars'].update({'C31': dict(area='CIT', alive=True, par=0, gw=5, int=0, guard=0), 'C07': dict(area='CIT', alive=True, par=0, gw=0, int=0, guard=0)})
    assert any(c == 'C31' and a['target'] == 'C07' and 'declare' in (a['arg'] or {}) for c, i, a in ab.declaration_options(s, full=True))


class _Table:
    """盤面の代わり: 出した問い（ui）を記録し、用意した答えを順に返す。受け付けなかった答えは rejected に。"""
    def __init__(self, answers):
        self.answers, self.asked, self.rejected, self.reply, self.s = list(answers), [], [], '', None

    def show(self, s, title, w, **kw):
        self.asked.append(w)

    def take(self, kinds):
        return {'type': 'mm', 'choice': self.answers.pop(0)}

    def reject(self, what, why):
        self.rejected.append(why)


def _human(answers):
    t = _Table(answers)
    return ai_gm.HumanMastermind(t, None), t


def test_human_mastermind_doctor_tree_rule_x_and_gained_ability():
    s = _state()
    s['script']['roles'] = {'C08': 'KUROMAKU', 'C30': 'KUROMAKU', 'C25': 'FACTOR'}
    s['chars']['C30'] = {'area': 'CIT', 'alive': True, 'par': 0, 'gw': 1, 'int': 0, 'guard': 0}
    # 医者[友好2]: 同エリアの医者以外が対象。反則（自分）は聞き直し、使わない（{}）も可
    h, t = _human([{'target': 'C08', 'mode': 'place'}, {'target': 'C19', 'mode': 'remove'},
                   {'target': 'C21', 'counter': 'par'}, {'target': 'C21', 'counter': 'gw'}])
    uses = h.mm_extra_uses(s)
    assert [x['value'] for x in t.asked[0]['ui']['fields'][0]['options']] == ['C19', 'C25']
    assert [x['value'] for x in t.asked[-1]['ui']['fields'][1]['options']] == ['gw']  # 木の上にあるカウンターだけ
    assert len(t.rejected) == 2  # 医者自身・木に無い不安
    assert uses == [{'char': 'C08', 'ability': 'DOCTOR', 'target': 'C19', 'mode': 'remove'},
                    {'char': 'C30', 'ability': 'SHINBOKU', 'target': 'C21', 'counter': 'gw'}]
    h, t = _human([{}])
    s['chars']['C30']['gw'] = 0
    assert h.mm_extra_uses(s) == []
    # ルールX の選び方
    h, t = _human([{'rule': 'X_B'}])
    assert h.choose_rule_x(s, ['X_A', 'X_B']) == 'X_B'
    # 得た能力（ファクターのクロマク）ではボードも対象に出る
    h, t = _human([{'target': 'B:HOS'}])
    assert h.mm_ability(s, 'C25', 'KUROMAKU') == 'B:HOS'
    h, t = _human([{'target': 'C19'}])
    h.mm_ability(s, 'C25', 'MISLEADER')
    assert not any(x['value'].startswith('B:') for x in t.asked[0]['ui']['fields'][0]['options'])


def test_human_mastermind_rumor_answers_a_board():
    """不穏な噂の答えはエンジンの形（'B:HOS'）。以前は 'HOS' を返して進行役ごと落ちた。"""
    from engine import phases as ph
    s = _state()
    s['script']['rules'] = ['X_RUMOR']
    h, t = _human([{'board': 'B:HOS'}])
    b = h.mm_rumor(s)
    ph.rumor(s, b)
    assert s['boards']['HOS'] == 1


def test_human_mastermind_chooses_informant_rule_x():
    """情報屋で脚本に無いルールXを宣言すると、残る2つのどちらを公開するかを人の脚本家に聞く（run._use の経路）。"""
    from engine.scripts import by_id
    from selfplay.run import _use
    from selfplay.script_routes import start_state
    s = start_state(by_id('s01_seal'))  # ルールX: X_THREAD・X_VIRUS
    s['chars']['C07']['gw'] = 5
    h, t = _human([{'rule': 'X_LOVE'}, {'rule': 'X_VIRUS'}])
    ev = _use(s, h, 'C07', 0, {'declare': 'X_LOVE'}, False)
    assert sorted(x['value'] for x in t.asked[0]['ui']['fields'][0]['options']) == ['X_THREAD', 'X_VIRUS']
    assert len(t.rejected) == 1  # 選択肢に無いルールは聞き直し
    assert any(e.get('reveal_rule_x') == 'X_VIRUS' for e in ev)
