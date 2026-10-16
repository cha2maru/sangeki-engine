"""目隠し対戦（play_cli --serve）の公開ログと Claude の決定から、試合の記録（one_game.py と同じ jsonl）を作り直す。

    cd play && python -m selfplay.blind_relog <脚本 id> <seed> <opp> <決定 d.jsonl> <public_log.txt> <出力.jsonl>

再生（脚本家を同じ seed で動かし直す）は乱数がずれて別の試合になる（目隠し6戦目）。脚本家の札は解決後に公開されるので、
公開ログの「脚本家の札」をそのまま置く脚本家で辿り直す。札以外の脚本家の決定（能力の対象・殺人事件の被害者など）は opp の型が決める。
ponytail: 札以外の決定が実際と違えば、その後の盤面がずれうる。出来事の行が公開ログと一致するかを最後に数えて出す。
これからの目隠し対戦は play_cli --serve が private/full_log.jsonl を書くので、この道具は過去の試合用。"""
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_gm import CARD_UI, AREA_JA  # noqa: E402
from engine.resolve import CHARS  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.play_cli import ClaudeProtagonist, load_slots  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402

CARD = {v: k for k, v in CARD_UI.items()}
BOARD = {v: 'B:' + k for k, v in AREA_JA.items()}


def mm_cards_from_log(text, chars):
    by_name = {CHARS[c]['name']: c for c in chars}
    out = {}
    for m in re.finditer(r'出来事（L(\d+)D(\d+) actions）: 脚本家の札: ([^／\n]+)', text):
        cards = []
        for part in m[3].strip().split('、'):
            who, card = part.split('=')
            cards.append({'by': 'M', 'target': BOARD.get(who) or by_name[who], 'card': CARD[card]})
        out[(int(m[1]), int(m[2]))] = cards
    return out


def main(sid, seed, opp, dpath, lpath, out):
    sc = by_id(sid)
    rng = random.Random(int(seed))
    text = open(lpath, encoding='utf-8').read()
    logged = mm_cards_from_log(text, sc['init'])
    mm = make(opp, rng, sc)

    def cards(s):
        return logged[(s['loop'], s['day'])]
    mm.mm_cards = cards
    slots = load_slots(dpath)
    pc = ClaudeProtagonist([slots[k] for k in sorted(slots)], rng, sc)
    recs = []
    from selfplay.threat_table import Table
    t = Table()  # 脅威の見えやすさと止めたかの表（threat_table）も一緒に作る
    r = play_game(sc, Combo(mm, pc), lambda k, rec: recs.append((k, rec)), observers=(t.ob,), on_day=t.on_day)
    with open(out, 'w', encoding='utf-8') as f:
        for k, rec in recs:
            f.write(json.dumps({'k': k, **rec}, ensure_ascii=False, default=str) + '\n')
    with open(out[:-6] + '.threats.jsonl', 'w', encoding='utf-8') as f:
        for x in t.rows(recs):
            f.write(json.dumps(dict(x, game=sid), ensure_ascii=False) + '\n')
    print(sid, r, '公開ログの結果', re.findall(r'=== 対戦終了 (.*)', text)[-1:])


if __name__ == '__main__':
    main(*sys.argv[1:7])
