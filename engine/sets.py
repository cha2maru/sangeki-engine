"""惨劇セットのデータ（engine/data/sets/<id>.json。plan/engine-api.md 4章）。エンジンは役職・ルールの性質と能力をここから引き、部品を実行する。"""
import json
import os
from functools import lru_cache

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'sets')


@lru_cache(maxsize=None)
def load(set_id='BTX'):
    """公開シートの表（ルールの name・type・roles・text、役職の name）がセットに無ければ、Basic Tragedy X の表（data/rules.json・roles.json）から補う。"""
    d = json.load(open(os.path.join(_DIR, f'{set_id or "BTX"}.json'), encoding='utf-8'))
    rules = json.load(open(os.path.join(_DIR, '..', 'rules.json'), encoding='utf-8'))['rules']
    roles = json.load(open(os.path.join(_DIR, '..', 'roles.json'), encoding='utf-8'))['roles']
    for r, v in d['rules'].items():
        for k in ('name', 'type', 'roles', 'text'):
            if k not in v:
                v[k] = rules[r][k]
    for r, v in d['roles'].items():
        v.setdefault('name', roles[r]['name'] if r in roles else r)
    return d


def ids():
    return sorted(f[:-5] for f in os.listdir(_DIR) if f.endswith('.json'))


def of(state):
    """盤面（state）の惨劇セット。脚本に set が無ければ Basic Tragedy X。"""
    return load(state.get('script', {}).get('set') or 'BTX')


def role(state, r):
    return of(state)['roles'].get(r, {})


def rule(state, rid):
    return of(state)['rules'].get(rid, {})


def roles_with(state, key):
    """その性質・能力を持つ役職 {役職: 値}。"""
    out = {}
    for r, v in of(state)['roles'].items():
        if key in v:
            out[r] = v[key]
        elif key in v.get('traits', []):
            out[r] = True
    return out


def refusal(state, r):
    """役職の友好無視（'optional'＝友好無視・'absolute'＝絶対友好無視・None）。"""
    return role(state, r).get('refusal')


def has_trait(state, r, t):
    return t in role(state, r).get('traits', [])


def meets(v, mins=None, maxs=None):
    """カウンターの下限・上限（{'int': 2} など）を満たすか。"""
    return all(v.get(k, 0) >= n for k, n in (mins or {}).items()) and all(v.get(k, 0) <= n for k, n in (maxs or {}).items())
