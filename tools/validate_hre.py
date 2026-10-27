"""Check the generated HRE realms.csv / kingdoms.csv for problems the game only logs at load.

Usage: python tools/validate_hre.py   (after tools/import_hre.py; reads URAH_HRE/ and the vanilla data, see urah_paths.py)

Per period: every owner has a kingdom, capitals are held by their kingdom, overlords hold land and
are not vassals themselves, cultures exist, title sets exist, ruler names have texts. Union quests:
goal realms and union kingdoms exist, no kingdom starts with a quest it almost owns; lists the
kingdoms without a quest. Periods: vanilla's E / M / L plus every early period (hre_periods.ALL_PERIODS)
the import wrote rows for; there every owner needs its own kingdom row (vanilla has none for 867 / 999).
"""
import csv, re
from collections import defaultdict
import urah_paths
import hre_periods
H=str(urah_paths.REPO/'URAH_HRE')+'/'; M=H+'Maps/europe/'; V=str(urah_paths.VANILLA)+'/'
def rows(p): return [r for r in csv.reader(open(p,encoding='utf-8-sig')) if r and r[0] and not r[0].startswith('//')]
R=rows(M+'realms.csv'); h=R[0]; R=R[1:]
K=rows(M+'kingdoms.csv'); kh=K[0]; K=K[1:]
kc={x:i for i,x in enumerate(kh)}; rc={x:i for i,x in enumerate(h)}
base={}; per=defaultdict(dict)
for r in R:
    k,_,p=r[0].partition('.')
    if p: per[k][p]=r
    else: base[k]=r
kb={}; kp=defaultdict(dict)
for r in K:
    k,_,p=r[0].partition('.')
    if not k: continue
    if p: kp[k][p]=r
    else: kb[k]=r
def kget(k,p,col):
    v=kp[k].get(p,[None]*99)[kc[col]] if p in kp[k] else None
    if v in (None,'','-'): v=kb.get(k,[None]*99)[kc[col]] if k in kb else None
    return v if v not in (None,'','-') else ''
PERIODS=[p for p in hre_periods.ALL_PERIODS if p in hre_periods.VANILLA_PERIODS or any(p in d for d in per.values())]
cultures={r[0] for r in rows(V+'Maps/europe/cultures.csv')[1:]}
nob=set(re.findall(r'^def (\w+)Nobility', open(V+'Defs/nobility.def').read()+open(H+'Defs/hre_nobility.def').read(), re.M))|{''}
chars=set()
for f in [V+'Texts/en/en_character_names.csv',H+'Texts/en/en_hre_character_names.csv']:
    for r in csv.reader(open(f,encoding='utf-8-sig'),delimiter=';'):
        if r and r[0].startswith('cn_'): chars.add(r[0][3:].rsplit('.',1)[0])
errs=[]
realm_keys=set(base)
owners={}
for p in PERIODS:
    own=defaultdict(list)
    for k in base:
        o=per[k].get(p,base[k])[rc['Owner']]
        if o in ('','-'): o=base[k][rc['Owner']]
        own[o].append(k)
        for col,valid in (('Culture',cultures),):
            c=per[k].get(p,base[k])[rc[col]]
            c=c if c not in ('','-') else base[k][rc[col]]
            if c not in valid: errs.append(f'{p} realm {k} culture {c}')
    for o,rs in own.items():
        if o not in kb and o not in kp and o not in realm_keys: errs.append(f'{p}: owner {o} has no kingdom row')
        if o not in kb and o not in kp: continue
        if p not in hre_periods.VANILLA_PERIODS and p not in kp[o]: errs.append(f'{p}: owner {o} has no {o}.{p} kingdom row')
        cap=kget(o,p,'CapitalProvince')
        if cap not in rs: errs.append(f'{p}: {o} capital {cap} not owned')
        v=kget(o,p,'VassalOf')
        if v:
            if v not in own: errs.append(f'{p}: {o} vassal of landless {v}')
            elif kget(v,p,'VassalOf'): errs.append(f'{p}: nested {o} -> {v} -> {kget(v,p,"VassalOf")}')
        c=kget(o,p,'Culture')
        if c not in cultures: errs.append(f'{p}: {o} culture {c!r}')
        n=kget(o,p,'NobilityTitles')
        if n.replace('Nobility','') not in nob: errs.append(f'{p}: {o} titles {n}')
        ka=kget(o,p,'KingNameAge')
        if ka and ka.split('.')[0] not in chars: errs.append(f'{p}: {o} ruler {ka} has no name text')
    owners[p]=own

# union quests as the game hands them out at campaign start (QuestConditionDef: every activate condition
# must hold): vanilla's union_quest.def merged with the generated one. Every holder must still need at
# least MIN_TO_CONQUER goal realms, and every goal realm and union kingdom must exist
MIN_TO_CONQUER=4
def blocks(text, pat):
    for m in re.finditer(pat, text):
        d,i=1,m.end()
        while d: d+={'{':1,'}':-1}.get(text[i],0); i+=1
        yield m.group(1), text[m.end():i-1]
def lists(body):
    out={}
    for sec,sb in blocks(body, r'(\w+)\s*\{'):
        sb=re.sub(r'//[^\n]*','',sb)
        for name,entries in re.findall(r'(\w+)\s*\{([^{}]*)\}', sb): out[name]=entries.split(';')
        for name in re.findall(r'(?m)^\s*delete (\w+)\s*$', sb): out[name]=None  # removed: no condition
        c=re.search(r'culture\s*=\s*\[([^\]]*)\]', sb)
        if c: out['culture']=re.findall(r'"(\w+)"', c.group(1))
        a=re.search(r'active_period\s*=\s*\[([^\]]*)\]', sb)
        if a: out['active_period']=re.findall(r'"(\w+)"', a.group(1))
    u=re.search(r'unlock_union\s*=\s*"(\w+)"', body)
    out['unlock_union']=u.group(1) if u else ''
    return {k:[e.strip() for e in v if e.strip()] if isinstance(v,list) else v for k,v in out.items()}
def merge(d,new):
    for n,v in new.items():
        if v is None: d.pop(n,None)
        elif n!='unlock_union' or v: d[n]=v
vq=open(V+'Maps/europe/union_quest.def',encoding='utf-8-sig').read()
gq=open(M+'union_quest.def',encoding='utf-8').read()
quests={k:{n:v for n,v in lists(b).items() if v is not None} for k,b in blocks(vq, r'def (\w+) : UnionQuest\s*\{')}
for k in re.findall(r'(?m)^delete (\w+)$', gq): quests.pop(k,None)
for k,b in blocks(gq, r'(?m)^(\w+)\n\{'):
    if k in quests: merge(quests[k],lists(b))
for k,b in blocks(gq, r'def (\w+) : UnionQuest\s*\{'): quests[k]=lists(b)
PER={p:hre_periods.ALL_PERIODS[p][0] for p in PERIODS}
for q,d in quests.items():
    if d['unlock_union'] not in kb and d['unlock_union'] not in kp: errs.append(f'quest {q}: unlocks {d["unlock_union"]!r}, which has no kingdom row')
    for r in d.get('own_realms',[]):
        if r not in realm_keys: errs.append(f'quest {q}: goal realm {r} not on the map')
questless={}
for p in PERIODS:
    none=[]
    for o,rs in owners[p].items():
        if o not in kb and o not in kp: continue
        got=0
        for q,d in quests.items():
            if 'active_period' in d and PER[p] not in d['active_period']: continue
            if d.get('culture') and kget(o,p,'Culture') not in d['culture']: continue
            if o in d.get('excluded_kingdoms',[]): continue
            if 'eligable_kingdoms' in d and o not in d['eligable_kingdoms']: continue
            if 'eligable_realms' in d and not set(rs)&set(d['eligable_realms']): continue
            got+=1
            goal=d.get('own_realms',[])
            missing=[r for r in goal if r not in rs]
            if goal and len(missing)<MIN_TO_CONQUER:
                errs.append(f'{p}: {o} starts with quest {q} needing only {len(missing)} of {len(goal)} realms ({" ".join(missing)})')
        if not got: none.append(o)
    questless[p]=sorted(none)
print('kingdoms without a union quest:')
for p in PERIODS: print(f'  {p} ({len(questless[p])}): {" ".join(questless[p])}')
print(len(errs), 'problems')
print('\n'.join(errs[:60]))
raise SystemExit(1 if errs else 0)
