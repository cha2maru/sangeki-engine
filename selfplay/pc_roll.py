"""脚本を知る主人公に、日ごとの先読み（ロールアウト）をさせる（ユーザー「脚本を知っている主人公がループ1で勝てないのは、
ルートがないからか、ロジックが弱いからかを分析したい」）。

日の始めに、脚本家の今日の伏せ札（本物の脚本家の写しで引く＝伏せ札の中身まで知る上限）を固定し、主人公の候補の上位 k 手を
それぞれ n 回ループの終わりまで回して、脚本家がループを取った割合が最も低い手を今日の手にする（branch_pc.rollout を使う）。
先読みしても守れないなら、そのループに勝ち筋が無い見込みが高い。守れるなら、ふだんの主人公の打ち方が弱い。
ponytail: 上位 k 手の外に勝ち筋がある場合は見落とす（k を増やせば広がる）。既定はループ1だけ（loops=(1,)）。
"""
import copy

from selfplay.branch_pc import rollout


def plan_day(s, player, loop, day, script, k=4, n=1, loops=(1,)):
    if loop not in loops:
        return
    mm = copy.deepcopy(player.mm).mm_cards(copy.deepcopy(s))  # 本物の脚本家が今日選ぶ伏せ札（乱数の状態ごと写す）
    order = ['A', 'B', 'C']
    i = order.index(s['leader'])
    porder = order[i:] + order[:i]
    pcc = copy.deepcopy(player.pc)
    pcc.debug, pcc.last_debug = True, []
    pcc.pc_cards(copy.deepcopy(s), porder, [x['target'] for x in mm])
    cands = sorted(pcc.last_debug, key=lambda r: r[0])[:k]
    if not cands:
        return
    best, best_v = None, None
    for r, (_, _, _, cards) in enumerate(cands):
        pc = [{'by': b, 'target': t, 'card': c} for t, c, b in cards]
        v = sum(rollout(s, player, loop, day, mm, pc, script['days'], reseed=None if j == 0 else f'{loop}:{day}:{r}:{j}')[0]
                for j in range(n)) / n
        if best_v is None or v < best_v:
            best, best_v = pc, v
        if v == 0:
            break
    orig, used = player.pc.pc_cards, []

    def once(*a, **kw):
        if not used:
            used.append(1)
            player.pc.pc_cards = orig
            return [dict(x) for x in best]
        return orig(*a, **kw)
    player.pc.pc_cards = once
