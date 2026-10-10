"""対AIモード: 人間がブラウザの盤面（board.html）で主人公3人を操作し、エンジンと自動の脚本家が進行する。

    # 盤面サーバー（手動モードの game/ とは別の置き場所・ポート）
    SANGEKI_GAME=play/game_ai PORT=8766 node play/server.mjs
    # 進行役
    cd play && python ai_gm.py --script s03_bomb [--mm search] [--seed 1]

- 盤面（state.json）と公開ログ（log.jsonl）には公開情報だけを書く（engine/project.py の射影と、出来事の秘匿項目の除去）。
- 主人公の番では waiting に入力待ちを書き、inbox.jsonl に届いた操作を読む（手動モードと同じ形式）。
- 公開の出来事は推理エンジン（selfplay.players.PublicObserver）にも通し、候補の割合を deduce.json に書く（推理シートの印）。
"""
import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import abilities as ab  # noqa: E402
from engine.adapter import NAME2C, placements_from_inbox  # noqa: E402
from engine.project import AREA_JA, CARD_UI, INC_JA, project  # noqa: E402
from engine.resolve import CARDS, CHARS, ONCE  # noqa: E402
from engine.scripts import by_id  # noqa: E402
from selfplay.league import make  # noqa: E402
from selfplay.players import PublicObserver  # noqa: E402
from selfplay.run import Combo, play_game  # noqa: E402

ROLES = json.load(open(os.path.join(HERE, 'engine', 'data', 'roles.json'), encoding='utf-8'))['roles']
RULES = json.load(open(os.path.join(HERE, 'engine', 'data', 'rules.json'), encoding='utf-8'))['rules']
ROLE_JA = {k: (v['name'] if isinstance(v, dict) else v) for k, v in ROLES.items()}
RULE_JA = {k: (v['name'] if isinstance(v, dict) else v) for k, v in RULES.items()}
JA2ROLE = {v: k for k, v in ROLE_JA.items()}
JA2RULE = {v: k for k, v in RULE_JA.items()}
JA2INC = {v: k for k, v in INC_JA.items()}
INC_EFFECT = {v['name']: v['effect'] for v in json.load(open(os.path.join(HERE, 'engine', 'data', 'incidents.json'), encoding='utf-8'))['incidents'].values()}
SET_JA = {'BTX': 'Basic Tragedy X'}
SECRET = ('role', 'actor', 'cause', 'by', 'culprit', 'reason')  # 公開ログに出さない項目（役職や理由を含みうる）
END_ABILITY = '能力の宣言を終える'
END_FINAL = '指摘を終える'


COUNTER_JA = {'par': '不安', 'gw': '友好', 'int': '暗躍', 'guard': '護衛'}


def name(c):
    if isinstance(c, str) and c.startswith('B:'):
        return AREA_JA[c[2:]]
    return CHARS[c]['name'] if c in CHARS else str(c)


PHASE_JA = {'loop_start': 'ループの準備', 'turn_start': 'ターン開始フェイズ', 'mm_cards': '脚本家行動フェイズ', 'pc_cards': '主人公行動フェイズ',
            'actions': '行動解決フェイズ', 'mm_ability': '脚本家能力フェイズ', 'ability': '主人公能力フェイズ', 'incident': '事件フェイズ',
            'turn_end': 'ターン終了フェイズ', 'loop_end_check': 'ループ終了時の判定'}


class Table:
    """ゲームの置き場所（state.json・log.jsonl・inbox.jsonl・deduce.json）の読み書き。"""

    def __init__(self, d, script):
        self.d, self.script = d, script
        os.makedirs(d, exist_ok=True)
        for f in ('log.jsonl', 'inbox.jsonl', 'snapshots.jsonl'):
            open(os.path.join(d, f), 'w').close()
        if os.path.exists(os.path.join(d, 'deduce.json')):  # 前の試合の推理を推理シートに残さない
            os.remove(os.path.join(d, 'deduce.json'))
        self.n, self.phase_id, self.read = 0, 0, 0
        self.game = int(time.time())  # 試合の識別子（盤面が前の試合の送信済みを出さないため）
        self.s, self.placed, self.phase_name = None, [], '準備'
        self.waiting = {'kind': 'none', 'text': '脚本家の番', 'options': []}
        self.reply = ''  # 「脚本家の返答」の欄（反則の置き方などの知らせ。公開ログには書かない＝振り返りに出さない）
        self.inc_result = {}
        self.last_phase = None
        self.ability_options = None
        self.mm_targets_hist = []

    def phase(self, loop, day, key, extra=''):
        """フェイズの区切りをログに出す（フェイズの進行は常に公開する）。"""
        if (loop, day, key) == self.last_phase or key not in PHASE_JA:
            return
        self.last_phase = (loop, day, key)
        where = f'L{min(loop, self.script["loops"])}' + (f'D{day}' if day else '') if loop else ''
        self.log(f'── {where} {PHASE_JA[key]} ──' + (f' {extra}' if extra else ''))

    def _write(self, f, obj):
        tmp = os.path.join(self.d, f + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as fp:
            json.dump(obj, fp, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.d, f))

    def log(self, text):
        self.n += 1
        s = self.s or {}
        with open(os.path.join(self.d, 'log.jsonl'), 'a', encoding='utf-8') as fp:
            loop = min(s['loop'], self.script['loops']) if s.get('loop') else None  # 最後の戦いではエンジンのループ番号が1つ進んでいる
            fp.write(json.dumps({'n': self.n, 'loop': loop, 'day': s.get('day'), 'text': text}, ensure_ascii=False) + '\n')

    def show(self, s=None, phase_name=None, waiting=None, placed=None, bump=True):
        """bump=False: 同じ段階の中での表示の更新（phaseId を変えないので、送信済みの操作が古い扱いにならない）。"""
        if s is not None:
            self.s = s
        if phase_name is not None:
            self.phase_name = phase_name
        if waiting is not None:
            self.waiting = waiting
        if placed is not None:
            self.placed = placed
        if self.s is None:
            return
        if bump:
            self.phase_id += 1
        pub = project(self.s, self.placed)
        for i in pub['incidents']:
            i['effect'] = INC_EFFECT.get(i['name'], '')
            r = self.inc_result.get((self.s['loop'], i['day']))
            if r:
                i['result'] = r
        sc = self.script
        appear = []
        for c, a in (sc.get('appear') or {}).items():
            if a.get('loop'):
                appear.append(f'{name(c)}: ループ{a["loop"]}から登場')
            if a.get('day'):
                appear.append(f'{name(c)}: 各ループの{a["day"]}日目から登場')
        # 公開シートに書かれること（惨劇セット・ループ数・日数・事件予定・テリトリー・登場の時期・特殊ルール）
        pub['publicSheet'] = {'set': SET_JA.get(sc.get('set', 'BTX'), sc.get('set')), 'loops': sc['loops'], 'days': sc['days'],
                              'territory': AREA_JA.get(sc.get('territory')) if sc.get('territory') else None,
                              'appear': appear, 'special': sc.get('special') or ''}
        pub['loop'] = min(pub['loop'], self.script['loops'])  # 最後の戦いではエンジンのループ番号が1つ進んでいる
        pub.update({'mode': 'ai', 'reply': self.reply,  # 対AIモード（盤面は手動モード用の欄＝脚本家に伝える・ハッシュ・異議を出さない）
                    'game': self.game, 'phaseId': self.phase_id, 'phaseName': self.phase_name, 'waiting': self.waiting,
                    'loops': self.script['loops'], 'days': self.script['days'],
                    # 盤面は「キャラクター名#番号」で使用済みを判定する（エンジンは「C07#0」）
                    'abilityUsedToday': [self._tag(x) for x in self.s.get('ability_used_today', [])],
                    'abilityUsedLoop': [self._tag(x) for x in self.s.get('ability_used_loop', []) if '#' in str(x)],  # 主人公の能力だけ（不穏な噂は非公開）
                    # 主人公能力フェイズの間だけ、いま合法な宣言（盤面の能力のメニューを絞る）
                    'abilityOptions': self.ability_options if (self.waiting or {}).get('kind') == 'ability' else None})
        self._write('state.json', pub)
        # 盤面の履歴（ログ #n の処理が反映された直後の盤面）。盤面の「過去の盤面」「解決前／解決後」が読む（手動モードの gm.py と同じ形）
        if self.n > getattr(self, '_snap_n', 0):
            with open(os.path.join(self.d, 'snapshots.jsonl'), 'a', encoding='utf-8') as fp:
                fp.write(json.dumps({'prev_n': getattr(self, '_snap_n', 0), 'log_n': self.n, 'state': pub}, ensure_ascii=False) + '\n')
            self._snap_n = self.n

    @staticmethod
    def _tag(x):
        c, _, i = str(x).partition('#')
        return f'{name(c)}#{i}'

    def wait(self, types):
        """いまの phaseId に対する、types のどれかの操作を1つ待って返す。"""
        path = os.path.join(self.d, 'inbox.jsonl')
        while True:
            lines = open(path, encoding='utf-8').read().splitlines()
            for line in lines[self.read:]:
                self.read += 1
                rec = json.loads(line)
                if rec.get('phaseId') == self.phase_id and rec.get('type') in types:
                    return rec
                if rec.get('type') == 'message':
                    self.log(f'（伝言を受け取った: {rec.get("text")}）')
            time.sleep(0.5)


def describe(e):
    """公開の出来事1つを日本語の1行に。秘匿の項目は使わない。"""
    k = e.get('kind')
    if k == 'revealed':  # 行動解決で6枚が公開された。脚本家の札は「脚本家の札が公開された」の行で出すので、ここは主人公の札（振り返り用）
        pc = [x for x in e.get('cards', []) if x.get('by') != 'M']
        return ('主人公の札: ' + '、'.join(f'{x["by"]} {name(x["target"])}={CARD_UI.get(x["card"], x["card"])}' for x in pc)) if pc else None
    if k == 'move':
        src = f'{AREA_JA.get(e["from"], e["from"])} → ' if e.get('from') else ''  # 行方不明の移動には移動元が無い
        return f'{name(e["char"])} が {src}{AREA_JA.get(e.get("to"), e.get("to"))} へ移動'
    if k == 'mm_ability':
        # 脚本家は理由を説明しない。役職の能力は「どこからともなく」と伝える（主人公の書 印刷p23）
        if e.get('role') == 'DOCTOR':  # 医者[2] を脚本家が使った（使ったこと自体は公開）
            return f'脚本家が{name(e.get("actor"))}の能力を使った: {name(e["target"])} の不安を' + ('取り除いた' if e.get('mode') == 'remove' else '置いた')
        ja = next((j for c, j in (('int', '暗躍'), ('par', '不安'), ('gw', '友好')) if c in e), 'カウンター')
        return f'どこからともなく {name(e["target"])} に{ja}+1'
    if k == 'no_death':
        return f'{name(e["char"])} は死亡しなかった'
    if k == 'guarded':
        return f'{name(e["char"])} の死亡は護衛で防がれた'
    if k == 'substitute':
        return f'{name(e["char"])} が {"・".join(name(x) for x in (e.get("for") or []))} の身代わりになった'
    if k == 'gwx_ignored':
        return f'{name(e["target"])} への友好禁止は無視された'
    if k == 'intx_ignored':
        return f'{name(e["target"])} への暗躍禁止は無視された'
    if k == 'move_blocked':
        return f'{name(e["char"])} への移動は移動禁止で止まった'
    if k == 'move_forbidden':
        return f'{name(e["char"])} の移動は禁止エリアのため無効'
    if k in ('par', 'gw', 'int'):
        if e.get('from') is None and e.get('to') is None:
            if e.get('n'):  # 因果の糸・事件の効果など、増えた数だけの記録（止められない増減。公開される）
                ja = {'par': '不安', 'gw': '友好', 'int': '暗躍'}[k]
                # 出典がルール・事件・カードの特性のときだけ出す（キャラクターの役職が出典なら割れうる）
                src = RULE_JA.get(e.get('by')) or INC_JA.get(e.get('by')) or {'SCHOLAR': '学者の特性', 'BLACK_CAT': '黒猫の特性'}.get(e.get('by'))
                return f'{name(e["target"])} に{ja}{e["n"]:+d}' + (f'（{src}）' if src else '')
            return None  # ループの準備での片付けなど
        ja = {'par': '不安', 'gw': '友好', 'int': '暗躍'}[k]
        card = f'（{CARD_UI.get(e.get("card"), e.get("card"))}）' if e.get('card') else ''
        return f'{name(e["target"])} の{ja} {e.get("from")} → {e.get("to")}{card}'
    if k == 'nullified':
        return f'{name(e["target"])} への {"・".join(CARD_UI.get(c, c) for c in e.get("cards", []))} は無効になった'
    if k == 'intx_void':
        return f'暗躍禁止が複数出たので、{name(e["target"])} の暗躍禁止は無効'
    if k == 'used':
        return None
    if k == 'incident':
        return f'{e["day"]}日目 {INC_JA.get(e["id"], e["id"])}: ' + ('発生した' if e['occurred'] else '発生しなかった') + \
            (f'（不安が臨界以上: {"・".join(name(c) for c in e.get("critical", []))}）' if e.get('critical') else '')
    if k == 'death':
        return f'{name(e["char"])} が死亡した'
    if k == 'appear':
        return f'{name(e["char"])} が {AREA_JA.get(e.get("area"), e.get("area"))} に登場した'
    if k == 'refused':
        return f'{name(e["char"])} の友好能力は拒否された'
    if k == 'ability':
        extra = ''
        if 'reveal_role' in e:
            (c, r), = e['reveal_role'].items()
            extra = f' → {name(c)} の役職は {ROLE_JA.get(r, r)}'
        if isinstance(e.get('reveal_rule_x'), str):
            extra = f' → ルールX {RULE_JA.get(e["reveal_rule_x"], e["reveal_rule_x"])}'
        if 'reveal_culprit' in e:
            extra = ' → 犯人: ' + '、'.join(f'{INC_JA.get(i, i)}={name(c)}' for i, c in e['reveal_culprit'].items())
        # カウンターの増減・移動などの結果（公開の盤面に現れる事実）も1行に添える
        eff = {'int-': '暗躍-1', 'int+': '暗躍+1', 'par-': '不安-1', 'par+': '不安+1', 'par+2': '不安+2', 'gw+': '友好+1',
               'guard+': '護衛+1', 'int_to_gw': '暗躍→友好', 'move': '移動', 'transfer': 'カウンターを移動', 'revive': '蘇生',
               'clear': '全てのカウンターを取り除いた'}
        if isinstance(e.get('par'), (list, tuple)) and len(e['par']) == 2:  # 医者[2] など: (対象, 'place'|'remove')
            t, mode = e['par']
            extra += f" → {name(t)} 不安{'+1' if mode == 'place' else '-1'}"
        if isinstance(e.get('transfer'), (list, tuple)) and len(e['transfer']) == 3:  # 鑑識官[2]: (元, 先, カウンター)
            a, b, cnt = e['transfer']
            extra += f" → {name(a)} から {name(b)} へ{COUNTER_JA.get(cnt, cnt)}を1つ移動"
        for key, ja in eff.items():
            if key in e and not (key == 'transfer' and isinstance(e[key], (list, tuple)) and len(e[key]) == 3):
                v = e[key]
                v = '・'.join(name(x) for x in v) if isinstance(v, (list, tuple)) else name(v) if isinstance(v, str) else v
                extra += f' → {v} {ja}' if not isinstance(v, dict) else f' → {ja}'
        return f'{name(e["char"])} の友好能力を使った{extra}'
    if k == 'role_public':
        return f'{name(e["char"])} の役職が公開された: {ROLE_JA.get(e["role"], e["role"])}'
    if k == 'loop_end_check':
        return 'ループ終了時の判定: ' + ('主人公の敗北' if e.get('loss') else '敗北条件は無い')
    if k == 'loop_end':
        # 脚本家は理由を説明しない（主人公の書 印刷p23、wiki/concepts/script.md）。主人公の死亡は「あなたたちは死亡しました」とだけ伝え、
        # キーパーソンの死亡・タイムトラベラーなどは敗北したことだけを伝える（理由の文をそのまま出すと役職が割れる）
        # どのフェイズで終わったかは公開情報（ユーザー指摘）
        ph_ja = {'turn_start': 'ターン開始フェイズ', 'actions': '行動解決フェイズ', 'mm_ability': '脚本家能力フェイズ', 'ability': '主人公能力フェイズ',
                 'incident': '事件フェイズ', 'turn_end': 'ターン終了フェイズ'}.get(e.get('at_phase'), '')
        head = f'{ph_ja}に' if ph_ja else ''
        return head + ('あなたたちは死亡した。ループが終わった' if e.get('reason') == '主人公死亡' else 'ループが終わった（主人公の敗北）')
    rest = {kk: vv for kk, vv in e.items() if kk not in SECRET and kk != 'kind'}
    return f'{k} {json.dumps(rest, ensure_ascii=False)}'


class HumanProtagonist:
    """主人公3人の判断を、盤面からの操作で行う。"""

    def __init__(self, table, rng):
        self.t, self.rng = table, rng

    def pc_cards(self, s, order, mm_targets=None):
        self.t.s = s  # ログの行の L・D を正しくする
        self.t.mm_targets_hist += list(mm_targets or [])  # 狙いの読み（推理シートの「狙い込み」）に使う。伏せ札の置き場所は公開情報
        mm = [{'by': 'M', 'target': x, 'card': 'INT1'} for x in (mm_targets or [])]
        self.t.phase(s['loop'], s['day'], 'mm_cards', '伏せ札: ' + '・'.join(name(x) for x in mm_targets or []))
        self.t.phase(s['loop'], s['day'], 'pc_cards', f'リーダー {order[0]}')
        while True:
            self.t.show(s, f'L{s["loop"]} {s["day"]}日目 主人公行動フェイズ',
                        {'kind': 'cards', 'text': (f'{self.t.reply} ／ ' if self.t.reply else '') + f'行動カードを3枚（{"→".join(order)}）置いて『確定』。脚本家の伏せ札は '
                         + '・'.join(name(x) for x in mm_targets or []), 'options': []}, placed=mm)
            rec = self.t.wait(('cards',))
            # 受け付けなかった置き方はログに書かない（振り返りには成功した配置だけを出す。行動解決で「主人公の札: …」の行になる）
            try:
                pc = placements_from_inbox(rec)
            except KeyError as e:
                self.t.reply = f'札を読めなかった（{e}）。置き直してください'
                continue
            err = self._check(s, pc)
            if err:
                self.t.reply = f'札の置き方が合法でない: {err}。置き直してください'
                continue
            self.t.reply = ''
            return sorted(pc, key=lambda x: order.index(x['by']))

    @staticmethod
    def _check(s, pc):
        if sorted(x['by'] for x in pc) != ['A', 'B', 'C']:
            return '主人公A・B・Cが1枚ずつ置く（主人公の書 印刷p21 4-3）'
        if len({x['target'] for x in pc}) < 3:
            return '主人公どうしで同じ場所には置けない'
        for x in pc:
            if x['card'] in ONCE[x['by']] and x['card'] in s['used'][x['by']]:
                return f'{x["by"]} の {CARD_UI[x["card"]]} はこのループで使用済み（1ループ1回）'
            if x['card'] not in CARDS['protagonist_hand']:
                return f'{x["card"]} は主人公の札ではない'
        return None

    def ability_one(self, s):
        """主人公能力フェイズの宣言を1件受け付けて返す（エンジンがその場で解決し、結果を公開ログに書く）。
        『能力の宣言を終える』なら None。前の能力の結果を見て次を選べるように、1件ずつ解決する（ユーザー指摘）。"""
        key = (s['loop'], s['day'])
        if getattr(self, '_ab_day', None) != key:
            self._ab_day = key
            # 脚本家能力フェイズの区切りは毎日必ず出す（能力を持つ役職がいる日だけ出すと、その存在が漏れる）
            self.t.phase(s['loop'], s['day'], 'mm_ability')
            self.t.phase(s['loop'], s['day'], 'ability')
        while True:
            self.t.ability_options = self._options(s)  # 前の能力の解決で盤面が変わるので毎回作り直す
            self.t.show(s, f'L{s["loop"]} {s["day"]}日目 主人公能力フェイズ',
                        {'kind': 'ability', 'text': (f'{self.t.reply} ／ ' if self.t.reply else '') + 'リーダーから友好能力を宣言（右クリック）。1件ずつその場で解決する。宣言し終えたら『' + END_ABILITY + '』',
                         'options': [END_ABILITY]})
            rec = self.t.wait(('ability', 'option'))
            if rec['type'] == 'option':
                return None
            dec = self._match(s, rec)
            if dec is None:  # 受け付けなかった宣言はログに書かない（「脚本家の返答」に出す）
                self.t.reply = f'{rec.get("char")} の能力 {rec.get("label", "")} は、いまは使えない（友好・場所・対象・回数を確認）'
                self.t.show(bump=False)
                continue
            self.t.reply = ''
            self.t.log(f'宣言を受け付けた: {rec.get("char")} {rec.get("label", "")}')
            return dec

    @staticmethod
    def _options(s):
        """いま合法な宣言を、盤面のメニュー向けに {'キャラ名#番号': {'targets': [...], 'modes': [...]}}。"""
        out = {}
        for c, i, a in ab.declaration_options(s):
            a = a or {}  # 引数の無い能力（巫女[3] など）は None
            o = out.setdefault(f'{name(c)}#{i}', {'targets': [], 'modes': []})
            t = a.get('target')
            if t and name(t) not in o['targets']:
                o['targets'].append(name(t))
            if 'from' in a:  # 鑑識官[2]★: 同じエリアの2人の間でカウンター1つを移す
                for key, v in (('froms', name(a['from'])), ('tos', name(a['to'])), ('counters', COUNTER_JA.get(a.get('counter')))):
                    if v and v not in o.setdefault(key, []):
                        o[key].append(v)
            m = {'remove': '取り除く', 'place': '置く'}.get(a.get('mode'))
            if m and m not in o['modes']:
                o['modes'].append(m)
        return out

    @staticmethod
    def _match(s, rec):
        cid = NAME2C.get(rec.get('char'))
        idx = rec.get('ability')
        want = {}
        if rec.get('target'):
            want['target'] = NAME2C.get(rec['target'], rec['target'])
        if rec.get('from'):
            want['from'] = NAME2C.get(rec['from'], rec['from'])
            want['to'] = NAME2C.get(rec.get('to'), rec.get('to'))
            want['counter'] = {v: k for k, v in COUNTER_JA.items()}.get(rec.get('counter'))
        if rec.get('mode'):
            want['mode'] = {'取り除く': 'remove', '置く': 'place'}.get(rec['mode'], rec['mode'])
        if rec.get('declare'):
            want['declare'] = JA2RULE.get(rec['declare'], rec['declare'])
        if rec.get('incident'):
            want['incident'] = JA2INC.get(rec['incident'], rec['incident'])
        if rec.get('day'):  # 同じ名前の事件が2つある脚本（2日・4日の遠隔殺人など）
            want['day'] = rec['day']
        for c, i, a in ab.declaration_options(s):
            if c == cid and i == idx and all((a or {}).get(k) == v for k, v in want.items() if k in (a or {})):
                return (c, i, a)
        return None

    def ability_candidates(self, s):
        return []

    def final_guesses(self, s):
        order, first = [], True
        while True:
            left = [c for c in s['chars'] if c not in {g['char'] for g in order}]
            self.t.show(s, '最後の戦い', {'kind': 'final', 'text': '右クリックで役職を1人ずつ指摘（宣言順に判定。外れた時点で脚本家の勝ち）。'
                                         f'残り {len(left)} 人。全員を指摘したら『{END_FINAL}』', 'options': [END_FINAL]}, bump=first)
            first = False
            rec = self.t.wait(('final', 'option'))
            if rec['type'] == 'option':
                return order + [{'char': c, 'role': 'PERSON'} for c in left]
            c, r = NAME2C.get(rec['char']), JA2ROLE.get(rec['role'])
            if c in s['chars'] and r:
                order = [g for g in order if g['char'] != c] + [{'char': c, 'role': r}]
                self.t.log(f'指摘: {rec["char"]} は {rec["role"]}')


class Narrator:
    """公開の出来事を盤面・公開ログ・推理（deduce.json）に反映する観測者。"""

    def __init__(self, table):
        self.t = table
        self.pub = PublicObserver()

    def observe(self, s, phase, events):
        self.t.s = s
        if phase == 'incident':
            for e in events:
                if e.get('kind') == 'incident':
                    self.t.inc_result[(s['loop'], e['day'])] = '発生' if e['occurred'] else '不発'
        # 公開された札（主人公の札）を、移動やカウンターの行より先に（脚本家の札の行のすぐ後）
        for e in sorted(events, key=lambda x: not (isinstance(x, dict) and x.get('kind') == 'revealed')):
            if isinstance(e, dict):
                try:
                    line = describe(e)
                except Exception:  # ログの1行が作れなくても試合は止めない（秘匿の項目は除いて出す）
                    line = f'{e.get("kind")} ' + json.dumps({k: v for k, v in e.items() if k not in SECRET and k != 'kind'}, ensure_ascii=False)
                if line:
                    self.t.log(line)
        self.pub.observe(s, phase, events)
        self.t.show(s, placed=[] if phase in ('actions', 'turn_end', 'loop_start') else None)
        self.deduce(s)

    def deduce(self, s):
        """推理エンジンの候補の割合（役職・ルール・犯人）を deduce.json に書く。"""
        import numpy as np
        from engine.deduce import COMBOS, incident_candidates
        ded = self.pub.ded
        if ded is None:
            return
        m, tot = ded.marginals()
        R, C = ded._sync()
        w = ded._w(C)
        wt = float(w.sum()) or 1.0
        rules = {}
        for rid in RULE_JA:
            idx = [i for i, cb in enumerate(COMBOS) if rid in cb]
            rules[RULE_JA[rid]] = round(float(w[np.isin(C, idx)].sum()) / wt, 3)
        roles = {name(c): {ROLE_JA[r]: round(float(v) / tot, 3) for r, v in d.items() if v > 0} for c, d in m.items()} if tot else {}
        cands = incident_candidates(list(s['chars']), self.pub.inc_history, self.pub.culprits)
        culprits = {}
        for key, cs in cands.items():
            iid, _, day = key.partition('@')
            culprits[f'{day}日 {INC_JA.get(iid, iid)}'] = [name(c) for c in sorted(cs)]
        out = {'loop': s['loop'], 'day': s['day'], 'hypotheses': int(ded.n_hyp()),
               'roles': roles, 'rules': rules, 'culprits': culprits}
        if ded.empty_by:  # 推理の矛盾（仮説が全部消えた）。進行役の端末と推理のデータに出す（エンジンか推理の不具合）
            out['empty_by'] = [ded.empty_by[0], str(ded.empty_by[1])[:200]]
            if not getattr(self, '_warned_empty', False):
                self._warned_empty = True
                print(f'[推理の矛盾] 公開情報の推理の仮説が全部消えた（観測 {ded.empty_by[0]}）。エンジンか推理の不具合', file=sys.stderr, flush=True)
        else:
            out['intent'] = self._intent(s, ded)
        self.t._write('deduce.json', out)

    def _intent(self, s, ded, n=300, k=0.6):
        """狙い込みの見込み: 仮説を n 個引き、これまでの伏せ札の対象（全ループ）が、その仮説の近い勝ち筋に関わる数で
        exp(k×数) の重みを付けた、ルールと役職の割合（ユーザー「あからさまな動きでもキーパーソン・キラーの線が上がらない」。
        自動の主人公の intent と同じ考え。ponytail: 標本 n 個の近似で、犯人は数えない）。"""
        import math
        import random
        from selfplay.pc_v2 import involved
        past = self.t.mm_targets_hist
        if not past:
            return None
        picks = ded.sample(random.Random(f'{s["loop"]}:{s["day"]}:{len(past)}'), n)
        if not picks:
            return None
        rules, roles, tot = {}, {}, 0.0
        incs = [dict(i, culprit=None) for i in s['script']['incidents']]
        for combo, rl in picks:
            inv = involved(s, {'rules': list(combo), 'roles': rl, 'incidents': incs})
            w = math.exp(k * sum(t in inv for t in past))
            tot += w
            for r in combo:
                rules[RULE_JA.get(r, r)] = rules.get(RULE_JA.get(r, r), 0) + w
            for c in s['chars']:
                rj = ROLE_JA.get(rl.get(c, 'PERSON'), rl.get(c, 'PERSON'))
                roles.setdefault(name(c), {})
                roles[name(c)][rj] = roles[name(c)].get(rj, 0) + w
        return {'rules': {r: round(v / tot, 3) for r, v in rules.items()},
                'roles': {c: {r: round(v / tot, 3) for r, v in d.items()} for c, d in roles.items()}}


HISTORY = os.path.join(HERE, 'records', 'blind_history.jsonl')  # 目隠しで遊んだ脚本の記録（試合の後に追記。ルールの組は試合後に公開済み）
SEEN_PREFIX = ('gen_s21', 'gen_s22', 'gen_s23', 'gen_s24', 'gen_s25')  # Claude が一覧でルールを見てしまった生成脚本


def pick_blind(rng):
    """Claude が中身を知らない生成脚本を、進行役が選ぶ（ユーザーの方針、2026-09-24）。
    - 遊んだことのあるルールの組（Y＋X2つ）は選ばない（同じルールは出てよいが、組み合わせは変える）。
    - 遊んだ回数が少ないルールほど少し選ばれやすい（重み＝各ルールの 1/(1+回数) の積の平方根）。確実に選ばれるルールは作らない。
    - 選んだ脚本の id もルールも表示しない。"""
    import glob
    from collections import Counter
    hist = [json.loads(l) for l in open(HISTORY, encoding='utf-8')] if os.path.exists(HISTORY) else []
    played_ids = {h['id'] for h in hist}
    played_combos = {tuple(sorted(h['rules'])) for h in hist}
    count = Counter(r for h in hist for r in h['rules'])
    cands, ws = [], []
    from engine.scripts import glob_scripts
    for f in glob_scripts('generated/*.json'):
        sc = json.load(open(f, encoding='utf-8'))
        if sc['id'] in played_ids or sc['id'].startswith(SEEN_PREFIX) or tuple(sorted(sc['rules'])) in played_combos:
            continue
        w = 1.0
        for r in sc['rules']:
            w *= 1 / (1 + count[r])
        try:  # 勝ち筋の厚い脚本ほど選ばれやすく（ユーザーの感想「勝ち筋が少ない脚本は弱い」。selfplay.script_routes）
            from selfplay.script_routes import route_richness
            w *= max(0.1, route_richness(by_id('generated/' + sc['id']))['spread'])
        except Exception:
            pass
        cands.append('generated/' + sc['id'])
        ws.append(w ** 0.5)
    if not cands:
        raise SystemExit('目隠しで選べる脚本が無い（生成で補充する）')
    return rng.choices(cands, weights=ws)[0], len(cands)


THINK = {'loop_setup': 'ループの準備', 'mm_cards': '伏せ札', 'mm_ability': '脚本家能力', 'incident_choice': '事件の対象',
         'refuse': '友好能力を拒否するか', 'killer_pick': '任意能力', 'choose_rule_x': 'ルールの選択'}


def _thinking(t, what, f):
    def g(*a, **k):
        s = a[0] if a and isinstance(a[0], dict) and 'chars' in a[0] else None  # 1日目は盤面がまだ書かれていないので、ここで渡す
        t.show(s, waiting={'kind': 'thinking', 'text': f'脚本家が考えています（{what}）', 'what': what, 'since': time.time(), 'options': []}, bump=False)
        return f(*a, **k)
    return g


def _progress(t, every=0.3):
    """脚本家の進み具合（SearchMastermind._prog）を盤面に書く。書き込みは every 秒に1回まで（最後の 1.0 は必ず）。"""
    last = [0.0]

    def cb(frac, text):
        now = time.time()
        if (t.waiting or {}).get('kind') != 'thinking' or (now - last[0] < every and frac < 1.0):
            return
        last[0] = now
        t.show(waiting={**t.waiting, 'progress': round(frac, 3), 'step': text}, bump=False)
    return cb


def short_id(sid):
    """開始時に出す脚本の ID。最後の _ 以降（例 s03_bomb の bomb、gen_s11_036 の 036）は隠す（名前は内容の手がかりになる）。"""
    head, _, base = sid.rpartition('/')
    return (head + '/' if head else '') + base.rsplit('_', 1)[0]


def list_scripts():
    """開始画面に出す脚本: 同梱（試験用）と設計した脚本。自動生成の脚本は「伏せて選ぶ」で使う。公開情報だけ（ネタバレを出さない）。"""
    from engine.scripts import glob_scripts, load, script_dirs
    out = []
    for group, pat in (('同梱', '*.json'), ('設計', 'designed/*.json')):
        for f in glob_scripts(pat):
            sc = load(f)
            rel = next(os.path.relpath(f, d) for d in script_dirs() if f.startswith(d))
            sid = rel[:-5].replace(os.sep, '/')
            if sid.startswith('toukou_') or sid == 'blind_001':  # 投稿シナリオは手で指定（--script）。目隠しの記録は出さない
                continue
            out.append({'id': sid, 'title': sc.get('title') or sid, 'loops': sc.get('loops'), 'days': sc.get('days'),
                        'set': sc.get('set', 'BTX'), 'group': group})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--script', default='s03_bomb')
    ap.add_argument('--blind', action='store_true', help='脚本を進行役が目隠しで選ぶ（id もルールも表示しない。遊んだ組は選ばない）')
    ap.add_argument('--mm', default='search', help='自動の脚本家（selfplay.league.make の種類）')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--dir', default=os.path.join(HERE, 'game_ai'))
    ap.add_argument('--list', action='store_true', help='開始画面の脚本の一覧（題名・ループ数・日数だけ。役職・犯人は出さない）を JSON で出して終わる')
    a = ap.parse_args()
    if a.list:  # 開始画面用: 選べる脚本と、伏せて選べる脚本（自動生成）の数
        from engine.scripts import glob_scripts
        print(json.dumps({'scripts': list_scripts(), 'blind': len(glob_scripts('generated/*.json'))}, ensure_ascii=False))
        return
    if a.blind:
        a.script, n = pick_blind(random.Random(f'blind:{a.seed}:{time.time()}'))
        os.makedirs(os.path.join(a.dir, 'private'), exist_ok=True)
        json.dump({'script': a.script}, open(os.path.join(a.dir, 'private', 'pick.json'), 'w'))  # Claude は試合中に読まない
        print(f'目隠しで脚本を選んだ（候補 {n} 本）')
    script = by_id(a.script)
    t = Table(a.dir, script)
    rng = random.Random(a.seed)
    pc = HumanProtagonist(t, rng)
    if a.mm == 'claude':  # Claude が脚本家（問いは private/ に書くので盤面からは見えない）
        from selfplay.play_cli import BlockingMastermind
        mm = BlockingMastermind(os.path.join(a.dir, 'private', 'mm_question.txt'), os.path.join(a.dir, 'private', 'mm_decisions.jsonl'),
                                make('search', rng, script))
    else:
        mm = make(a.mm, rng, script)
    # 脚本家が考えている間は、盤面に「考え中」を出す（何を考えているかと開始時刻。calcG は1手に数十秒かかる）
    for meth, what in THINK.items():
        f = getattr(mm, meth, None)
        if callable(f):
            setattr(mm, meth, _thinking(t, what, f))
    mm.progress = _progress(t)
    player = Combo(mm, pc)
    nar = Narrator(t)
    # 題名は内容の手がかりになるので、開始時は ID だけ出す（題名はゲーム終了時の非公開シートの公開で出す）
    t.log(f'対AIモード開始。脚本 {short_id(a.script)}（ループ {script["loops"]} 回・1ループ {script["days"]} 日・'
          f'{script.get("set", "BTX")}）。脚本家は' + ('Claude' if a.mm == 'claude' else f'自動（{a.mm}）'))

    def log0(kind, rec):
        if rec.get('phase') in PHASE_JA and rec.get('phase') != 'ability':  # 主人公能力フェイズは宣言の受付の前に出す
            t.phase(rec.get('loop'), rec.get('day'), rec['phase'])
        # 行動解決では、脚本家の伏せ札も表向きになる（効果の無かった札も公開される＝推理の材料）
        if kind == 'decisions' and rec.get('kind') == 'place_cards' and rec.get('who') == 'M':
            t.log('脚本家の札が公開された: ' + '、'.join(f'{name(x["target"])}={CARD_UI.get(x["card"], x["card"])}' for x in rec['choice']))
        if rec.get('phase') == 'final_battle':
            t.log('最後の戦い: ' + ('主人公の勝ち' if rec['result']['winner'] == 'protagonists' else
                                 f'{rec["result"].get("stopped_at", 0) + 1} 人目の指摘で外れた。脚本家の勝ち'))
    r = play_game(script, player, log0, observers=(nar,))
    winner = '主人公' if r.get('winner') == 'protagonists' else '脚本家'
    how = '最後の戦い' if r.get('loop') == 'final' else f'ループ{r.get("loop")}を守りきった'
    t.log(f'ゲーム終了: {winner}の勝ち（{how}）')
    t.log(f'非公開シート: 脚本「{script.get("title") or a.script}」（{a.script}） ／ ルール ' + '・'.join(RULE_JA.get(x, x) for x in script['rules']) + ' ／ 配役 '
          + '・'.join(f'{name(c)}={ROLE_JA.get(v, v)}' for c, v in script['roles'].items()) + ' ／ 犯人 '
          + '・'.join(f'{i["day"]}日 {INC_JA.get(i["id"], i["id"])}={name(i["culprit"])}' for i in script['incidents']))
    if a.blind:  # 試合の後に、遊んだルールの組を記録する（次の目隠しの選択で同じ組を避ける）
        with open(HISTORY, 'a', encoding='utf-8') as fp:
            fp.write(json.dumps({'id': a.script.split('/')[-1], 'rules': script['rules'], 'mm': a.mm, 'winner': r.get('winner')}, ensure_ascii=False) + '\n')
    t.show(phase_name=f'ゲーム終了 ― {winner}の勝ち', waiting={'kind': 'none', 'text': 'ゲーム終了。非公開シートを公開ログに出した', 'options': []})


if __name__ == '__main__':
    main()
