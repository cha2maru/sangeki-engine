"""推理の回帰試験（敵対的レビュー10）: 行列で持つ推理（engine/deduce.Deduction）が、仮説を1件ずつ判定する素朴な実装
（行列化の前の _consistent を写したもの）と、同じ観測の列に対して同じ仮説の集合を残すことを確かめる。
「真の配役が消えない」（test_deduce）だけでは、絞り込みを弱めても通ってしまうため。"""
import os
import pytest
import random
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
from engine.deduce import ABSOLUTE, REFUSERS, Deduction  # noqa: E402
from selfplay.players import DeductiveBlocker, HiddenMastermind, RouteMastermind  # noqa: E402
from selfplay.run import TOUKOU001, Combo, play_game  # noqa: E402
pytestmark = pytest.mark.skipif(TOUKOU001 is None, reason='toukou_001（他の方の投稿シナリオ）は公開版に無い')


def reference_consistent(combo, roles, obs):
    role = lambda c: roles.get(c, 'PERSON')  # noqa: E731
    for kind, kw in obs:
        if kind == 'rule_x_revealed' and kw['rule'] not in combo:
            return False
        if kind == 'role_revealed':
            r, c = kw['role'], kw['char']
            # 妄想拡大ウイルスでパーソンがシリアルキラーとして明かされることがある
            if not (role(c) == r or (r == 'SK' and role(c) == 'PERSON' and 'X_VIRUS' in combo)):
                return False
        if kind == 'refused' and role(kw['char']) not in REFUSERS:
            return False
        if kind == 'not_refused' and role(kw['char']) in ABSOLUTE:
            return False
        if kind == 'mm_effect':
            present = kw['present']  # そのとき、そのエリアにいた生存キャラクター
            want = {'int': {'KUROMAKU'}, 'par': {'MISLEADER'}}[kw['counter']]
            ok = any(role(c) in want for c in present)
            if kw['counter'] == 'par' and not ok:
                ok = kw.get('school_int', 0) >= 2 and any(role(c) == 'FACTOR' for c in present)
            if kw['counter'] == 'int' and not ok and kw.get('rumor_possible') and 'X_RUMOR' in combo:
                ok = True  # 不穏な噂（1ループ1回、任意のボード）
            if not ok:
                return False
        if kind == 'loop_start_par' and 'X_THREAD' not in combo:
            return False  # ループ開始時に不安が置かれるのは因果の糸だけ（早見表 ルールX）
        if kind == 'turn_end_death':
            # ターン終了フェイズの死亡: 同一エリアにキラー（被害者がキーパーソンで暗躍2以上）か、
            # シリアルキラー（被害者と2人きり。配役、または妄想拡大ウイルスで不安3以上のパーソン）がいた
            v, others = kw['victim'], kw['others']
            ok = False
            for c in others:
                if role(c) == 'KILLER' and role(v) in ('KEY', 'FACTOR') and kw['victim_int'] >= 2:
                    ok = True
                if len(others) == 1 and (role(c) == 'SK' or (role(c) == 'PERSON' and 'X_VIRUS' in combo and kw['par'].get(c, 0) >= 3)):
                    ok = True
            if not ok:
                return False
        if kind == 'key_death':
            # 同時に複数が死んだとき、どれがキーパーソンかは公開情報では分からない。死んだ者の誰かがキー（またはキーの能力を持つファクター）
            cs = kw.get('chars') or [kw['char']]
            if not any(role(c) == 'KEY' or (role(c) == 'FACTOR' and kw.get('city_int', 0) >= 2) for c in cs):
                return False
        if kind == 'no_sk':
            for c in kw['chars']:
                if role(c) == 'SK' or (role(c) == 'PERSON' and 'X_VIRUS' in combo and kw['par'].get(c, 0) >= 3):
                    return False
        if kind == 'intx_ignored' and not any(role(c) == 'CULTIST' for c in kw['present']):
            return False
        if kind == 'no_death' and role(kw['char']) != 'TT':
            return False
        if kind == 'lovers_par' and {role(kw['char']), role(kw['dead'])} != {'LOVERS', 'MAIN_LOVERS'}:
            return False
        if kind == 'tt_loss' and not any(role(c) == 'TT' and g <= 2 for c, g in kw['gw'].items()):
            return False
        if kind == 'loop_end_check':
            b = kw['boards']
            cond = ('Y_SEAL' in combo and b['SHR'] >= 2) or ('Y_FUTURE' in combo and kw.get('butterfly'))
            cond = cond or any(i >= 2 and 'Y_CONTRACT' in combo and role(c) == 'KEY' for c, i in kw['ints'].items())
            cond = cond or any('Y_BOMB' in combo and role(c) == 'WITCH' and b.get(kw['init'].get(c), 0) >= 2 for c in kw['ints'])
            cond = cond or any(role(c) == 'FRIEND' for c in kw['dead'])
            if bool(cond) != kw['loss']:
                return False
        if kind == 'prot_death_turn_end':
            if not any((role(c) == 'KILLER' and i >= 4) or (role(c) == 'MAIN_LOVERS' and i >= 1 and p >= 3)
                       for c, (i, p) in kw['counters'].items()):
                return False
    return True


def test_matrix_deduction_matches_reference():
    for seed, MM in ((21, RouteMastermind), (22, HiddenMastermind)):
        rng = random.Random(seed)
        pc = DeductiveBlocker(rng)
        play_game(TOUKOU001, Combo(MM(rng, samples=20), pc), lambda *a: None)
        obs = pc.ded.obs
        assert obs
        # 観測の列の途中（最初の1/3）でも比べる。全仮説の素朴な判定は重いので、行列側で途中まで絞った集合を起点にする
        for cut in (len(obs) // 3, len(obs)):
            d = Deduction(pc.ded.chars)
            for k, kw in obs[:max(1, cut // 2)]:
                d.observe(k, **kw)
            start = d.hypotheses()
            if len(start) > 500000:
                continue
            for k, kw in obs[max(1, cut // 2):cut]:
                d.observe(k, **kw)
            got = {(tuple(sorted(cb)), tuple(sorted(r.items()))) for cb, r in d.hypotheses()}
            want = {(tuple(sorted(cb)), tuple(sorted(r.items()))) for cb, r in start
                    if reference_consistent(cb, r, obs[max(1, cut // 2):cut])}
            assert got == want, f'seed {seed} cut {cut}: 行列 {len(got)} 件 / 素朴 {len(want)} 件'
