"""負け筋の逆算（engine/route_calc.solve）を、手で数えられる小さな盤面で確かめる。"""
from engine.route_calc import solve

USED = (False, False, False)  # 主人公3人とも不安−1 を使い切った
FREE = (True, True, True)


def test_par_alone_needs_pc_out_of_par_minus():
    t, need = [('C01', 'par')], [('turn_end', ((0, 3),))]
    assert solve(t, [0], need, 1, 3, USED, True)[0]          # 1日1枚で3日
    assert not solve(t, [0], need, 2, 3, USED, True)[0]      # 2日では足りない
    assert solve(t, [0], need, 1, 6, FREE, True)[0]          # 6枚置けば、3枚打ち消されても3
    assert not solve(t, [0], need, 1, 5, FREE, True)[0]


def test_int_two_choice_needs_int2():
    t = [('B:CIT', 'int'), ('B:SCH', 'int')]
    need = [('turn_end', ((0, 2),)), ('turn_end', ((1, 2),))]
    ok, move = solve(t, [0, 1], need, 1, 1, FREE, True)      # 暗躍+2 と 暗躍+1 を別々に。暗躍禁止は1か所だけ
    assert ok and sorted(c for _, c in move) == ['INT1', 'INT2']
    assert not solve(t, [0, 0], need, 1, 1, FREE, True)[0]
    assert not solve(t, [0, 1], need, 1, 1, FREE, False)[0]  # 暗躍+2 を使った後


def test_incident_only_on_its_day():
    t, need = [('C02', 'par')], [(2, ((0, 2),))]
    assert solve(t, [0], need, 1, 8, USED, True)[0]
    assert not solve(t, [0], need, 1, 8, FREE, True)[0]
    assert not solve(t, [0], need, 3, 8, USED, True)[0]      # 事件の日を過ぎた


def test_one_card_per_character():
    t = [('C03', 'par'), ('C03', 'int')]                     # 同じ人物の不安と暗躍を同じ日に押せない
    need = [('turn_end', ((0, 1), (1, 1)))]
    assert not solve(t, [0, 0], need, 1, 1, USED, True)[0]
    assert not solve(t, [1, 0], need, 1, 3, USED, True)[0]   # 暗躍が1か所だけなら、毎日暗躍禁止で止まる


def test_par_surplus_survives_par_minus():
    t, need = [('C12', 'par')], [('turn_end', ((0, 2),))]
    assert solve(t, [4], need, 1, 1, FREE, True)[0]          # 不安4: 不安−1 を1枚置かれても2以上残る
    assert not solve(t, [1], need, 1, 1, FREE, True)[0]      # 不安1: 不安+1 と不安−1 で1のまま


def test_idle_means_locked():
    t, need = [('C12', 'par')], [(5, ((0, 2),))]
    assert solve(t, [3], need, 4, 6, FREE, True)[0]                  # 明日 不安+1 を置けば取れる
    assert not solve(t, [3], need, 4, 6, FREE, True, idle=True)[0]  # 何も置かなければ2日で不安1
    assert solve(t, [3], need, 4, 6, FREE, True)[1] == [('C12', 'PAR+')]  # 今日も押して余裕を持つ
    assert solve(t, [5], need, 4, 6, FREE, True, idle=True)[0]


def test_hidden_two_choice_is_a_coin_flip():
    from engine.route_calc import solve_hidden
    t = [('B:CIT', 'int'), ('C34', 'int')]
    need = [('turn_end', ((0, 2),)), ('turn_end', ((1, 2),))]
    p, mix = solve_hidden(t, [0, 0], need, 1, 1, FREE, True)
    assert abs(p - 0.5) < 1e-6                                  # 暗躍+2 をどちらに置いたか見えない → 五分五分
    assert {c for _, mv in mix for _, c in mv} >= {'INT2'}
    assert solve_hidden(t, [0, 0], need, 1, 1, FREE, False)[0] == 0  # 暗躍+2 を使った後は取れない


def test_block_rates_points_at_the_int2_threat():
    from engine.route_calc import solve_hidden
    t = [('B:CIT', 'int'), ('C34', 'int')]
    need = [('turn_end', ((0, 2),)), ('turn_end', ((1, 2),))]
    r = solve_hidden(t, [1, 0], need, 1, 1, FREE, True, fixed_T=['B:CIT', 'C99'])  # 伏せ札は都市と関係の無い人物
    assert r[(frozenset(), 'B:CIT')][0] == 0.0     # 都市に暗躍禁止なら取られない
    assert r[(frozenset(), None)][0] == 1.0        # 止めなければ暗躍+1 で都市2
