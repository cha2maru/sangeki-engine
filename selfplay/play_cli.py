"""Claude が片方を担当して自動プレイヤーと対戦するための道具（ユーザー指示: 自動の側の弱点を調べる）。

対戦は「乱数の種＋それまでの Claude の選択の一覧」から毎回最初から再生する。Claude の番で止まり、
盤面と選べる手を表示して終了する。Claude は選択を decisions ファイルに1行（JSON）足して再実行する。

    cd play && /workspaces/sangeki/.venv/bin/python -m selfplay.play_cli --side mm --seed 5 --file selfplay/runs/claude_mm.jsonl

--side mm: Claude が脚本家（相手は DeductiveBlocker＝公開情報だけの主人公）
--side pc: Claude が主人公（相手は --opp route|hidden の脚本家）。表示は公開情報だけ（役職は出さない）
  {"kind": "pc_cards", "choice": [{"by": "A", "target": "C03", "card": "INTX"}, ...リーダーから3枚]}
  {"kind": "abilities", "choice": [["C06", 0, null], ["C04", 1, {"target": "C02"}]]}   （その日の友好能力の宣言。無ければ []）
  {"kind": "final", "choice": [{"char": "C03", "role": "KEY"}, ...全員、宣言順]}
  友好能力の引数の例: 情報屋 {"declare": "X_..."}、医者[2] {"target": ..., "mode": "remove"}、委員長 {"card": "PAR-"}、刑事[4] {"incident": "SUICIDE", "day": 2}
選択の書式（1行1つ、決定の順）:
  {"kind": "loop_setup", "choice": {"init": {"C18": "HOS"}}}   （手先・学者がいる脚本だけ。各ループの始め）
  {"kind": "mm_cards", "choice": [{"target": "C03", "card": "INT1"}, ...3枚]}
  {"kind": "mm_ability", "choice": "C03" | "B:SCH" | null}
  {"kind": "incident", "choice": {"target": ...} | {"par_target": ..., "int_target": ...} | null}
  {"kind": "killer", "choice": [{"char": "C08", "ability": "KILLER_KEY"}] | []}
  {"kind": "refuse", "choice": true | false}
  {"kind": "rumor", "choice": "B:SHR" | null}
--script で engine/scripts/<id>.json を使う。--opp は相手の型（脚本家: route|hidden|search、主人公: blind|read）
--script-file PATH: 脚本の id をファイルから読む（表示しない。主人公の目隠し用）

決定ファイルの番号: "n" のある行はその番号、無い行は直前までの最大の番号+1。同じ番号の行が複数あれば最後の行を使う
（再生も常駐も同じ読み方）。

常駐の郵便受けモード（--serve、--side pc）: 試合を1回だけ進め、Claude の番が来るたびに問いを <file の場所>/question.txt に書き、
決定ファイルの n 番目の決定が来るまで待つ（重い脚本家でも毎回最初から再生しない）。

    python -m selfplay.play_cli --side pc --opp calcGB --seed 3 --script-file DIR/script_id --file DIR/decisions.jsonl --serve

  question.txt の1行目は {"n": 決定の番号, "kind": 決定の種類[, "error": 受け付けなかった理由]}、続けて盤面と前の決定から後の出来事。
  答えは決定ファイルに1行足す（上の書式。"n" を付けてよい）。誤り（JSON・種類の違い・反則）は聞き直すので、{"n": N, ...} で1行足して直す。
  出来事は DIR/public_log.txt に随時追記し、終局で結果と全公開ログを DIR/result.txt に書き、question.txt の1行目を {"end": true} にして終わる。
  常駐が死んだら、同じ決定ファイルで --serve を起動し直せば続きから（--serve なしなら再生で）作り直せる。
  Claude の手順: 常駐を裏で起動 → Monitor などで question.txt の1行目（"n" が増える・"end"）を待つ → 読んで決定ファイルに1行足す → 繰り返す。
  目隠しでは --script-file を使い、脚本の id（id のファイル・脚本・<file>.meta.json）を読まない。詳しくは公開版の docs/cli.md（元は tools/public/engine/docs/cli.md）。

試合の条件（script・seed・side・opp）は <file>.meta.json に残し、違う条件で同じ決定ファイルを使うと止まる。
再生方式の表示は前の Claude の決定から後の出来事を全部（環境変数 PLAY_CLI_LOG=8 なら直近8件）。
先の宣言で合法でなくなった友好能力の宣言は行わず、「！行わなかった友好能力の宣言」と出す（run.py の ability_rejected）。
"""
import argparse
import copy
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import phases as ph  # noqa: E402
from engine.routes import enumerate_routes  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402


class NeedDecision(Exception):
    pass


def load_slots(path):
    """決定ファイル → {番号: 行}。"n" のある行はその番号、無い行は直前までの最大の番号+1。同じ番号は後の行が勝つ。
    JSON として読めない行は {'_error': ...} として次の番号に入れる（常駐は聞き直し、再生は止まる）。"""
    slots = {}
    if not path or not os.path.exists(path):
        return slots
    for l in open(path, encoding='utf-8'):
        if not l.strip():
            continue
        try:
            x = json.loads(l)
            n = x.get('n') if isinstance(x, dict) else None
        except ValueError as e:
            x, n = {'_error': f'JSON として読めない: {e}'}, None
        slots[n if isinstance(n, int) and n > 0 else max(slots, default=0) + 1] = x
    return slots


def load_decisions(path):
    """番号順の決定の一覧（抜けた番号の手前まで）。"""
    slots, out = load_slots(path), []
    while len(out) + 1 in slots:
        out.append(slots[len(out) + 1])
    return out


def _take(x, kind, check=None):
    """1つの決定を確かめて choice を返す。誤りは例外で投げる。"""
    if not isinstance(x, dict) or '_error' in x:
        raise ValueError(x.get('_error') if isinstance(x, dict) else '行が JSON のオブジェクトでない')
    if x.get('kind') != kind:
        raise ValueError(f'決定の順がずれている: 期待 {kind} / ファイル {x.get("kind")}')
    if 'choice' not in x:
        raise ValueError('choice が無い')
    if check:
        check(x['choice'])
    return x['choice']


class Mailbox:
    """問いを qpath に書き、決定ファイル dpath の n 番目の決定が来るまで待つ（interval 秒ごとに読む）。
    誤った答えはプロセスを落とさず、理由を問いに書き足して同じ n で聞き直す。"""

    interval = 0.5

    def __init__(self, qpath, dpath):
        self.qpath, self.dpath, self.n = qpath, dpath, 0
        open(dpath, 'a').close()

    def write(self, head, text):
        tmp = self.qpath + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(json.dumps(head, ensure_ascii=False) + '\n' + text)
        os.replace(tmp, self.qpath)  # 読む側が書きかけを見ないように

    def ask(self, kind, text, check=None):
        self.n += 1
        head, seen = {'n': self.n, 'kind': kind}, None
        self.write(head, text)
        while True:
            x = load_slots(self.dpath).get(self.n)
            if x is not None and x != seen:
                seen = x
                try:
                    return _take(x, kind, check)
                except Exception as e:  # 誤りの種類を問わず聞き直す（Claude は行を足すだけで直せる）
                    err = f'{type(e).__name__}: {e}'
                    self.write(dict(head, error=err), text + f'\n！n={self.n} の答えは受け付けなかった: {err}\n'
                               f'  {{"n": {self.n}, "kind": "{kind}", "choice": ...}} を1行足して直す\n')
            time.sleep(self.interval)


def _captured(fn, *args):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


def show(s, title, options):
    print(f'=== Claude の番: {title}（L{s["loop"]}D{s["day"]}、リーダー {s["leader"]}）')
    for c, v in s['chars'].items():
        r = ph.role(s, c)
        print(f"  {c} {v['area']} {'生' if v['alive'] else '死'} 不{v['par']} 友{v['gw']} 暗{v['int']} 護{v['guard']} [{r}]")
    print('  ボードの暗躍', s['boards'], ' 使用済み', s['used'])
    print('  勝ち筋（距離の近い順）:')
    for r in enumerate_routes(s)[:5]:
        print(f"    {r['total']:>2} {r['id']} via {r['via']} → {r['goal']}")
    print('  選べる手:', json.dumps(options, ensure_ascii=False)[:600])


class ClaudeMastermind:
    def __init__(self, decisions):
        self.d = list(decisions)

    def _next(self, kind, s, title, options, check=None):
        if not self.d:
            show(s, title, options)
            raise NeedDecision(kind)
        ch = _take(self.d.pop(0), kind)
        if check:
            check(ch)
        return ch

    def loop_setup(self, s, script):
        if 'C18' not in script['init'] and 'C19' not in script['init']:
            return {}
        return self._next('loop_setup', s, 'ループの準備（{"init": {"C18": "HOS"}} 手先の初期エリア・{"scholar": "gw"} 学者）',
                          {'C18': 'C18' in script['init'], 'C19': 'C19' in script['init']})

    def mm_cards(self, s):
        ch = self._next('mm_cards', s, '伏せ札を3枚', {'hand_used': s['used']['M']}, lambda ch: _check_mm_cards(s, ch))
        return [{'by': 'M', **c} for c in ch]

    def cultist_pick(self, s, placements):
        """カルティストの任意能力（同一エリアへの暗躍禁止を無視）。移動の解決後に同一エリアの暗躍禁止があるときだけ聞く
        （これが無く、Claude の脚本家ではカルティストの能力が一度も使われなかった。2026-10-24）。"""
        from engine.resolve import IllegalPlacement, resolve_actions
        try:
            after, _ = resolve_actions(s, placements)
        except (IllegalPlacement, ph.LoopEnd):
            return []
        cults = [c for c, v in after['chars'].items() if v['alive'] and ph.role(s, c) == 'CULTIST']
        opts = [{'ability': 'CULTIST', 'char': c, 'target': x['target']} for x in placements if x['card'] == 'INTX' for c in cults
                if x['target'] == 'B:' + after['chars'][c]['area']
                or (x['target'] in after['chars'] and after['chars'][x['target']]['area'] == after['chars'][c]['area'])]
        if not opts:
            return []
        return self._next('cultist', s, 'カルティストの暗躍禁止の無視（選べる手から0個以上の列）', {'options': opts})

    def mm_ability(self, s, actor, ability=None):
        return self._next('mm_ability', s, f'脚本家能力（{actor}={ph.role(s, actor)}、{ability or ph.role(s, actor)} の能力）',
                          {'actor': actor, 'area': s['chars'][actor]['area']})

    def mm_rumor(self, s):
        return self._next('rumor', s, '不穏な噂（任意のボード1つに暗躍1。使わなければ null）', {})

    def incident_choice(self, s, inc):
        return self._next('incident', s, f"事件 {inc['id']}（犯人 {inc['culprit']}）", inc)

    def killer_pick(self, s):
        opts = ph.killer_options(s)
        return self._next('killer', s, 'キラーの任意能力', opts) if opts else []

    def refuse(self, s, cid):
        return self._next('refuse', s, f'{cid} の友好能力を拒否するか', {'char': cid, 'role': ph.role(s, cid)})


class BlockingMastermind(ClaudeMastermind):
    """対AIモードで Claude が脚本家を務める（ユーザー「ちょっと私と遊んでほしい」）。判断のたびに問いを qpath に書き、
    dpath（JSON の1行＝1決定）に答えが足されるまで待つ。Claude は qpath を Monitor で見て、dpath に1行足して答える。
    Claude の型が持たない判断（ルールXの公開の選び方など）は fallback（自動の脚本家）に任せる。"""

    def __init__(self, qpath, dpath, fallback):
        super().__init__([])
        self.box, self.fallback = Mailbox(qpath, dpath), fallback

    def _next(self, kind, s, title, options):
        return self.box.ask(kind, _captured(show, s, title, options))

    def __getattr__(self, name):
        if name.startswith('__') or name in ('fallback', 'd', 'box'):
            raise AttributeError(name)
        return getattr(self.fallback, name)


CARD_CODES = ('札のコード: PAR+ 不安+1 / PAR- 不安-1（ループ1回）/ GW1 友好+1 / GW2 友好+2（ループ1回）/ INTX 暗躍禁止 / '
              'MV_V 移動↑↓ / MV_H 移動←→ / MVX 移動禁止（ループ1回）。ボードは B:HOS・B:SHR・B:CIT・B:SCH')


def show_public(s, title, extra, script=None):
    """主人公に見える盤面（公開シート・キャラクター名・事件名は日本語。Claude が主人公の点検: d07 で SPREAD を流布と読み違えた）。"""
    from engine.project import AREA_JA, INC_JA
    from engine.resolve import CHARS
    sc = dict(s['script'], **{k: v for k, v in (script or {}).items() if k in ('loops', 'set')})
    print(f'=== Claude（主人公）の番: {title}（ループ {s["loop"]}/{sc.get("loops", "?")}・{s["day"]}日目/{sc.get("days")}日、リーダー {s["leader"]}）')
    from ai_gm import SET_JA
    print(f"  公開シート: {SET_JA.get(sc.get('set', 'BTX'), sc.get('set'))}（ループ {sc.get('loops', '?')} 回 × {sc.get('days')} 日）")
    for c, v in s['chars'].items():
        if not v.get('present', True) or not v.get('area'):
            continue
        print(f"  {c} {CHARS[c]['name']}（臨界{CHARS[c]['limit']}） {AREA_JA.get(v['area'], v['area'])} {'生' if v['alive'] else '死'} "
              f"不{v['par']} 友{v['gw']} 暗{v['int']} 護{v['guard']}")
    print('  ボードの暗躍', {AREA_JA[k]: n for k, n in s['boards'].items()}, ' 使用済み', s['used'])
    print('  事件予定', [(i['day'], INC_JA.get(i['id'], i['id'])) for i in sc['incidents']], ' 公開された役職', s.get('revealed_roles', {}))
    print('  ' + CARD_CODES)
    print('  ', json.dumps(extra, ensure_ascii=False)[:500])


def event_line(rec, log, side):
    """出来事の記録1つを表示の1行に（主人公側は対AIモードの公開ログと同じ文。役職・理由は伏せる）。出さない記録は None。
    判断の記録のうち、行わなかった友好能力の宣言（run.py の ability_rejected）も出す（以前は黙って捨てていた）。"""
    if rec.get('kind') == 'ability_rejected':
        return f"  ！行わなかった友好能力の宣言（L{rec['loop']}D{rec['day']}）: {rec['choice']} … {rec['why']}"
    if rec.get('phase') == 'pc_knowledge' or 'events' not in rec:
        return None
    evs = rec.get('events') or []
    if side != 'pc':
        return f"  出来事 {rec.get('phase')} {json.dumps(evs, ensure_ascii=False)[:1500]}"
    from ai_gm import describe, name
    from engine.project import CARD_UI
    lines = [x for x in (describe(e) for e in evs if isinstance(e, dict)) if x]
    if rec.get('phase') == 'actions':  # 行動解決で脚本家の札も表向きになる（公開）
        mmc = next((r_['choice'] for k_, r_ in log if k_ == 'decisions' and r_.get('kind') == 'place_cards' and r_.get('who') == 'M'
                    and (r_.get('loop'), r_.get('day')) == (rec.get('loop'), rec.get('day'))), None)
        if mmc:
            lines.insert(0, '脚本家の札: ' + '、'.join(f"{name(x['target'])}={CARD_UI.get(x['card'], x['card'])}" for x in mmc))
    return f"  出来事（L{rec.get('loop')}D{rec.get('day')} {rec.get('phase')}）: " + ' ／ '.join(lines)[:1500]


class ClaudeProtagonist:
    def __init__(self, decisions, rng, script=None):
        self.d, self.rng, self.script = list(decisions), rng, script

    def _next(self, kind, s, title, extra, check=None):
        if not self.d:
            show_public(s, title, extra, self.script)
            raise NeedDecision(kind)
        return _take(self.d.pop(0), kind)

    def pc_cards(self, s, order, mm_targets=None):
        return self._next('pc_cards', s, f'行動カード（{"→".join(order)}）', {'脚本家の伏せ札の置き場所': mm_targets},
                          lambda ch: _check_pc_cards(s, mm_targets, ch))

    def abilities_for_day(self, s):
        return [tuple(x) for x in self._next('abilities', s, '友好能力の宣言（その日の分をまとめて）', {'1ループ1回で使用済みの能力': [x for x in s.get('ability_used_loop', []) if '#' in x]},  # 主人公の能力だけ（不穏な噂 X_RUMOR などは非公開）
                                             lambda ch: _check_abilities(s, ch))]

    def ability_candidates(self, s):
        return []

    def final_guesses(self, s):
        return self._next('final', s, '最後の戦い（全員の役職を宣言順に）', {}, lambda ch: _check_final(s, ch))


def _check_mm_cards(s, choice):
    """脚本家の札の反則（行動カードを置けないキャラ・手札にない札など）。主人公の札は無難な札で埋めて確かめる。
    確かめないと、反則の置き方が主人公の読み（card_read）まで進んで落ちた（幻想に不安+1、2026-10-24）。"""
    from engine.resolve import validate
    from selfplay.players import DUMMY_PC
    if not isinstance(choice, list) or len(choice) != 3 or not all(isinstance(c, dict) and {'target', 'card'} <= set(c) for c in choice):
        raise ValueError('[{"target": ..., "card": ...}, ...] の形で3枚')
    validate(s, [{'by': 'M', **c} for c in choice] + DUMMY_PC)


def _check_pc_cards(s, mm_targets, choice):
    """主人公の札の反則（印刷p21-22）を、公開情報だけで確かめる。脚本家の札は置き場所だけ分かるので、無難な札で埋める。"""
    from engine.resolve import validate
    dummy = [{'by': 'M', 'target': t, 'card': c} for t, c in zip(mm_targets or [], ('PAR+', 'PAR+', 'INT1'))]
    if len(dummy) != 3:
        return
    if not isinstance(choice, list) or not all(isinstance(c, dict) and {'by', 'target', 'card'} <= set(c) for c in choice):
        raise ValueError('[{"by": "A", "target": ..., "card": ...}, ...] の形で3枚')
    validate(s, dummy + choice)


def _check_abilities(s, choice):
    """宣言は1つずつ解決されるので、その場の盤面で確かめられるのは最初の1つだけ（後の宣言は解決時に確かめ、反則なら行わない）。"""
    from engine import abilities as ab
    decs = [tuple(x) for x in choice]
    for d in decs:
        if len(d) != 3:
            raise ValueError(f'宣言は [キャラクター, 能力の番号, 引数] の3つ組: {list(d)}')
    if decs:
        ab.check_declaration(copy.deepcopy(s), *decs[0])


def _check_final(s, choice):
    from engine import sets
    roles = set(sets.of(s)['roles']) | {'PERSON'}
    for g in choice:
        if g.get('char') not in s['chars']:
            raise ValueError(f'{g.get("char")} は登場していない')
        if g.get('role') not in roles:
            raise ValueError(f'{g.get("role")} という役職は無い（{sorted(roles)}）')
    named = [{'C33': 'C32'}.get(g['char'], g['char']) for g in choice]  # アルバイトとアルバイト？は2人で1回の宣言（final_battle と同じ）
    if len(set(named)) != len(named):
        raise ValueError('同じキャラクターを2度宣言している')
    left = set(s['chars']) - set(named) - ({'C33'} if 'C32' in named else set())
    if left:
        raise ValueError(f'宣言していないキャラクター: {sorted(left)}')


class ServingProtagonist(ClaudeProtagonist):
    """常駐の郵便受けモードの主人公。問いには盤面と、前の決定から後の出来事を載せる。"""

    def __init__(self, rng, script, box, log, public):
        super().__init__([], rng, script)
        self.box, self.log, self.public, self.mark = box, log, public, 0

    def _next(self, kind, s, title, extra, check=None):
        evs = [x for x in (event_line(rec, self.log, 'pc') for k, rec in self.log[self.mark:]) if x]
        text = _captured(show_public, s, title, extra, self.script) + '\n'.join(['前の決定から後の出来事:'] + evs) + '\n'
        ch = self.box.ask(kind, text, check)
        self.mark = len(self.log)
        self.public(f'Claude の決定 n={self.box.n} {kind}: {json.dumps(ch, ensure_ascii=False)}')
        return ch


def serve_pc(a, script, rng, opp):
    d = os.path.dirname(os.path.abspath(a.file))
    plog, rpath = os.path.join(d, 'public_log.txt'), os.path.join(d, 'result.txt')
    open(plog, 'w').close()  # 起動し直したら決定ファイルから作り直すので、公開ログも初めから書く
    log = []

    def public(line):
        with open(plog, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    # 全記録（脚本家の決定・役職を含む。one_game.py と同じ形式）を private/full_log.jsonl に書く。Claude は対戦中に読まない。
    # 対戦後に試合をそのまま辿り直すため（再生は乱数がずれて別の試合になる。目隠し6戦目）
    os.makedirs(os.path.join(d, 'private'), exist_ok=True)
    flog = open(os.path.join(d, 'private', 'full_log.jsonl'), 'w', encoding='utf-8')

    def log0(k, rec):
        log.append((k, rec))
        flog.write(json.dumps({'k': k, **rec}, ensure_ascii=False, default=str) + '\n')
        flog.flush()
        line = event_line(rec, log, 'pc')
        if line:
            public(line)

    box = Mailbox(os.path.join(d, 'question.txt'), a.file)
    pc = ServingProtagonist(rng, script, box, log, public)
    def day_state(st, *_):  # 各日の始めの盤面も全記録に（脅威の見えやすさと止めたかの表を作る。ユーザー「なぜ止めなかったかを数値化」）
        flog.write(json.dumps({'k': 'state', 'state': st}, ensure_ascii=False, default=str) + '\n')
    r = play_game(script, Combo(opp, pc), log0, on_day=day_state)
    flog.close()
    public(f'=== 対戦終了 {json.dumps(r, ensure_ascii=False)}')
    with open(rpath, 'w', encoding='utf-8') as f:
        f.write(open(plog, encoding='utf-8').read())
    box.write({'end': True, 'result': r}, f'=== 対戦終了 {json.dumps(r, ensure_ascii=False)}（全公開ログは {rpath}）\n')
    print('=== 対戦終了', r)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--side', default='mm', choices=['mm', 'pc', 'both'])
    # --side both: Claude 同士の対戦。--mm-file・--pc-file にそれぞれの決定、--as で自分の側。相手の番なら盤面は出さず「相手の番」とだけ出す
    ap.add_argument('--mm-file')
    ap.add_argument('--pc-file')
    ap.add_argument('--as', dest='as_side', choices=['mm', 'pc'])
    ap.add_argument('--opp', default=None)  # 相手の型（league の make が受け付ける名前）
    ap.add_argument('--script', default='s03_bomb')
    ap.add_argument('--script-file')  # 脚本の id をファイルから読む（表示しない）
    ap.add_argument('--seed', type=int, default=5)
    ap.add_argument('--file')
    ap.add_argument('--serve', action='store_true')  # 常駐の郵便受けモード（--side pc）
    a = ap.parse_args(argv)
    if a.script_file:
        a.script = open(a.script_file, encoding='utf-8').read().strip()
    _load = load_decisions
    decs = _load(a.file)
    if a.file and a.side != 'both':  # 試合の条件を決定ファイルの横に残し、違う条件で再生しない（同じ種類の決定が並ぶと、黙って別の試合になる）
        import hashlib  # --script-file（目隠し）のときは脚本の id の代わりにハッシュを残す（meta.json から id が読めないように）
        sid = 'sha1:' + hashlib.sha1(a.script.encode()).hexdigest()[:12] if a.script_file else a.script
        meta, mp = {'script': sid, 'seed': a.seed, 'side': a.side, 'opp': a.opp}, a.file + '.meta.json'
        if os.path.exists(mp) and json.load(open(mp, encoding='utf-8')) != meta:
            raise SystemExit(f'試合の条件が {mp} と違う: {json.load(open(mp, encoding="utf-8"))} / 今回 {meta}')
        if not os.path.exists(mp):
            json.dump(meta, open(mp, 'w', encoding='utf-8'), ensure_ascii=False)
    rng = random.Random(a.seed)
    log = []
    from engine.scripts import by_id
    from selfplay.league import make
    script = by_id(a.script)
    if a.side == 'both':
        return main_both(a, script, rng, _load)
    if a.serve:
        if a.side != 'pc' or not a.file:
            ap.error('--serve は --side pc と --file で使う')
        return serve_pc(a, script, rng, make(a.opp or 'route', rng, script))
    if a.side == 'pc':
        pc = ClaudeProtagonist(decs, rng, script)
        player = Combo(make(a.opp or 'route', rng, script), pc)
    else:
        pc = make(a.opp or 'blind', rng, script)
        player = Combo(ClaudeMastermind(decs), pc)
    r = None
    try:
        r = play_game(script, player, lambda k, rec: log.append((k, rec)))
        print('=== 対戦終了', r)
    except NeedDecision:
        pass
    if os.environ.get('PLAY_CLI_READ') and hasattr(pc, 'read_w'):  # 伏せ札を読む主人公（oreadc）の型の重み（Claude が脚本家のときの確かめ用）
        print('  型の重み:', {k: round(v, 2) for k, v in pc.read_w.items()})
    # 前の Claude の決定から後の出来事を全部出す（直近の8件だけだと、ループが途中で終わった日の出来事を見落とした）。PLAY_CLI_LOG で件数を指定もできる。
    # 主人公側では、役職や理由を含む項目を伏せる（公開ログと同じ範囲）
    me = ('P', 'A', 'B', 'C') if a.side == 'pc' else ('M',)
    last = max((i for i, (k, rec) in enumerate(log[:-1]) if k == 'decisions' and rec.get('who') in me and rec.get('kind') != 'ability_rejected'), default=0)
    for k, rec in log[last:] if 'PLAY_CLI_LOG' not in os.environ else log[-int(os.environ['PLAY_CLI_LOG']):]:
        line = event_line(rec, log, a.side)
        if line:
            print(line)
    if getattr(pc, 'ded', None) is not None:
        m, tot = pc.ded.marginals()
        top = {c: max(d.items(), key=lambda x: x[1]) for c, d in m.items() if d}
        print('  主人公の推理（各人の最有力の役職と確率）:', {c: (r, round(w / tot, 2)) for c, (r, w) in top.items()})
    return r


PC_KINDS = {'pc_cards', 'abilities', 'final'}


def main_both(a, script, rng, _load):
    """Claude 同士の対戦（脚本家・主人公とも Claude のサブエージェント）。毎回最初から再生し、決定の足りない側の番で止まる。
    --as の側の番なら、その側の盤面（主人公は公開情報だけ）を出す。相手の番なら「相手の番」とだけ出す（脚本家の盤面を主人公に見せない）。"""
    import contextlib
    import io
    log = []
    player = Combo(ClaudeMastermind(_load(a.mm_file)), ClaudeProtagonist(_load(a.pc_file), rng, script))
    buf = io.StringIO()
    turn, result = None, None
    try:
        with contextlib.redirect_stdout(buf):
            result = play_game(script, player, lambda k, rec: log.append((k, rec)))
    except NeedDecision as e:
        turn = 'pc' if e.args[0] in PC_KINDS else 'mm'
    if result is not None:
        print('TURN: end')  # 待つ側は1行目で判定するので、終了も1行目に出す
        print('=== 対戦終了', result)
        return
    print(f'TURN: {turn}')
    if turn != a.as_side:
        print('  相手の番。相手が決定を足すまで待つ。')
        return
    print(buf.getvalue()[-6000:])
    for k, rec in log[-8:]:
        if k == 'events' and rec.get('phase') != 'pc_knowledge':
            evs = rec.get('events') or []
            if a.as_side == 'pc':
                evs = [{kk: vv for kk, vv in e.items() if kk not in ('role', 'actor', 'cause', 'by', 'reason')} for e in evs if isinstance(e, dict)]
            print('  出来事', rec.get('phase'), json.dumps(evs, ensure_ascii=False)[:1500])


if __name__ == '__main__':
    main()
