#!/usr/bin/env python3
"""
找出文字裡提到的大法官解釋（釋字）與憲法法庭判決（憲判字）。

  釋字第603號、釋字603號、司法院釋字第六○三號、釋字第六百零三號
  大法官第603號解釋、司法院大法官釋字第603、631號解釋（列舉）
  111年憲判字第13號、112年度憲判字第1號

find_jyi(text)  → [603, 631]（由小到大、不重複）
find_xp(text)   → ['111-13']
"""
import re

JYI_MAX = 813          # 111 年 1 月 4 日憲法訴訟法施行後不再作成釋字，最後一號是 813

_D = {'零': 0, '〇': 0, '○': 0, 'Ｏ': 0, 'O': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_U = {'十': 10, '百': 100, '千': 1000}


def cn2int(s):
    s = s.translate(str.maketrans('０１２３４５６７８９', '0123456789')).strip()
    if s.isdigit():
        return int(s)
    if not any(c in _U for c in s):                 # 六○三：逐位數字
        try:
            return int(''.join(str(_D[c]) for c in s))
        except KeyError:
            return None
    total, cur = 0, 0                               # 六百零三、二十二、十二
    for c in s:
        if c in _D:
            cur = _D[c]
        elif c in _U:
            total += (cur or 1) * _U[c]; cur = 0
        else:
            return None
    return total + cur


NUM = r'(?:[0-9０-９]+|[零〇○Ｏ一二兩三四五六七八九十百千]+)'
# 列舉：「第603、631號」「第392號及第588號」「第748號解釋與第775號」；最後一定要接「號」，避免把「及第22條」算進來
LIST = rf'{NUM}(?:\s*(?:號\s*(?:解釋)?)?\s*(?:、|，|,|及|與|和|暨)\s*(?:第\s*)?{NUM})*'
_JYI = [
    re.compile(rf'(?:釋字|解釋字)\s*(?:第\s*)?({LIST})\s*號'),
    re.compile(rf'(?:釋字|解釋字)\s*(?:第\s*)?({NUM})(?![0-9０-９零〇○一二三四五六七八九十百])'),   # 「釋字748」沒寫「號」也算
    re.compile(rf'大法官(?:會議)?(?:解釋)?\s*第\s*({LIST})\s*號(?:解釋)?'),
    re.compile(rf'司法院(?:大法官)?(?:會議)?\s*第\s*({LIST})\s*號\s*解釋'),
]
_SPLIT = re.compile(rf'{NUM}')
_XP = re.compile(r'([0-9０-９]{3})\s*年(?:度)?\s*憲判字\s*第\s*([0-9０-９]+|[零〇一二三四五六七八九十]+)\s*號')


def find_jyi(text):
    out = set()
    for rx in _JYI:
        for m in rx.finditer(text or ''):
            for n in _SPLIT.findall(m.group(1)):
                v = cn2int(n)
                if v and 1 <= v <= JYI_MAX:
                    out.add(v)
    return sorted(out)


def find_xp(text):
    out = []
    for m in _XP.finditer(text or ''):
        y, n = cn2int(m.group(1)), cn2int(m.group(2))
        if y and n and 111 <= y <= 200:
            k = f'{y}-{n}'
            if k not in out:
                out.append(k)
    return out


if __name__ == '__main__':
    tests = {
        '依司法院釋字第603號解釋意旨': [603],
        '釋字第六○三號': [603], '釋字第六百零三號解釋': [603], '大法官第六三一號解釋': [631],
        '司法院大法官釋字第603、631號解釋': [603, 631], '釋字第三九二號及第五八八號': [392, 588],
        '依大法官解釋第499號': [499], '司法院第748號解釋': [748], '釋字748': [748], '釋字第748號解釋與第七七五號': [748, 775], '釋字第603號解釋及第22條': [603], '依釋字第603號及憲法第22條': [603],
        '釋字第748號解釋施行法': [748], '依憲法第22條': [], '第900號解釋': [],
        '釋字第 1 號': [1], '釋字第十二號': [12], '釋字第８１３號': [813],
    }
    bad = 0
    for t, want in tests.items():
        got = find_jyi(t)
        if got != want:
            bad += 1; print('✗', t, got, '應為', want)
    for t, want in {'111年憲判字第13號判決': ['111-13'], '依112年度憲判字第1號': ['112-1'], '釋字第603號': []}.items():
        if find_xp(t) != want:
            bad += 1; print('✗', t, find_xp(t), '應為', want)
    print('全部通過' if not bad else f'{bad} 個錯誤')
