"""段2・3: 役職の導出、脚本家能力、事件、死亡、ターン終了、ループの開始。

根拠は早見表（originals/summary.pdf、wiki/concepts/role-abilities.md・rule-role-matrix.md で照合済み）、
主人公の書 印刷p20・p23〜25、公式FAQ（Ch01・Ch02・Ch11）、ルール調整 haunted_p12。
判断（任意能力を使うか・対象・事件の効果の対象）はすべて引数で受け取る。
各関数は状態を直接書き換え、出来事（events）のリストを返す。
"""
import copy
import json
import os

from . import sets
from .resolve import CHARS, IllegalPlacement, copy_state

RULES = json.load(open(os.path.join(os.path.dirname(__file__), 'data', 'rules.json'), encoding='utf-8'))['rules']

AREAS = ('HOS', 'SHR', 'CIT', 'SCH')


class LoopEnd(Exception):
    """ループが直ちに終わる（キーパーソンの死亡・主人公の死亡など）。loss=True なら主人公の敗北。"""
    def __init__(self, reason, loss=True, events=None):
        super().__init__(reason)
        self.reason, self.loss, self.events = reason, loss, events or []


def base_role(state, cid):
    roles = state['script']['roles']
    if cid == 'C32':  # アルバイト【特性】このカードは配役を無視してパーソンとなる
        return 'PERSON'
    if cid == 'C33':  # アルバイト？【特性】配役はアルバイトと一致（脚本上のアルバイトの配役と読む。裁定待ち）
        return roles.get('C32', 'PERSON')
    return roles.get(cid, 'PERSON')


def mm_areas(state, cid):
    """脚本家が能力を使うときの、そのキャラクターの位置。大物【特性】テリトリーにいるものとして能力を使用してもよい。"""
    a = [state['chars'][cid]['area']]
    if cid == 'C16' and state['script'].get('territory'):
        a.append(state['script']['territory'])
    return a


def role(state, cid):
    """いまの役職。ルールの役職の変化（惨劇セットのデータ role_change。BTX: 妄想拡大ウイルス【強制：常時】パーソンは不安3以上の間シリアルキラー）
    を配役に重ねる。変更は保存せず毎回導く（plan/engine-api.md 4.3）。"""
    r = base_role(state, cid)
    for rid in state['script']['rules']:
        ch = sets.rule(state, rid).get('role_change')
        if ch and r == ch['from'] and sets.meets(state['chars'][cid], ch.get('self_min'), ch.get('self_max')):
            return ch['to']
    return r


def has_ability(state, cid, ab):
    """役職の能力を持つか。ボードの暗躍で他の役職の能力を得る役職（データ gains。BTX: ファクターは【強制：常時】学校に暗躍2以上で
    ミスリーダーの能力、都市に暗躍2以上でキーパーソンの能力を得る。役職自体は変化しない。早見表 ファクター）。"""
    r = role(state, cid)
    if r == ab:
        return True
    return any(g['ability'] == ab and state['boards'].get(g['board'], 0) >= g['min'] for g in sets.role(state, r).get('gains', []))


def alive_in(state, area):
    # 盤面にいないキャラクター（登場前の神格・転校生、present=False）はどのエリアにもいない
    return [c for c, v in state['chars'].items() if v['alive'] and v.get('present', True) and v['area'] == area]


def kill(state, cid, cause, events):
    kill_many(state, [cid], cause, events)


def kill_many(state, cids, cause, events):
    """死亡させる（同時に死ぬ者はまとめて渡す）。
    - タイムトラベラー【強制：常時】死亡しない（対象には選べ、何も起きない＝FAQ Ch18）。護衛も減らない（字面どおり。裁定待ち）
    - 護衛があれば代わりに護衛を1つ取り除く（刑事[友好5] の文面）
    - ラバーズ・メインラバーズ【強制：相手の死亡時】不安6。同時に死亡した場合は、全員を死体にしてから相手の生死を見る（FAQ Ch15）
    キーパーソンの死亡の判定は呼び出し側（check_key_death）。"""
    died = []
    sv = state['chars'].get('C34')
    if sv and sv['alive'] and sv.get('present', True):
        # 従者【特性】同一エリアのお嬢様か大物（従者[友好4] で足した者を含む）が死亡する場合、代わりに死亡する
        guarded = {'C03', 'C16', *state.get('servant_targets', [])}
        sub = [c for c in cids if c in guarded and c != 'C34' and state['chars'][c]['alive']
               and state['chars'][c]['area'] == sv['area']]
        if sub:
            if isinstance(cause, dict):
                cause = dict(cause, C34=cause.get(sub[0]))
            # 従者は、自身の死（同時に死ぬ場合）と身代わりの死をそれぞれ受ける（作者回答＝裁定。護衛1つなら2回目で死亡）
            cids = [c for c in cids if c not in sub] + ['C34'] * len(sub)
            events.append({'kind': 'substitute', 'char': 'C34', 'for': sub})
    for cid in cids:
        cause_c = cause.get(cid) if isinstance(cause, dict) else cause
        ch = state['chars'][cid]
        if not ch['alive']:
            continue
        if sets.has_trait(state, base_role(state, cid), 'immortal'):  # タイムトラベラー【強制：常時】このキャラクターは死亡しない
            events.append({'kind': 'no_death', 'char': cid, 'cause': cause_c})
            continue
        if ch.get('guard', 0) > 0:
            ch['guard'] -= 1
            events.append({'kind': 'guarded', 'char': cid, 'cause': cause_c})
            continue
        ch['alive'] = False
        died.append(cid)
        events.append({'kind': 'death', 'char': cid, 'cause': cause_c})
    for cid in died:  # 相手の死亡時に不安（データ partner。BTX: ラバーズ・メインラバーズ【強制】相手の死亡時に不安6）
        pt = sets.role(state, base_role(state, cid)).get('partner')
        for c, v in state['chars'].items():
            if pt and base_role(state, c) == pt['role'] and v['alive']:
                v['par'] += pt['par']
                events.append({'kind': 'par', 'target': c, 'by': pt['role'], 'n': pt['par']})


def protagonists_die(state, events, why):
    """主人公を死亡させる効果。軍人[友好5]「このループ中、主人公は死亡しない」が効いていれば何も起こらない
    （印刷p41「脚本家はそのような効果が発生した事実を伝える必要はありません」）。"""
    if state.get('protagonists_immortal'):
        return
    events.append({'kind': 'loop_end', 'reason': f'主人公死亡（{why}）', 'loss': True})
    raise LoopEnd('主人公死亡', events=events)


def servant_follow(state, before, events, follow=None):
    """従者【特性】同一エリアのお嬢様か大物（と従者[友好4] の対象）が移動する場合、自身への移動を無視して一緒に移動する
    （重複する場合はリーダーが選べる＝follow {'C34': 付いていく相手}）。before: 移動前のエリア {キャラ: エリア}。
    付いていったら True（呼び出し側は従者自身への移動を無視する）。"""
    sv = state['chars'].get('C34')
    if not sv or not sv['alive'] or not sv.get('present', True) or 'C34' not in before:
        return False
    guarded = {'C03', 'C16', *state.get('servant_targets', [])}
    movers = [c for c in guarded if c in before and c in state['chars'] and before[c] == before['C34']
              and state['chars'][c]['area'] != before[c]]
    if not movers:
        return False
    dests = {state['chars'][c]['area'] for c in movers}
    if len(dests) > 1:
        pick = (follow or {}).get('C34')
        if pick not in movers:
            raise IllegalPlacement('従者がどちらに付いていくかの選択が無い（リーダーが選ぶ）')
        to = state['chars'][pick]['area']
    else:
        to = dests.pop()
    sv['area'] = to
    events.append({'kind': 'move', 'char': 'C34', 'to': to, 'by': 'SERVANT'})
    return True


def check_key_death(state, events):
    # キーパーソン【強制: このキャラクターの死亡時】（早見表 役職表）: いま死亡した者（events の death）だけを見る。
    # 先に死んでいたファクターが、後で都市の暗躍2以上でキーパーソンの能力を得ても、死亡時は過ぎているので終わらない（d16 で誤ってループが終わっていた）
    died = {e['char'] for e in events if isinstance(e, dict) and e.get('kind') == 'death'}
    for cid in died:
        v = state['chars'][cid]
        if not v['alive'] and any(has_ability(state, cid, k) for k in sets.roles_with(state, 'loss_on_death')):  # データ traits（BTX: キーパーソン）
            events.append({'kind': 'loop_end', 'reason': 'キーパーソン死亡', 'loss': True})
            raise LoopEnd('キーパーソン死亡', events=events)


# ---- ターン開始フェイズ ----
def turn_start(state):
    """転校生【特性】指定した日のターン開始フェイズに、初期エリア（学校）に配置する（生存・カウンター無し）。
    前日の能力の使用（1フェイズ1回の記録）もここで消す。以前は脚本家能力フェイズの後にだけ消していて、朝の盤面に前日の使用が残り、
    下限の表（route_calc._pc_abilities）と先読みが前日に使ったナース[2] を「使えない」と数えた（d30 L1D6、Guignol が確定と誤って隠した 2026-10-29）。"""
    state['ability_used_today'] = []
    events = []
    d = state['script'].get('appear', {}).get('C24', {}).get('day')
    ch = state['chars'].get('C24')
    if ch is not None and d == state['day'] and not ch.get('present', True):
        ch.update(present=True, area=CHARS['C24']['start'], alive=True, par=0, gw=0, int=0, guard=0)
        events.append({'kind': 'appear', 'char': 'C24', 'area': ch['area']})
    pt, q = state['chars'].get('C32'), state['chars'].get('C33')
    if pt is not None and q is not None and not pt['alive'] and not q.get('present', True):
        # アルバイト【特性】ターン開始フェイズにこのカードが死亡している場合、都市にアルバイト？を配置する
        q.update(present=True, area='CIT', alive=True, par=0, gw=0, int=0, guard=0)
        events.append({'kind': 'appear', 'char': 'C33', 'area': 'CIT'})
    return events


# ---- 脚本家能力フェイズ（印刷p23 4-5） ----
def mm_ability(state, actor, target, ability=None):
    """クロマク: 同一エリアのキャラ1人、または自身のいるボードに暗躍1。ミスリーダー: 同一エリアのキャラ1人に不安1。
    同一エリアは自身を含む（公式FAQ Ch01）。1ターンに1つだけ（FAQ Ch11。呼び出し側が1回だけ呼ぶ）。"""
    a = state['chars'][actor]
    if not a['alive']:
        raise IllegalPlacement(f'{actor} は死亡しており能力を使えない')
    r = ability if ability else role(state, actor)
    if not has_ability(state, actor, r):
        raise IllegalPlacement(f'{actor} は {r} の能力を持たない')
    events = []
    spec = sets.role(state, r).get('mm_ability')  # データ mm_ability（BTX: クロマク＝暗躍1・ボード可、ミスリーダー＝不安1）
    if not spec:
        raise IllegalPlacement(f'{actor}（{r}）は脚本家能力を持たない')
    k = spec['counter']
    if target.startswith('B:'):
        if not spec.get('board') or target[2:] not in mm_areas(state, actor):
            raise IllegalPlacement(f'{r} は自身のいるボードにしか置けない' if spec.get('board') else f'{r} はボードに置けない')
        state['boards'][target[2:]] += 1
    else:
        t = state['chars'][target]
        if not t['alive'] or t['area'] not in mm_areas(state, actor):
            raise IllegalPlacement(f'{r} の射程外（同一エリアの生存キャラ）')
        t[k] += 1
    events.append({'kind': 'mm_ability', 'role': r, 'actor': actor, 'target': target, k: 1})
    return events


# ---- 事件フェイズ（印刷p24 4-7） ----
def incident_today(state):
    return [i for i in state['script']['incidents'] if i['day'] == state['day']]


def incident_occurs(state, inc):
    """犯人が生存し、犯人に不安臨界以上の不安があれば必ず発生する。
    手先[友好3] で止められた犯人は発生させない（不可能優先の原則＝公式FAQ）。
    A.I.【特性】自身が犯人の事件の判定では、全てのカウンターを不安としても扱う（護衛を含むかは裁定待ち。字面で含める）。
    アルバイト？【特性】事件の犯人かどうかはアルバイトと一致する（アルバイトが犯人ならアルバイト？も犯人）。"""
    return acting_culprit(state, inc) is not None


def acting_culprit(state, inc):
    """事件を発生させる犯人（発生しないなら None）。"""
    cands = [inc['culprit']] + (['C33'] if inc['culprit'] == 'C32' and 'C33' in state['chars'] else [])
    for cul in cands:
        c = state['chars'][cul]
        if cul in state.get('suppressed_culprits', []) or not c.get('present', True) or not c['alive']:
            continue
        n = c['par'] + (c['gw'] + c['int'] + c.get('guard', 0) if cul == 'C22' else 0)
        if n >= CHARS[cul]['limit']:
            return cul
    return None


def run_incident(state, inc, choice=None):
    """choice は効果の対象の選択（脚本家の判断）: MURDER {'target'}、SPREAD {'par_target','int_target'}、HOSPITAL なし、
    REMOTE {'target'}（候補が無ければ None）、MISSING {'board'}、RUMOR_SPREAD {'from','to'}、BUTTERFLY {'target','counter'}。
    誤った選択（IllegalPlacement）では状態を変えない。"""
    snap = copy_state(state)
    try:
        return _run_incident(state, inc, choice)
    except IllegalPlacement:
        state.clear()
        state.update(snap)
        raise


def _run_incident(state, inc, choice=None):
    # critical: 判定の時点で不安臨界以上だった生存者（公開情報。犯人の推理 deduce.incident_candidates に使う）
    critical = [c for c, v in state['chars'].items() if v['alive'] and v.get('present', True)
                and v['par'] + (v['gw'] + v['int'] + v.get('guard', 0) if c == 'C22' else 0) >= CHARS[c]['limit']]  # A.I. は全カウンター
    events = [{'kind': 'incident', 'id': inc['id'], 'day': state['day'], 'occurred': incident_occurs(state, inc), 'critical': critical}]
    # 発生した事件をこのループの記録に残す（刑事[友好4] の対象。誰も死ななかった殺人事件も発生に数える＝FAQ Af01）
    state.setdefault('incident_log', []).append({'loop': state['loop'], 'id': inc['id'], 'occurred': events[0]['occurred']})
    state.setdefault('incident_day_log', []).append(inc['day'])  # incident_log と同じ順の日付（同じ名前の事件を区別する。刑事[4]）
    if not events[0]['occurred']:
        return events
    cul = acting_culprit(state, inc)
    if cul == 'C26':  # 黒猫【特性】このキャラクターが犯人の事件の事件効果は「何も起きない」に変更される
        return events
    if cul == 'C29':  # 教祖【特性】犯人の事件が発生するならば、事件効果を代わりに2回解決する（選択は2要素の列）
        chs = list(choice) if isinstance(choice, (list, tuple)) else [choice, None]
        incident_effect(state, inc['id'], cul, chs[0], events)
        check_key_death(state, events)  # 1回目の効果でループが終われば2回目は解決しない（作者回答＝裁定）
        incident_effect(state, inc['id'], cul, chs[1] if len(chs) > 1 else None, events)
    else:
        incident_effect(state, inc['id'], cul, choice, events)
    check_key_death(state, events)
    return events


def incident_effect(state, iid, cul, choice, events):
    """事件効果だけを解決する（発生の判定・記録・キーパーソンの死亡の判定は呼び出し側）。A.I.[友好3] もこれを使う。"""
    area = state['chars'][cul]['area']
    inc = {'id': iid, 'culprit': cul}
    if choice and 'par' in choice and 'par_target' not in choice:  # 不安拡大の選択の別名 {'par','int'}
        choice = {'par_target': choice['par'], 'int_target': choice['int']}
    if inc['id'] == 'MURDER':
        cand = [c for c in alive_in(state, area) if c != cul]
        t = (choice or {}).get('target')
        if cand or t is not None:
            if t not in cand:
                raise IllegalPlacement(f'殺人事件の対象は犯人と同一エリアの犯人以外（候補 {cand}）')
            kill(state, t, 'MURDER', events)
    elif inc['id'] == 'SPREAD':
        p, i = choice['par_target'], choice['int_target']
        if not (state['chars'][p]['alive'] and state['chars'][i]['alive']):
            raise IllegalPlacement('死体は対象にできない（FAQ Ru05）')
        if p == i:
            raise IllegalPlacement('不安拡大は「別の」キャラクターに暗躍を置く')
        state['chars'][p]['par'] += 2
        state['chars'][i]['int'] += 1
        events.append({'kind': 'par', 'target': p, 'by': 'SPREAD', 'n': 2})
        events.append({'kind': 'int', 'target': i, 'by': 'SPREAD', 'n': 1})
    elif inc['id'] == 'CORRUPT':
        state['boards']['SHR'] += 2
        events.append({'kind': 'int', 'target': 'B:SHR', 'by': 'CORRUPT', 'n': 2})
    elif inc['id'] == 'SUICIDE':
        kill(state, cul, 'SUICIDE', events)
    elif inc['id'] == 'HOSPITAL':
        # 病院に暗躍1以上→病院にいる全員死亡、2以上→主人公死亡（効果を最後まで解決してから判定＝FAQ Af02 の読み。裁定待ち）
        n = state['boards']['HOS']
        if n >= 1:
            kill_many(state, alive_in(state, 'HOS'), 'HOSPITAL', events)
            check_key_death(state, events)  # キーパーソンが死んだ時点でループが終わり、続きは解決しない（作者の Af02 訂正＝裁定）
        if n >= 2:
            protagonists_die(state, events, '病院の事件')
    elif inc['id'] == 'REMOTE':
        cand = [c for c, v in state['chars'].items() if v['alive'] and v['int'] >= 2]
        if cand:
            t = (choice or {}).get('target')
            if t not in cand:
                raise IllegalPlacement(f'遠隔殺人の対象は暗躍2以上の生存キャラクター（候補 {cand}）')
            kill(state, t, 'REMOTE', events)
    elif inc['id'] == 'MISSING':
        to = (choice or {}).get('board')
        if to not in AREAS:
            raise IllegalPlacement('行方不明は移動先のボードを選ぶ（留まるなら今のエリア）')
        if to in CHARS[cul].get('forbidden', []) and cul not in state.get('unbound', []):
            raise IllegalPlacement('行方不明でも禁止エリアへは動かせない（非公式wiki FAQ・作者回答）')
        before = {c: v['area'] for c, v in state['chars'].items() if v['alive'] and v.get('present', True)}
        stay = state['chars'][cul]['area'] == to
        state['chars'][cul]['area'] = to
        state['boards'][to] += 1
        if not stay:  # 元いたボードに留まる場合は暗躍カウンターを置くだけで、移動は告げない（wiki/concepts/incident-effects.md）
            events.append({'kind': 'move', 'char': cul, 'to': to, 'by': 'MISSING'})
        servant_follow(state, before, events)
        events.append({'kind': 'int', 'target': 'B:' + to, 'by': 'MISSING', 'n': 1})
    elif inc['id'] == 'RUMOR_SPREAD':
        f, to = (choice or {}).get('from'), (choice or {}).get('to')
        if f == to or f not in state['chars'] or to not in state['chars']:
            raise IllegalPlacement('流布は別々のキャラクター2人を選ぶ')
        if not (state['chars'][f]['alive'] and state['chars'][to]['alive']):
            raise IllegalPlacement('死体は対象にできない（FAQ Ru05）')
        state['chars'][f]['gw'] = max(0, state['chars'][f]['gw'] - 2)
        state['chars'][to]['gw'] += 2
        events.append({'kind': 'gw', 'target': f, 'by': 'RUMOR_SPREAD', 'n': -2})
        events.append({'kind': 'gw', 'target': to, 'by': 'RUMOR_SPREAD', 'n': 2})
    elif inc['id'] == 'BUTTERFLY':
        t, k = (choice or {}).get('target'), (choice or {}).get('counter')
        if t not in alive_in(state, area):
            raise IllegalPlacement('蝶の羽ばたきの対象は犯人と同一エリアの生存キャラクター（犯人自身を含む）')
        if k not in ('gw', 'par', 'int'):
            raise IllegalPlacement("置けるのは友好・不安・暗躍のいずれか（'gw'|'par'|'int'）")
        state['chars'][t][k] += 1
        events.append({'kind': k, 'target': t, 'by': 'BUTTERFLY', 'n': 1})
    else:
        raise NotImplementedError(inc['id'])


# ---- ターン終了フェイズ（印刷p24 4-9） ----
def turn_end(state, optional=None):
    """【強制】をすべて同時に解決してから【任意】を脚本家の選んだ順に解決する（haunted_p12 能力の区分けとその解決順）。
    シリアルキラー: 同一エリアの生存キャラが1人だけ→その1人を死亡（死体は数えない。SK2人だけなら相打ち＝FAQ Ch02）。
    optional: 脚本家が使うと選んだ任意能力 [{'char': キラー, 'ability': 'KILLER_KEY'|'KILLER_PROT'}]。
    条件を満たさない・死亡している（FAQ Ru06）ものを選ぶと IllegalPlacement。"""
    events = []
    victims = []
    for cid, v in state['chars'].items():
        forced = sets.role(state, role(state, cid)).get('turn_end_forced', []) if v['alive'] else []
        if any(f['do'] == 'kill_alone' for f in forced):  # データ（BTX: シリアルキラー【強制】同一エリアの生存者が1人だけ→その1人を死亡）
            others = [c for c in alive_in(state, v['area']) if c != cid]
            if len(others) == 1:
                victims.append((others[0], cid))
    # 同時解決: 判定をすべて済ませてから、まとめて死亡させる（ラバーズの不安6も同時死亡を考える）
    pt = state['chars'].get('C32')
    if pt and pt['alive'] and pt.get('present', True) and pt['par'] + pt['gw'] + pt['int'] + pt.get('guard', 0) >= 3:
        victims.append(('C32', 'SELF'))  # アルバイト【特性】ターン終了フェイズにカウンター合計3以上なら死亡（護衛を数えるかは裁定待ち）
    kill_many(state, [v for v, _ in victims], {v: (f'SK:{sk}' if sk != 'SELF' else 'PART_TIMER') for v, sk in victims}, events)
    check_key_death(state, events)
    return events + turn_end_optional(state, optional)


def turn_end_mandatory(state):
    """ターン終了フェイズの【強制】だけを解決する（任意能力の候補は、この後の盤面で決める。ユーザー裁定 2026-09-27:
    処理順は脚本家の自由なので、シリアルキラーの殺害→ラバーズの死でメインラバーズに不安6→同じフェイズにメインラバーズで主人公死亡、ができる）。"""
    return turn_end(state, [])


def turn_end_optional(state, optional=None):
    """ターン終了フェイズの【任意】能力を脚本家の選んだ順に解決する。"""
    events = []
    for o in optional or []:
        cid, ab = o['char'], o['ability']
        v = state['chars'][cid]
        if not v['alive']:
            raise IllegalPlacement('死亡しており能力を使えない（FAQ Ru06）')
        spec = next((x for x in _optionals(state, cid) if x['id'] == ab), None)
        if spec is None:
            raise IllegalPlacement(f'{cid} は {ab} の能力を持たない')
        if not _opt_ok(state, cid, spec):
            raise IllegalPlacement(f'{ab} の条件を満たしていない')
        if spec['do'] == 'loss_now':  # 主人公は敗北し、直ちにループを終了（BTX: タイムトラベラー）
            events.append({'kind': 'loop_end', 'reason': spec['reason'], 'loss': True})
            raise LoopEnd(ab, events=events)
        if spec['do'] == 'protagonists_die':  # BTX: キラー（暗躍4）・メインラバーズ（暗躍1・不安3）
            protagonists_die(state, events, spec['reason'])
            continue
        if spec['do'] == 'kill_key_same_area':  # BTX: キラー（同一エリアの暗躍2以上のキーパーソン）
            kill(state, _keys_in_reach(state, cid, spec)[0], f'KILLER:{cid}', events)
            check_key_death(state, events)
            continue
        raise IllegalPlacement(f'未対応の部品 {spec["do"]}')
    return events


def _opt_ok(state, cid, spec):
    """任意能力（データ turn_end_optional の1件）の条件を満たすか。"""
    v = state['chars'][cid]
    if not sets.meets(v, spec.get('self_min'), spec.get('self_max')):
        return False
    if spec.get('last_day') and state['day'] != state['script'].get('days'):
        return False
    if spec['do'] == 'kill_key_same_area':
        return bool(_keys_in_reach(state, cid, spec))
    return True


def _keys_in_reach(state, cid, spec):
    """同一エリアの、条件を満たすキーパーソン（役職そのもの。テリトリーはターン終了では参照しない＝裁定）。"""
    return [c for c in alive_in(state, state['chars'][cid]['area']) if base_role(state, c) == 'KEY' and sets.meets(state['chars'][c], spec.get('key_min'))]


def _optionals(state, cid):
    v = state['chars'][cid]
    if not v['alive']:
        return []
    return sets.role(state, role(state, cid)).get('turn_end_optional', [])


def killer_options(state):
    """いま使える【任意】のターン終了の能力の一覧（データ turn_end_optional。BTX: キラー・メインラバーズ・タイムトラベラー）。"""
    return [{'char': cid, 'ability': o['id']} for cid in state['chars'] for o in _optionals(state, cid) if _opt_ok(state, cid, o)]


# ---- ループの準備（印刷p20 §3）とループ開始時 ----
def loop_start(prev_end_state, init_areas, choice=None):
    """キャラを初期エリアに生存で配置、カウンター全除去、手札を配り直し。
    因果の糸【強制：ループ開始時】ひとつ前のループ終了時に友好が置かれていた全キャラに不安2（早見表 ルールX）。
    choice（脚本家がループの準備で決めること）: {'init': {'C18': エリア}（手先の初期エリア）, 'scholar': 'gw'|'par'|'int'（学者）}。
    登場の指定 script['appear']: 神格 {'C13': {'loop': N}} は第Nループまで配置しない（字面。境界は裁定待ち）、
    転校生 {'C24': {'day': D}} はループの準備では配置せず、D日目のターン開始に配置する（turn_start）。"""
    choice = choice or {}
    init_areas = dict(init_areas, **choice.get('init', {}))
    if 'C18' in init_areas and init_areas['C18'] not in AREAS:
        raise IllegalPlacement('手先の初期エリアは各ループの準備で脚本家が決める（カードの特性）')
    s = {k: prev_end_state[k] for k in ('script', 'leader')}
    s['loop'] = prev_end_state['loop'] + 1
    s['day'] = 1
    s['chars'] = {c: {'area': a, 'alive': True, 'par': 0, 'gw': 0, 'int': 0, 'guard': 0} for c, a in init_areas.items()}
    appear = s['script'].get('appear', {})
    if 'C13' in s['chars'] and s['loop'] < appear.get('C13', {}).get('loop', 0):  # 第Nループから登場（裁定）
        s['chars']['C13'].update(present=False, area=None)
    if 'C24' in s['chars'] and 'day' in appear.get('C24', {}):
        s['chars']['C24'].update(present=False, area=None)
    if 'C33' in s['chars']:  # アルバイト？ はアルバイトが死体のときにターン開始で配置される（ループの準備では置かない。裁定待ち）
        s['chars']['C33'].update(present=False, area=None)
    if 'C34' in s['chars'] and s['script'].get('start', {}).get('C34'):
        s['chars']['C34']['area'] = s['script']['start']['C34']  # 従者の初期エリアは脚本で指定（都市か学校）
    s['boards'] = {a: 0 for a in AREAS}
    s['used'] = {p: [] for p in ('M', 'A', 'B', 'C')}
    s['incident_log'], s['ability_used_loop'], s['ability_used_today'] = [], [], []
    s['revealed_roles'] = dict(prev_end_state.get('revealed_roles', {}))
    s['init'] = dict(init_areas)
    events = []
    if 'C19' in s['chars']:  # 学者【特性】各ループ開始時に、友好・不安・暗躍のいずれか1つ（脚本家が選ぶ）
        k = choice.get('scholar')
        if k not in ('gw', 'par', 'int'):
            raise IllegalPlacement('学者に置くカウンター（gw|par|int）を脚本家が選ぶ（カードの特性）')
        s['chars']['C19'][k] += 1
        events.append({'kind': k, 'target': 'C19', 'by': 'SCHOLAR', 'n': 1})
    if 'C26' in s['chars']:  # 黒猫【特性】各ループ開始時に、神社に暗躍1（カウンターの除去の後）
        s['boards']['SHR'] += 1
        events.append({'kind': 'int', 'target': 'B:SHR', 'by': 'BLACK_CAT', 'n': 1})
    # フレンド【強制：ループ開始時】このキャラクターの役職が公開されたことがある→友好1（早見表 役職。能力で見られた場合も＝FAQ Ch21）
    for c in s['revealed_roles']:  # データ revealed_loop_start_gw（BTX: フレンド）
        n = sets.role(s, base_role(s, c)).get('revealed_loop_start_gw') if c in s['chars'] else None
        if n:
            s['chars'][c]['gw'] += n
            events.append({'kind': 'gw', 'target': c, 'by': base_role(s, c), 'n': n})
    for rid in s['script']['rules']:  # データ loop_start（BTX: 因果の糸【強制：ループ開始時】前のループ終了時に友好のあった全員に不安2）
        ls = sets.rule(s, rid).get('loop_start')
        if ls and ls['do'] == 'par_to_prev_gw':
            for c, v in prev_end_state['chars'].items():
                if v['gw'] > 0:
                    s['chars'][c]['par'] += ls['n']
                    events.append({'kind': 'par', 'target': c, 'by': rid, 'n': ls['n']})
    return s, events


def loop_end_loss(state):
    """ループ終了時の敗北判定。成立した敗北条件の列を返す（空＝敗北なし）。
    早見表の追加ルール（wiki/concepts/rule-role-matrix.md「追加ルールの文面」）と役職フレンド（role-abilities.md）:
    - 封印されしモノ: 神社に暗躍2以上 / 僕と契約しようよ！: キーパーソン（役職。ファクターは数えない＝ユーザー裁定）に暗躍2以上
    - 未来改変プラン: このループ中に蝶の羽ばたきが発生 / 巨大時限爆弾X: ウィッチの初期エリアのボードに暗躍2以上（生死を問わない＝ユーザー裁定）
    - フレンド: 死亡している。その場合、役職を公開する（state['revealed_roles'] に書き足す）
    規定日数前にループが終わった場合も呼ぶ（FAQ Ch07・Ch20）。どの条件が成立したかは主人公に告げない（FAQ Ru02）。"""
    b = state['boards']
    out = []
    for rid in state['script']['rules']:  # データ loop_end_loss（ルールY）
        c = sets.rule(state, rid).get('loop_end_loss')
        if not c:
            continue
        if c['do'] == 'board_int':
            hit = b[c['board']] >= c['min']
        elif c['do'] == 'role_counter':  # 役職そのもの（ファクターは数えない＝ユーザー裁定）
            hit = any(base_role(state, x) == c['role'] and v[c['counter']] >= c['min'] for x, v in state['chars'].items())
        elif c['do'] == 'incident_occurred':
            hit = any(i['loop'] == state['loop'] and i['id'] == c['incident'] and i['occurred'] for i in state.get('incident_log', []))
        elif c['do'] == 'start_area_int':  # 生死を問わない＝ユーザー裁定
            init = state.get('init') or {}
            hit = any(base_role(state, x) == c['role'] and b.get(init.get(x), 0) >= c['min'] for x in state['chars'])
        else:
            raise IllegalPlacement(f'未対応の部品 {c["do"]}')
        if hit:
            out.append(rid)
    for r_, how in sets.roles_with(state, 'loop_end_loss').items():  # 役職の敗北条件（BTX: フレンドが死亡している。役職を公開）
        dead = [c for c, v in state['chars'].items() if base_role(state, c) == r_ and not v['alive']] if how == 'dead' else []
        if dead:
            out.append(r_)
            rev = state.setdefault('revealed_roles', {})
            for c in dead:
                rev[c] = r_
    return out


def mm_phase(state, uses):
    """脚本家能力フェイズ全体。uses = [{'char','ability','target', 'mode'?}]。
    能力ごとに1回、対象は1つ（印刷p23 4-5「複数の能力が使える場合、可能な全ての能力を1回ずつ」、FAQ Ch11）。
    ability: 'KUROMAKU' | 'MISLEADER' は役職の能力、'DOCTOR' は医者[友好2] を脚本家が使う場合
    （カード「このキャラクターが友好無視を持ち、さらに友好カウンターが2つ以上置かれている場合、脚本家はこの能力を
    脚本家能力フェイズに使用できる」、FAQ Ch04・Ch16）。"""
    seen, events = set(), []
    snap = copy_state(state)
    try:
        return _mm_phase(state, uses, seen, events)
    except IllegalPlacement:
        state.clear()
        state.update(snap)  # 誤った宣言では状態を変えない
        raise


def tree_forced(state):
    """ご神木が友好無視を持ち、移せるカウンターと相手がいる（脚本家能力フェイズに特性を使わなければならない）。"""
    tree = state['chars'].get('C30')
    if not tree or not tree['alive'] or not tree.get('present', True) or not sets.refusal(state, role(state, 'C30')):
        return False
    return any(tree.get(k, 0) > 0 for k in ('par', 'gw', 'int')) and any(c != 'C30' for c in alive_in(state, tree['area']))


def _mm_phase(state, uses, seen, events):
    # ご神木【特性】友好無視を持つ場合、脚本家能力フェイズに脚本家もこの特性を用いる（強制）
    tree = state['chars'].get('C30')
    if tree and tree['alive'] and tree.get('present', True) and sets.refusal(state, role(state, 'C30')):
        can = any(tree.get(k, 0) > 0 for k in ('par', 'gw', 'int')) and any(c != 'C30' for c in alive_in(state, tree['area']))
        if can and not any(u['ability'] == 'SHINBOKU' for u in uses):
            raise IllegalPlacement('ご神木の強制の特性が使われていない')
    for u in uses:
        if u['ability'] == 'SHINBOKU':
            t, k = u['target'], u.get('counter')
            if not tree or not sets.refusal(state, role(state, 'C30')):
                raise IllegalPlacement('ご神木が友好無視を持たない')
            tc = state['chars'].get(t)
            if t == 'C30' or tc is None or not tc['alive'] or tc['area'] != tree['area'] or tree.get(k, 0) < 1:
                raise IllegalPlacement('ご神木の上のカウンター1つを同一エリアの他のキャラクター1人に')
            tree[k] -= 1
            tc[k] = tc.get(k, 0) + 1
            events.append({'kind': 'mm_ability', 'role': 'SHINBOKU', 'target': t, k: 1})
            continue
        key = (u.get('char'), u['ability'])
        if key in seen:
            raise IllegalPlacement('同じ能力は1ターンに1回（印刷p23 4-5、FAQ Ch11）')
        seen.add(key)
        if u['ability'] == 'X_RUMOR':
            events += rumor(state, u['target'])
            continue
        if u['ability'] == 'DOCTOR':
            events += doctor_by_mastermind(state, u['char'], u['target'], u.get('mode'))
            continue
        events += mm_ability(state, u['char'], u['target'], u['ability'])
    return events


REFUSERS = tuple(r for r, v in sets.load('BTX')['roles'].items() if v.get('refusal'))  # 友好無視・絶対友好無視（BTX。盤面ごとには sets.refusal）


def rumor(state, target):
    """不穏な噂【任意能力：脚本家能力フェイズ】任意のボード1つに暗躍1（1ループ1回制限）（早見表 追加ルール）。"""
    if not any(sets.rule(state, x).get('mm_board_once') for x in state['script']['rules']):  # データ mm_board_once（BTX: 不穏な噂）
        raise IllegalPlacement('不穏な噂は使われていない')
    if not (isinstance(target, str) and target.startswith('B:') and target[2:] in AREAS):
        raise IllegalPlacement('不穏な噂の対象はボード')
    used = state.setdefault('ability_used_loop', [])
    if 'X_RUMOR' in used:
        raise IllegalPlacement('不穏な噂は1ループ1回')
    used.append('X_RUMOR')
    state['boards'][target[2:]] += 1
    return [{'kind': 'mm_ability', 'role': 'X_RUMOR', 'target': target, 'int': 1}]


def doctor_by_mastermind(state, cid, target, mode):
    d = state['chars'].get(cid)
    if cid != 'C08' or d is None or not d['alive']:
        raise IllegalPlacement('脚本家が使えるのは生存している医者の[友好2]だけ')
    if not sets.refusal(state, role(state, cid)):
        raise IllegalPlacement('医者が友好無視を持たない')
    if d['gw'] < 2:
        raise IllegalPlacement('医者の友好が2未満（FAQ Ch04）')
    t = state['chars'].get(target)
    if target == cid or t is None or not t['alive'] or t['area'] != d['area']:
        raise IllegalPlacement('対象は同一エリアの自身以外の生存キャラクター')
    if mode not in ('place', 'remove'):
        raise IllegalPlacement("mode は 'place' か 'remove'")
    t['par'] = t['par'] + 1 if mode == 'place' else max(0, t['par'] - 1)
    return [{'kind': 'mm_ability', 'role': 'DOCTOR', 'actor': cid, 'target': target, 'mode': mode}]


def default_init(chars):
    return {c: CHARS[c]['start'] for c in chars}
