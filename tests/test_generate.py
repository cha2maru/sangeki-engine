

def test_future_plan_has_butterfly():
    """未来改変プランの脚本には、敗北条件の蝶の羽ばたきを必ず入れる。"""
    import random
    from engine.generate import generate
    rng, seen = random.Random(7), 0
    for _ in range(400):
        sc = generate(rng, rules=['Y_FUTURE', 'X_VIRUS', 'X_THREAD'])
        if sc:
            seen += 1
            assert any(i['id'] == 'BUTTERFLY' for i in sc['incidents']), sc['incidents']
    assert seen
