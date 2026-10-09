"""推理の健全性: 自己対戦で主人公が集めた観測（公開情報だけ）を与えても、本当のルールと配役は
「観測と矛盾しない仮説」から消えない。期待値を人が書く必要のない性質なので、自己追認にならない。"""
import os
import pytest
import random
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from selfplay.players import DeductiveBlocker, DeductiveProtagonist, HiddenMastermind, RouteMastermind  # noqa: E402
from selfplay.run import TOUKOU001, Combo, play_game  # noqa: E402
pytestmark = pytest.mark.skipif(TOUKOU001 is None, reason='toukou_001（他の方の投稿シナリオ）は公開版に無い')


def test_true_script_survives_all_observations():
    # 相手と主人公を変えて、観測の種類（シリアルキラーの不在・主人公死亡・拒否されなかった など）が一通り出るようにする
    for seed, MM, PC in ((11, RouteMastermind, DeductiveProtagonist), (12, RouteMastermind, DeductiveProtagonist),
                         (13, HiddenMastermind, DeductiveBlocker), (14, RouteMastermind, DeductiveBlocker)):
        rng = random.Random(seed)
        pc = PC(rng)
        play_game(TOUKOU001, Combo(MM(rng, samples=20), pc), lambda *a: None)
        assert pc.ded is not None and pc.ded.obs, '観測が1つも無い'
        truth = (tuple(TOUKOU001['rules']), TOUKOU001['roles'])
        combo_ok = [h for h in pc.ded.hypotheses() if set(h[0]) == set(truth[0])]
        assert any(h[1] == truth[1] for h in combo_ok), f'seed {seed}: 本当の配役が仮説から消えた（観測 {len(pc.ded.obs)} 件）'


def test_card_traits_constrain_casting():
    """A.I.【特性】パーソンにできない、妹【特性】友好無視を持つ役職にできない（カードの特性。公開情報）。"""
    from engine.deduce import REFUSERS, Deduction
    d = Deduction(['C22', 'C31', 'C01', 'C02', 'C03', 'C04', 'C05'])
    m, tot = d.marginals()
    assert m['C22'].get('PERSON', 0) == 0
    assert all(m['C31'].get(r, 0) == 0 for r in REFUSERS)
    assert tot > 0


def test_gwx_stopping_goodwill_rules_out_time_traveller():
    """友好禁止で友好が止まった者はタイムトラベラーではない（タイムトラベラーは友好禁止を無視する。早見表）。"""
    from selfplay.players import PublicObserver
    s = {'loop': 1, 'day': 1, 'script': {'set': 'BTX', 'rules': [], 'incidents': []},
         'chars': {c: {'area': 'SCH', 'alive': True, 'par': 0, 'gw': 0, 'int': 0} for c in ('C01', 'C02', 'C03', 'C04', 'C05')}}
    o = PublicObserver()
    o.observe(s, 'actions', [{'kind': 'nullified', 'target': 'C05', 'cards': ['GW1'], 'by': 'GWX'}])
    m, _ = o.ded.marginals()
    assert m['C05'].get('TT', 0) == 0 and any(m[c].get('TT', 0) > 0 for c in ('C01', 'C02'))


def test_dead_without_role_reveal_is_not_friend():
    """ループ終了時、死亡したフレンドは正体が公開される。公開されなかった死者はフレンドではない（早見表 フレンド、FAQ Ru02）。"""
    from engine.deduce import Deduction
    d = Deduction(['C01', 'C02', 'C03', 'C04', 'C05', 'C06'])
    kw = dict(loss=True, boards={'HOS': 0, 'SHR': 0, 'CIT': 0, 'SCH': 0}, ints={}, dead=['C01'], butterfly=False, init={})
    d.observe('loop_end_check', **kw)
    m, tot = d.marginals()
    assert m['C01'].get('FRIEND', 0) == 0
    d2 = Deduction(['C01', 'C02', 'C03', 'C04', 'C05', 'C06'])
    d2.observe('loop_end_check', revealed=['C01'], **kw)
    assert d2.marginals()[0]['C01'].get('FRIEND', 0) > 0


def test_contract_key_is_a_girl():
    """僕と契約しようよ！では必ず少女がキーパーソン（早見表）。少女でない者は、契約の世界ではキーパーソンにならない。"""
    from engine.deduce import Deduction
    from engine.resolve import CHARS
    chars = ['C01', 'C02', 'C03', 'C04', 'C05', 'C06']
    d = Deduction(chars)
    R, C = d._sync()
    import numpy as np
    from engine.deduce import COMBOS, RC
    contract = np.array(['Y_CONTRACT' in COMBOS[i] for i in C])
    for j, c in enumerate(chars):
        if '少女' not in CHARS[c]['tags']:
            assert not ((R[:, j] == RC['KEY']) & contract).any(), c
