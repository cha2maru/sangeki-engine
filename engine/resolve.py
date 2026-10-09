"""行動解決フェイズ（フェイズ4）。主人公の書 印刷p21〜22 4-2〜4-4、手札リスト 印刷p42・p43。

resolve_actions(state, placements) -> (new_state, events)
置き方が非合法なら IllegalPlacement を送出し、状態は変えない。
判断は含まない（札はすでに置かれている）。役職による例外は、カルティスト（任意、optional で受け取る）とタイムトラベラー（強制）。
"""
import copy
import json
import os

_DATA = os.path.join(os.path.dirname(__file__), 'data')
CARDS = json.load(open(os.path.join(_DATA, 'cards.json'), encoding='utf-8'))
CHARS = json.load(open(os.path.join(_DATA, 'characters.json'), encoding='utf-8'))['characters']
GRID = json.load(open(os.path.join(_DATA, 'board.json'), encoding='utf-8'))['grid']

PROTAGONISTS = ('A', 'B', 'C')
MOVES = ('MV_V', 'MV_H', 'MV_D')
# 1ループ1回の札。主人公の不安−1は主人公だけが制限を持つ（印刷p42／p43）
ONCE = {'A': {'PAR-', 'GW2', 'MVX'}, 'B': {'PAR-', 'GW2', 'MVX'}, 'C': {'PAR-', 'GW2', 'MVX'}, 'M': {'INT2', 'MV_D'}}


class IllegalPlacement(ValueError):
    pass


def _pos(area):
    for r, row in enumerate(GRID):
        if area in row:
            return r, row.index(area)
    raise KeyError(area)


def destination(area, direction):
    """V=上下、H=左右、D=斜め。2x2 なので行・列を反転するだけ（印刷p19 の配置）。"""
    r, c = _pos(area)
    if direction == 'V':
        r = 1 - r
    elif direction == 'H':
        c = 1 - c
    elif direction == 'D':
        r, c = 1 - r, 1 - c
    return GRID[r][c]


def combine_moves(cards):
    """重なった移動札から実際の方向を決める。印刷p42・43 の各移動札の文面:
    ↑↓+←→=斜め、↑↓+斜め=左右、←→+斜め=上下、同じ方向どうし=その方向へ通常どおり1回（元に戻らない）。"""
    dirs = {'MV_V': 'V', 'MV_H': 'H', 'MV_D': 'D'}
    ds = sorted({dirs[c] for c in cards})
    if len(ds) == 1:
        return ds[0]
    if len(ds) == 2:
        return ({'V', 'H', 'D'} - set(ds)).pop()
    raise IllegalPlacement('移動札が3方向重なることはない（1対象に置ける札は各陣営1枚）')


def validate(state, placements):
    mm = [p for p in placements if p['by'] == 'M']
    pc = [p for p in placements if p['by'] in PROTAGONISTS]
    if len(mm) != 3:
        raise IllegalPlacement(f'脚本家の札は3枚（{len(mm)}枚）― 印刷p21 4-2')
    if len({p['target'] for p in mm}) != 3:
        raise IllegalPlacement('脚本家が同じ対象に2枚以上置いた ― 印刷p21 4-2')
    if len(pc) != 3 or sorted(p['by'] for p in pc) != list(PROTAGONISTS):
        raise IllegalPlacement('主人公は1人1枚ずつ計3枚 ― 印刷p21 4-3')
    if len({p['target'] for p in pc}) != 3:
        raise IllegalPlacement('主人公が他の主人公の置いた対象に置いた ― 印刷p22')
    hand_m = list(CARDS['mastermind_hand'])
    for p in placements:
        t, card, by = p['target'], p['card'], p['by']
        if t.startswith('B:'):
            if t[2:] not in state['boards']:
                raise IllegalPlacement(f'{t} というボードは無い')
        else:
            ch = state['chars'].get(t)
            if ch is None or not ch.get('present', True):
                raise IllegalPlacement(f'{t} は盤上にいない')
            if t == 'C20':
                raise IllegalPlacement('幻想には行動カードをセットできない（カードの特性）')
            if not ch['alive']:
                raise IllegalPlacement(f'{t} は死体。死体には置けない ― 印刷p21 4-2')
        hand = hand_m if by == 'M' else CARDS['protagonist_hand']
        if card not in hand:
            raise IllegalPlacement(f'{by} の手札に {card} は無い')
        if by == 'M':
            hand_m.remove(card)  # 不安+1 は2枚まで
        if card in ONCE[by] and card in state['used'][by]:
            raise IllegalPlacement(f'{by} の {card} はこのループで使用済み ― 印刷p22 4-4')


def card_targets(state):
    """行動カードをセットできる対象: 盤面にいる生存キャラクター（幻想を除く＝カードの特性）と4つのボード。"""
    return [c for c, v in state['chars'].items() if v['alive'] and v.get('present', True) and c != 'C20'] + \
        ['B:' + a for a in ('HOS', 'SHR', 'CIT', 'SCH')]


def _role(state, cid):
    return state.get('script', {}).get('roles', {}).get(cid, 'PERSON')


def copy_state(state):
    """盤面の複製。脚本（script）と初期配置（init）は試合中に書き換えないので共有する。残りは数・文字・dict・list だけなので、
    deepcopy（参照の重複を記録する memo が重い）の代わりに素直に辿って複製する（1試合の時間の半分以上が deepcopy だった）。"""
    return {k: v if k in ('script', 'init') else _fc(v) for k, v in state.items()}


_SCALAR = (str, int, bool, float, type(None))


def _fc(v):
    t = type(v)
    if t in _SCALAR:
        return v
    if t is dict:
        return {k: _fc(x) for k, x in v.items()}
    if t is list:
        return [_fc(x) for x in v]
    if t is tuple:
        return tuple(_fc(x) for x in v)
    return copy.deepcopy(v)


def resolve_actions(state, placements, optional=None, follow=None):
    """optional: 行動解決フェイズの任意能力の使用。現状はカルティストのみ
    [{'char': 'C08', 'ability': 'CULTIST', 'target': 'B:SCH'}]（早見表 役職 カルティスト）。
    follow: 従者が付いていく相手が2人以上で行き先が違うときの、リーダーの選択 {'C34': キャラ}。"""
    validate(state, placements)
    s = copy_state(state)
    events = []
    by_target = {}
    for p in placements:
        by_target.setdefault(p['target'], []).append(p)
    # 幻想【特性】同一エリアのボードにセットされた行動カードの効果は、このキャラクターにも与えられる。
    # 移動の札は移動前のエリアのボードのもの、それ以外の札は移動後のエリアのボードのもの（裁定。移動を先に解決する＝印刷p22）
    il = s['chars'].get('C20')
    il_on = bool(il and il['alive'] and il.get('present', True))
    if il_on:
        for p in by_target.get('B:' + il['area'], []):
            if p['card'] in MOVES + ('MVX',):
                by_target.setdefault('C20', []).append(dict(p, target='C20'))
    before = {c: v['area'] for c, v in s['chars'].items() if v['alive'] and v.get('present', True)}

    # 1) 移動を先に解決する（印刷p22 4-4「キャラクターの移動を行う効果は、他のよりも先に解決」）
    for t, ps in by_target.items():
        if t.startswith('B:'):
            continue
        cards = [p['card'] for p in ps]
        moves = [c for c in cards if c in MOVES]
        if not moves:
            continue
        if 'MVX' in cards:
            events.append({'kind': 'move_blocked', 'char': t, 'cards': moves})
            continue
        d = combine_moves(moves)
        frm = s['chars'][t]['area']
        to = destination(frm, d)
        # 医者[友好3]「このループ中、入院患者は禁止エリアを失い、病院以外に移動できるようになる」（state['unbound'] に入る）
        if to in CHARS.get(t, {}).get('forbidden', []) and t not in s.get('unbound', []):
            # 禁止エリアへは移動しない（印刷p16「キャラクターが移動できないボードである禁止エリア」）
            events.append({'kind': 'move_forbidden', 'char': t, 'from': frm, 'to': to, 'cards': moves})
            continue
        s['chars'][t]['area'] = to
        events.append({'kind': 'move', 'char': t, 'from': frm, 'to': to, 'dir': d, 'cards': moves})

    # 従者【特性】同一エリアのお嬢様・大物が移動したら、自身への移動を無視して一緒に移動する
    from .phases import servant_follow
    if 'C34' in before:
        own = s['chars']['C34']['area']
        s['chars']['C34']['area'] = before['C34']
        if not servant_follow(s, before, events, follow):
            s['chars']['C34']['area'] = own

    if il_on:  # 幻想: 移動の後のエリアのボードの、移動以外の札
        by_target['C20'] = [p for p in by_target.get('C20', []) if p['card'] in MOVES + ('MVX',)] + \
            [dict(p, target='C20') for p in by_target.get('B:' + s['chars']['C20']['area'], []) if p['card'] not in MOVES + ('MVX',)]

    # 暗躍禁止は、複数のプレイヤーが出していると自身が無効（印刷p42 暗躍禁止）
    intx_players = {p['by'] for p in placements if p['card'] == 'INTX'}
    intx_active = len(intx_players) == 1

    # カルティスト【任意：行動解決フェイズ】同一エリアのキャラクター、または自身のいるボードへの暗躍禁止を無視してもよい。
    # 同一エリアかどうかは移動の解決後に判定する（移動が先に解決されるため）
    cultist_ignore = set()
    for o in optional or []:
        if o.get('ability') != 'CULTIST':
            raise IllegalPlacement(f'未対応の任意能力 {o}')
        cid, t = o['char'], o['target']
        cu = s['chars'].get(cid)
        if cu is None or not cu['alive'] or _role(s, cid) != 'CULTIST':
            raise IllegalPlacement(f'{cid} は生存しているカルティストではない')
        in_range = (t == 'B:' + cu['area']) if t.startswith('B:') else (t in s['chars'] and s['chars'][t]['area'] == cu['area'])
        if not in_range:
            raise IllegalPlacement('カルティストと同一エリアでない対象の暗躍禁止は無視できない')
        cultist_ignore.add(t)

    # 2) それ以外の効果
    for t, ps in by_target.items():
        cards = [p['card'] for p in ps]
        is_board = t.startswith('B:')
        cnt = s['boards'] if is_board else s['chars'][t]
        key_int = t[2:] if is_board else 'int'
        # 不安（不安禁止で +1・−1 とも無効。+1 が先に解決される）
        par = [c for c in cards if c in ('PAR+', 'PAR-')]
        if par and not is_board:
            if 'PARX' in cards:
                events.append({'kind': 'nullified', 'target': t, 'cards': par, 'by': 'PARX'})
            else:
                for c in sorted(par, key=lambda c: c != 'PAR+'):
                    before = cnt['par']
                    cnt['par'] = before + 1 if c == 'PAR+' else max(0, before - 1)
                    events.append({'kind': 'par', 'target': t, 'card': c, 'from': before, 'to': cnt['par']})
        # 友好（友好禁止で無効）
        gw = [c for c in cards if c in ('GW1', 'GW2')]
        if gw and not is_board:
            # タイムトラベラー【強制：行動解決フェイズ】このキャラクターへの友好禁止を無視する（早見表）
            if 'GWX' in cards and _role(s, t) != 'TT':
                events.append({'kind': 'nullified', 'target': t, 'cards': gw, 'by': 'GWX'})
            else:
                if 'GWX' in cards:  # 公開: 友好禁止がセットされていたのに友好が置かれた（札は解決後に公開される）
                    events.append({'kind': 'gwx_ignored', 'target': t})
                for c in gw:
                    before = cnt['gw']
                    cnt['gw'] += 2 if c == 'GW2' else 1
                    events.append({'kind': 'gw', 'target': t, 'card': c, 'from': before, 'to': cnt['gw']})
        # 暗躍（暗躍禁止で無効。キャラクターにもボードにも置ける）
        it = [c for c in cards if c in ('INT1', 'INT2')]
        if it:
            if 'INTX' in cards and intx_active and t in cultist_ignore:
                events.append({'kind': 'intx_ignored', 'target': t, 'by': 'CULTIST'})
            if 'INTX' in cards and intx_active and t not in cultist_ignore:
                events.append({'kind': 'nullified', 'target': t, 'cards': it, 'by': 'INTX'})
            else:
                for c in it:
                    before = cnt[key_int]
                    cnt[key_int] += 2 if c == 'INT2' else 1
                    events.append({'kind': 'int', 'target': t, 'card': c, 'from': before, 'to': cnt[key_int]})
        if 'INTX' in cards and not intx_active:
            events.append({'kind': 'intx_void', 'target': t, 'players': sorted(intx_players)})

    # 3) 1ループ1回の札は、無効化されたものも含めて使用済み（印刷p22 4-4「セットされたカード全て」）
    for p in placements:
        if p['card'] in ONCE[p['by']]:
            s['used'][p['by']].append(p['card'])
            events.append({'kind': 'used', 'by': p['by'], 'card': p['card']})
    # 行動解決では6枚すべてが表向きに公開される（wiki/concepts/turn-phases.md、主人公の書 p45）。効果の無かった札も中身が分かる
    events.append({'kind': 'revealed', 'cards': [{'by': p['by'], 'target': p['target'], 'card': p['card']} for p in placements]})
    return s, events
