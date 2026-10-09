"""段4: 主人公の友好能力（主人公能力フェイズ、主人公の書 印刷p23 4-6）と最後の戦い（印刷p25 §6）。

文面はキャラクターカード（wiki/characters/character-01〜09.md、画像照合済み）。
- 宣言はリーダーが1件ずつ。同じ能力はそのフェイズに1回、★は1ループ1回
- 友好無視を持つ役職は拒否してもよい（脚本家の判断）、絶対友好無視は必ず拒否。拒否されても★は使用済み（印刷p23）
- 役職を明かす能力は、変更後の役職を返す（公式FAQ XY01）。死体は能力の対象にならない（公式FAQ）
- 同一エリアは自身を含む。含まない場合はカードに「自身以外」と書かれる（公式FAQ Ch01）
"""
from .phases import alive_in, role, base_role
from .resolve import CHARS, IllegalPlacement, copy_state

# (cid, 番号): 必要な友好, 1ループ1回, 入力の型
ABILITIES = {
    ('C01', 0): (2, False, 'char'), ('C02', 0): (2, False, 'char'), ('C03', 0): (3, False, 'char'),
    ('C04', 0): (3, False, 'none'), ('C04', 1): (5, True, 'char'),
    ('C05', 0): (4, True, 'incident'), ('C05', 1): (5, True, 'char'),
    ('C06', 0): (3, False, 'none'), ('C07', 0): (5, True, 'ruleX'),
    ('C08', 0): (2, False, 'doctor'), ('C08', 1): (3, False, 'none'),
    # 第1陣（wiki/characters/character-10〜27、カード画像で照合済み。境界例 tests/cases/chars_batch1.json）
    ('C10', 0): (2, True, 'card'),
    ('C12', 0): (4, True, 'char'), ('C12', 1): (5, True, 'corpse'),
    ('C14', 0): (3, False, 'char'), ('C14', 1): (4, False, 'char'),
    ('C15', 0): (2, False, 'any'), ('C15', 1): (2, False, 'char_or_board'),
    ('C17', 0): (2, False, 'char'),
    ('C21', 0): (2, True, 'transfer'), ('C21', 1): (5, True, 'corpse_any'),
    ('C23', 0): (3, False, 'student'), ('C23', 1): (4, True, 'student'),
    ('C25', 0): (2, True, 'char'), ('C25', 1): (5, True, 'none'),
    ('C27', 0): (1, False, 'none'), ('C27', 1): (3, True, 'board'),
    # 第2陣a（特性を持つキャラクター。境界例 tests/cases/chars_batch2a.json）
    ('C13', 0): (3, True, 'sheet_incident'), ('C13', 1): (5, False, 'char_or_board'),
    ('C18', 0): (3, False, 'none'),
    ('C19', 0): (3, False, 'none'),
    ('C22', 0): (3, True, 'sheet_incident'),
    ('C24', 0): (2, False, 'char'),
    ('C29', 0): (3, False, 'critical_any'), ('C29', 1): (4, True, 'critical_here'),
    ('C31', 0): (5, False, 'proxy'),
    # 第2陣b（境界例 tests/cases/chars_batch2b.json）
    ('C11', 0): (3, False, 'loop2'), ('C28', 0): (3, False, 'loop2'),
    ('C16', 0): (5, True, 'territory'),
    ('C20', 0): (3, True, 'char_to_board'), ('C20', 1): (4, False, 'none'),
    ('C30', 0): (0, False, 'tree'),  # ご神木の特性（友好能力ではないが、主人公能力フェイズの宣言として扱う）
    ('C33', 0): (3, True, 'char'),
    ('C34', 0): (4, True, 'any_other'),
}
NOT_SELF = {('C01', 0), ('C02', 0), ('C08', 0), ('C12', 0), ('C14', 0), ('C14', 1), ('C15', 0), ('C17', 0), ('C24', 0),
            ('C29', 0), ('C29', 1)}  # カードに「自身以外」とある能力（FAQ Ch01）
UNREFUSABLE = {('C17', 0), ('C31', 0), ('C11', 0), ('C28', 0), ('C30', 0)}  # 妹[5] の代理の使用も拒否されない  # ナース「この能力は友好無視や絶対友好無視で拒否されない」
ADJ = {'HOS': {'SHR', 'CIT'}, 'SHR': {'HOS', 'SCH'}, 'CIT': {'HOS', 'SCH'}, 'SCH': {'SHR', 'CIT'}}  # 縦横で隣り合うボード（盤面 印刷p19）
COUNTERS = ('gw', 'par', 'int', 'guard')
REFUSAL = {'KILLER': 'optional', 'KUROMAKU': 'optional', 'FACTOR': 'optional', 'CULTIST': 'absolute', 'WITCH': 'absolute'}


def check_declaration(state, cid, idx, arg, ignore_gw=False, proxy=False):
    """宣言が合法か（公開情報だけで判定できる部分）。非合法なら IllegalPlacement。"""
    key = (cid, idx)
    if key not in ABILITIES:
        raise IllegalPlacement(f'{cid} に能力 {idx} は無い')
    need, once, kind = ABILITIES[key]
    ch = state['chars'][cid]
    tag = f'{cid}#{idx}'
    if not ch['alive']:
        raise IllegalPlacement(f'{cid} は死亡している')
    if not ch.get('present', True):
        raise IllegalPlacement(f'{cid} は盤面にいない')
    if ch['gw'] < need and not ignore_gw:
        raise IllegalPlacement(f'{cid} の友好 {ch["gw"]} < {need}')
    if tag in state.setdefault('ability_used_today', []) and not proxy:  # 妹[5] 経由なら同じフェイズの2回目も可（裁定）
        raise IllegalPlacement('同じ能力はこのフェイズで使用済み（印刷p23）')
    if once and tag in state.setdefault('ability_used_loop', []):
        raise IllegalPlacement('★1ループ1回の能力は使用済み')
    area = ch['area']
    if cid == 'C03' and area not in ('SCH', 'CIT'):
        raise IllegalPlacement('お嬢様は学校か都市にいないと使えない')
    if key == ('C04', 0) and area != 'SHR':
        raise IllegalPlacement('巫女[3]は神社にいないと使えない')
    if kind in ('critical_any', 'critical_here'):  # 教祖: 自身以外の、不安臨界以上の不安が置かれたキャラクター
        tc = state['chars'].get((arg or {}).get('target'))
        if tc is None or not tc['alive'] or not tc.get('present', True):
            raise IllegalPlacement('対象は盤面にいる生存キャラクター')
        if arg['target'] == cid:
            raise IllegalPlacement('「自身以外」の能力')
        if tc['par'] < CHARS[arg['target']]['limit'] or tc['par'] < 1:  # 不安が1つ以上必要（裁定: 臨界0・不安0は選べない）
            raise IllegalPlacement('対象は不安臨界以上の不安が置かれたキャラクター')
        if kind == 'critical_here' and tc['area'] != area:
            raise IllegalPlacement('対象は同一エリア')
    if kind == 'loop2' and state['loop'] < 2:
        raise IllegalPlacement('第2ループ以降でないと使えない')
    if kind == 'territory':
        tc = state['chars'].get((arg or {}).get('target'))
        if tc is None or not tc['alive'] or arg['target'] == cid or tc['area'] != state['script'].get('territory'):
            raise IllegalPlacement('テリトリーにいる自身以外の生存キャラクター')
    if kind == 'char_to_board':
        tc = state['chars'].get((arg or {}).get('target'))
        if tc is None or not tc['alive'] or tc['area'] != area or not tc.get('present', True):
            raise IllegalPlacement('対象は同一エリアの生存キャラクター')
        if arg.get('board') not in ('HOS', 'SHR', 'CIT', 'SCH'):
            raise IllegalPlacement('移動先のボードを選ぶ')
    if kind == 'tree':
        tc = state['chars'].get((arg or {}).get('target'))
        if tc is None or not tc['alive'] or arg['target'] == cid:
            raise IllegalPlacement('同一エリアの他のキャラクター')
        if tc['area'] != area:
            raise IllegalPlacement('同一エリアではない')
        if arg.get('counter') not in COUNTERS or ch.get(arg['counter'], 0) < 1:
            raise IllegalPlacement('ご神木の上のカウンターを選ぶ')
    if kind == 'any_other':
        tc = state['chars'].get((arg or {}).get('target'))
        if tc is None or not tc['alive'] or not tc.get('present', True) or arg['target'] == cid:
            raise IllegalPlacement('ボードにいる自身以外の生存キャラクター')
    if kind == 'sheet_incident':  # 神格[3]・A.I.[3]: 公開シートに書かれた事件（日付と名前で指定）
        if not any(i['id'] == arg.get('incident') and i['day'] == arg.get('day') for i in state['script']['incidents']):
            raise IllegalPlacement('公開シートに無い事件')
    if kind == 'proxy':  # 妹[5]: 同一エリアの大人1人が友好能力1つを、十分な友好があるかのように使う
        tgt = (arg or {}).get('target')
        tc = state['chars'].get(tgt)
        if tc is None or not tc['alive'] or tc['area'] != area or not tc.get('present', True):
            raise IllegalPlacement('対象は同一エリアの生存キャラクター（死体は対象にできない）')
        if '大人' not in CHARS[tgt]['tags']:
            raise IllegalPlacement('対象は大人')
        check_declaration(state, tgt, arg.get('idx'), arg.get('arg'), ignore_gw=True, proxy=True)
    if kind in ('char', 'doctor', 'any', 'student') or (kind == 'char_or_board' and not str((arg or {}).get('target', '')).startswith('B:')):
        t = (arg or {}).get('target')
        tc = state['chars'].get(t)
        if tc is None or not tc['alive']:
            raise IllegalPlacement('対象は生存しているキャラクター（死体は対象外）')
        if kind != 'any' and tc['area'] != area:
            raise IllegalPlacement('対象は同一エリア')
        if key in NOT_SELF and t == cid:
            raise IllegalPlacement('「自身以外」の能力')
        if (cid in ('C01', 'C02') or kind == 'student') and '学生' not in CHARS[t]['tags']:
            raise IllegalPlacement('対象は学生')
        if key == ('C17', 0) and (tc['par'] < CHARS[t]['limit'] or tc['par'] < 1):
            raise IllegalPlacement('ナースの対象は不安臨界以上の不安が置かれたキャラクター')
    if kind == 'char_or_board' and str(arg.get('target', '')).startswith('B:') and arg['target'][2:] != area:
        raise IllegalPlacement('ボードは自身のいるボードだけ')
    if kind in ('corpse', 'corpse_any'):
        tc = state['chars'].get((arg or {}).get('target'))
        if tc is None or tc['alive']:
            raise IllegalPlacement('対象は死体')
        if kind == 'corpse' and tc['area'] != area:
            raise IllegalPlacement('対象は同一エリアの死体')
    if kind == 'card':
        from .resolve import ONCE
        leader = state['leader']
        c = (arg or {}).get('card')
        if c not in state['used'][leader] or c not in ONCE[leader]:
            raise IllegalPlacement('リーダーの、1ループ1回制限かつ使用済みの行動カードを選ぶ（印刷p37「行えない場合、その能力は使えません」）')
    if kind == 'transfer':
        f, to, k = arg.get('from'), arg.get('to'), arg.get('counter')
        others = [c for c in alive_in(state, area) if c != cid]
        if f == to or f not in others or to not in others:
            raise IllegalPlacement('鑑識官の同一エリアの自身以外のキャラクター2人を選ぶ')
        has = any(state['chars'][x].get(c, 0) > 0 for x in (f, to) for c in COUNTERS)
        if k is None:
            if has:
                raise IllegalPlacement('カウンターがあれば1つ選ぶ（印刷p40「可能であれば」）')
        elif k not in COUNTERS or state['chars'][f].get(k, 0) < 1:
            raise IllegalPlacement('移動元に置かれたカウンターを選ぶ')
    if kind == 'board':
        b = (arg or {}).get('board')
        if b not in ADJ[area]:
            raise IllegalPlacement('現在地と縦横で隣り合うボード')
    if (kind == 'doctor' or key == ('C23', 0)) and arg.get('mode') not in ('place', 'remove'):
        raise IllegalPlacement("医者[2] は 'place'（置く）か 'remove'（取り除く）")
    if kind == 'ruleX':
        # 宣言は、使っている惨劇セットのルールXの名前（印刷p39 情報屋）
        from .phases import RULES
        if RULES.get(arg.get('declare'), {}).get('type') != 'X':
            raise IllegalPlacement('情報屋が宣言できるのは惨劇セットのルールXの名前だけ（印刷p39）')
    if kind == 'incident':
        if not any(i['id'] == arg['incident'] and arg.get('day', d) == d for i, d in _occurred_this_loop(state)):
            raise IllegalPlacement('刑事[4]はこのループで発生した事件だけ')
    return need, once, kind


def use_ability(state, cid, idx, arg, refuse=False, proxy=False):
    """誤った宣言（IllegalPlacement）では状態を変えない。"""
    import copy
    snap = copy_state(state)
    try:
        return _use_ability(state, cid, idx, arg, refuse, proxy)
    except IllegalPlacement:
        state.clear()
        state.update(snap)
        raise


def _use_ability(state, cid, idx, arg, refuse=False, proxy=False):
    """合法な宣言を解決するか拒否する。refuse は脚本家の判断（友好無視の役職だけが選べる）。
    proxy: 妹[5] による代理の使用（友好の条件を無視し、拒否されない）。返り値は出来事のリスト。"""
    need, once, kind = check_declaration(state, cid, idx, arg, ignore_gw=proxy, proxy=proxy)
    if kind == 'proxy':
        if refuse:
            raise IllegalPlacement('この能力は友好無視や絶対友好無視で拒否されない')
        state.setdefault('ability_used_today', []).append(f'{cid}#{idx}')
        return _use_ability(state, arg['target'], arg['idx'], arg.get('arg'), proxy=True)
    if proxy:
        refuse = False
    # 拒否の可否は、状態を変える前に確かめる（誤った宣言では状態を変えない）
    r = role(state, cid)
    mode = REFUSAL.get(r)
    if (cid, idx) in UNREFUSABLE or proxy:
        if refuse:
            raise IllegalPlacement('この能力は友好無視や絶対友好無視で拒否されない')
        mode = None
    if mode == 'absolute':
        refuse = True
    elif mode is None and refuse:
        raise IllegalPlacement(f'{cid} は友好無視を持たないので拒否できない')
    tag = f'{cid}#{idx}'
    state['ability_used_today'].append(tag)
    if once:
        state['ability_used_loop'].append(tag)  # 拒否されても使用済み（印刷p23）
    if refuse:
        return [{'kind': 'refused', 'char': cid, 'ability': idx}]
    area = state['chars'][cid]['area']
    t = (arg or {}).get('target')
    ev = {'kind': 'ability', 'char': cid, 'ability': idx}
    if cid in ('C01', 'C02'):
        state['chars'][t]['par'] = max(0, state['chars'][t]['par'] - 1); ev['par-'] = t
    elif cid == 'C03':
        state['chars'][t]['gw'] += 1; ev['gw+'] = t
    elif (cid, idx) == ('C04', 0):
        state['boards']['SHR'] = max(0, state['boards']['SHR'] - 1); ev['int-'] = 'B:SHR'
    elif (cid, idx) == ('C04', 1):
        ev['reveal_role'] = {t: role(state, t)}
        state.setdefault('revealed_roles', {})[t] = role(state, t)  # フレンドのループ開始時の友好（FAQ Ch21）
    elif (cid, idx) == ('C05', 0):
        # 同じ名前の事件が2つある脚本では日付で選ぶ（日付が無ければ最初の1つ）
        inc = next(i for i in state['script']['incidents'] if i['id'] == arg['incident'] and arg.get('day', i['day']) == i['day'])
        ev['reveal_culprit'] = {arg['incident']: inc['culprit']}
    elif (cid, idx) == ('C05', 1):
        state['chars'][t]['guard'] += 1; ev['guard+'] = t
    elif cid == 'C06':
        ev['reveal_role'] = {cid: role(state, cid)}
        state.setdefault('revealed_roles', {})[cid] = role(state, cid)
    elif cid == 'C07':
        xs = [x for x in state['script']['rules'] if x.startswith('X_') and x != arg['declare']]
        ev['reveal_rule_x'] = xs[0] if len(xs) == 1 else {'choose_from': xs}  # 2つ残れば脚本家が選ぶ
    elif (cid, idx) == ('C08', 0):
        c = state['chars'][t]
        c['par'] = c['par'] + 1 if arg['mode'] == 'place' else max(0, c['par'] - 1); ev['par'] = (t, arg['mode'])
    elif (cid, idx) == ('C08', 1):
        state.setdefault('unbound', []).append('C09'); ev['unbound'] = 'C09'
    elif cid == 'C10':
        state['used'][state['leader']].remove(arg['card']); ev['returned'] = arg['card']
    elif (cid, idx) == ('C12', 0):
        from .phases import kill, check_key_death
        evs = [ev]
        kill(state, t, f'ABILITY:{cid}', evs)
        check_key_death(state, evs)
        return evs
    elif (cid, idx) == ('C12', 1):
        state['chars'][t]['alive'] = True; ev['revive'] = t
    elif (cid, idx) == ('C14', 0) or cid == 'C17':
        state['chars'][t]['par'] = max(0, state['chars'][t]['par'] - 1); ev['par-'] = t
    elif (cid, idx) == ('C14', 1):
        state['chars'][t]['gw'] += 1; ev['gw+'] = t
    elif (cid, idx) == ('C15', 0):
        state['chars'][t]['par'] += 1; ev['par+'] = t
    elif (cid, idx) == ('C15', 1):
        if t.startswith('B:'):
            state['boards'][t[2:]] += 1
        else:
            state['chars'][t]['int'] += 1
        ev['int+'] = t
    elif (cid, idx) == ('C21', 0):
        k = arg.get('counter')
        if k:
            state['chars'][arg['from']][k] -= 1
            state['chars'][arg['to']][k] = state['chars'][arg['to']].get(k, 0) + 1
        ev['transfer'] = (arg['from'], arg['to'], k)
    elif (cid, idx) == ('C21', 1):
        # 死体の役職。妄想拡大ウイルスは「キャラクターは」なので死体には効かない読み（字面。裁定待ち）
        ev['reveal_role'] = {t: base_role(state, t)}
        state.setdefault('revealed_roles', {})[t] = base_role(state, t)
    elif (cid, idx) == ('C23', 0):
        c = state['chars'][t]
        c['par'] = c['par'] + 1 if arg.get('mode') == 'place' else max(0, c['par'] - 1); ev['par'] = (t, arg.get('mode'))
    elif (cid, idx) == ('C23', 1):
        ev['reveal_role'] = {t: role(state, t)}
        state.setdefault('revealed_roles', {})[t] = role(state, t)
    elif (cid, idx) == ('C25', 0):
        state['chars'][t]['par'] += 2; ev['par+2'] = t
    elif (cid, idx) == ('C25', 1):
        state['protagonists_immortal'] = True; ev['immortal'] = True
    elif (cid, idx) == ('C27', 0):
        state.setdefault('unbound', []).append('C27'); ev['unbound'] = 'C27'
    elif (cid, idx) == ('C27', 1):
        state['chars'][cid]['area'] = arg['board']; ev['move'] = arg['board']
    elif (cid, idx) == ('C13', 0):
        inc = next(i for i in state['script']['incidents'] if i['id'] == arg['incident'] and i['day'] == arg['day'])
        ev['reveal_culprit'] = {arg['incident']: inc['culprit']}
    elif (cid, idx) == ('C13', 1):
        if t.startswith('B:'):
            state['boards'][t[2:]] = max(0, state['boards'][t[2:]] - 1)
        else:
            state['chars'][t]['int'] = max(0, state['chars'][t]['int'] - 1)
        ev['int-'] = t
    elif cid == 'C18':
        state.setdefault('suppressed_culprits', []).append('C18'); ev['suppress'] = 'C18'
    elif cid == 'C19':
        for k in ('par', 'gw', 'int', 'guard'):  # 護衛を含むかは裁定待ち（字面で含める）
            state['chars'][cid][k] = 0
        ev['clear'] = cid
    elif cid == 'C22':
        # 公開シートの事件1つを、犯人が A.I. であるかのように解決する。脚本家が行う選択はリーダーが行う（カード）。発生には数えない
        from .phases import incident_effect, check_key_death
        evs = [ev]
        incident_effect(state, arg['incident'], cid, arg.get('choice'), evs)
        check_key_death(state, evs)
        return evs
    elif cid == 'C24':
        c = state['chars'][t]
        if c['int'] > 0:
            c['int'] -= 1
            c['gw'] += 1
        ev['int_to_gw'] = t
    elif (cid, idx) == ('C29', 0):
        state['chars'][t]['gw'] += 1; ev['gw+'] = t
    elif cid == 'C11':
        ev['reveal_role'] = {cid: role(state, cid)}
        state.setdefault('revealed_roles', {})[cid] = role(state, cid)
    elif cid == 'C28':
        me = role(state, cid)
        ev['reveal_same_role'] = {cid: [c for c, v in state['chars'].items()  # 死体・未登場は含まない（作者回答＝裁定）
                                        if c != cid and v['alive'] and v.get('present', True) and role(state, c) == me]}
    elif cid == 'C16':
        ev['reveal_role'] = {t: role(state, t)}
        state.setdefault('revealed_roles', {})[t] = role(state, t)
    elif (cid, idx) == ('C20', 0):
        from .phases import servant_follow
        before = {c: v['area'] for c, v in state['chars'].items() if v['alive'] and v.get('present', True)}
        state['chars'][t]['area'] = arg['board']; ev['move'] = (t, arg['board'])
        evs = [ev]
        servant_follow(state, before, evs)
        return evs
    elif (cid, idx) == ('C20', 1):
        state['chars'][cid].update(present=False, area=None); ev['removed'] = cid
    elif cid == 'C30':
        k = arg['counter']
        state['chars'][cid][k] -= 1
        state['chars'][t][k] = state['chars'][t].get(k, 0) + 1
        ev['transfer'] = (cid, t, k)
    elif cid == 'C33':
        ev['reveal_role'] = {cid: role(state, cid)}
        state.setdefault('revealed_roles', {})[cid] = role(state, cid)
        state['chars'][t]['gw'] += 2
    elif cid == 'C34':
        state.setdefault('servant_targets', []).append(t); ev['servant_target'] = t
    elif (cid, idx) == ('C29', 1):
        ev['reveal_role'] = {t: role(state, t)}
        state.setdefault('revealed_roles', {})[t] = role(state, t)
    return [ev]


def final_battle_answer(state, cid, guess):
    """最後の戦いで当てるのは配役（公式FAQ XY02: 妄想拡大ウイルスでも当てるのはパーソン）。
    アルバイトとアルバイト？は1回だけ宣言し、正解は脚本上のアルバイトの配役（裁定）。"""
    if cid in ('C32', 'C33'):
        return state['script']['roles'].get('C32', 'PERSON') == guess
    return base_role(state, cid) == guess


def final_battle(state, guesses):
    """最後の戦い（主人公の書 印刷p25 §6）。盤面の再構築（全カウンター除去・初期エリアに生存で配置）の後、
    主人公がキャラクターを1人ずつ選んで役職を宣言する。外れたらその時点で脚本家の勝ち、
    登場する全キャラクターを当てれば主人公の勝ち。当てるのは配役（FAQ XY02）。
    guesses: [{'char','role'}] を宣言順に。返り値: {'winner', 'stopped_at'}。"""
    chars = list(state['chars'])
    if 'C32' in chars and 'C33' in chars:  # 2人で1回の宣言（裁定）
        chars.remove('C33')
        guesses = [dict(g, char='C32') if g['char'] == 'C33' else g for g in guesses]
    named = set()
    for i, g in enumerate(guesses):
        if g['char'] in named:
            raise IllegalPlacement('同じキャラクターは2度宣言しない（「宣言されていない別のキャラクター」印刷p25）')
        named.add(g['char'])
        if not final_battle_answer(state, g['char'], g['role']):
            return {'winner': 'mastermind', 'stopped_at': i}
    if named != set(chars):
        raise IllegalPlacement('全キャラクターについて宣言していない')
    return {'winner': 'protagonists', 'stopped_at': None}


def _simple_args(state, cid, idx):
    """妹[5] の代理の使用で試す引数（対象を取る能力は同一エリアの生存者、それ以外は None）。"""
    kind = ABILITIES[(cid, idx)][2]
    here = alive_in(state, state['chars'][cid]['area'])
    if kind in ('char', 'student'):
        return [{'target': x} for x in here]
    if kind == 'doctor':
        return [{'target': x, 'mode': m} for x in here for m in ('place', 'remove')]
    return [None]


def _occurred_this_loop(state):
    """このループで発生した事件の [(記録, 日付)]。日付の記録が無い古い状態では日付は None。"""
    log, days = state.get('incident_log', []), state.get('incident_day_log', [])
    days = days if len(days) == len(log) else [None] * len(log)
    return [(i, d) for i, d in zip(log, days) if i['loop'] == state['loop'] and i['occurred']]


def declaration_options(state):
    """いま合法な宣言 (cid, idx, arg) の一覧（主人公能力フェイズ。公開情報だけで決まる）。自己対戦のプレイヤーが使う。"""
    from .phases import AREAS, RULES
    out = []
    for (cid, idx), (need, once, kind) in ABILITIES.items():
        ch = state['chars'].get(cid)
        if ch is None or not ch['alive'] or not ch.get('present', True):
            continue
        area = ch['area']
        here = alive_in(state, area)
        alive = [c for c, v in state['chars'].items() if v['alive'] and v.get('present', True)]
        dead = [c for c, v in state['chars'].items() if not v['alive']]
        if kind == 'doctor' or (cid, idx) == ('C23', 0):  # 教師[3] は学生が対象でも置く／取り除くを選ぶ（先に見ないと 'student' に吸われて候補が0だった）
            args = [{'target': x, 'mode': m} for x in here for m in ('place', 'remove')]
        elif kind in ('char', 'student'):
            args = [{'target': x} for x in here]
        elif kind == 'any':
            args = [{'target': x} for x in alive]
        elif kind == 'char_or_board':
            args = [{'target': x} for x in here] + [{'target': 'B:' + area}]
        elif kind == 'corpse':
            args = [{'target': x} for x in dead if state['chars'][x]['area'] == area]
        elif kind == 'corpse_any':
            args = [{'target': x} for x in dead]
        elif kind == 'incident':
            args = [{'incident': i['id'], 'day': d} for i, d in _occurred_this_loop(state)]
        elif kind == 'ruleX':
            args = [{'declare': x} for x, v in RULES.items() if v['type'] == 'X']
        elif kind == 'card':
            args = [{'card': c} for c in state['used'][state['leader']]]
        elif kind == 'transfer':
            others = [x for x in here if x != cid]
            args = [{'from': f, 'to': t, 'counter': k} for f in others for t in others if f != t
                    for k in (*COUNTERS, None)]
        elif kind == 'board':
            args = [{'board': b} for b in AREAS]
        elif kind == 'critical_any':
            args = [{'target': x} for x in alive]
        elif kind == 'critical_here':
            args = [{'target': x} for x in here]
        elif kind == 'sheet_incident':
            incs = [(i['id'], i['day']) for i in state['script']['incidents']]
            if cid == 'C22':  # A.I.: 選択の要らない事件と、殺人事件（同一エリアの対象）だけを候補にする（ponytail: 全選択の列挙は重い）
                args = [{'incident': i, 'day': d, 'choice': None} for i, d in incs if i in ('HOSPITAL', 'SUICIDE', 'CORRUPT')]
                args += [{'incident': i, 'day': d, 'choice': {'target': x}} for i, d in incs if i == 'MURDER' for x in here if x != cid]
            else:
                args = [{'incident': i, 'day': d} for i, d in incs]
        elif kind == 'proxy':
            args = [{'target': x, 'idx': j, 'arg': a2} for x in here if x != cid and '大人' in CHARS[x]['tags']
                    for (c2, j) in ABILITIES if c2 == x
                    for a2 in _simple_args(state, x, j)]
        else:
            args = [None]
        for a in args:
            try:
                check_declaration(state, cid, idx, a)
                out.append((cid, idx, a))
            except (IllegalPlacement, KeyError, TypeError):
                pass
    return out
