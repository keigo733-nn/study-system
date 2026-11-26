#!/usr/bin/env python3
"""
推定「這題在考哪一條」：題目沒寫條號時，用題幹＋正確答案跟條文比對文字相似度。

做法：把每一條條文切成 2～3 字的片段（TF-IDF），跟「題幹最後一句＋正確選項」比對，
只在候選法規裡找（題目提到的法規，或從科目名稱看得出來的法規，例如「行政法」→ 行政程序法、訴願法…）。
最像的那一條分數夠高、而且明顯比第二名高，才算「推定」。

驗證（2026-11，用 3,000 題有寫條號的題目當標準答案）：
  門檻 0.30／差距 0.10 → 約 1/3 的題目會被推定；跟題目自己寫的條號一致 77%，
  人工抽查不一致的 25 題，其中 19 題其實推定得對（題目引的是舊條號或相關條文），估計整體正確率約 94%。
"""
import json, os, re
import numpy as np

T_SCORE, T_MARGIN = 0.30, 0.10
ADMIN = ['行政程序法', '行政罰法', '訴願法', '行政訴訟法', '行政執行法', '國家賠償法', '中央法規標準法', '地方制度法',
         '公務員服務法', '公務人員保障法', '政府資訊公開法', '公務員懲戒法', '中央行政機關組織基準法', '公務人員任用法',
         '公務人員考績法', '行政法人法']
KW = {'行政法': ADMIN, '刑法': ['中華民國刑法'], '憲法': ['中華民國憲法', '中華民國憲法增修條文', '憲法訴訟法'],
      '民事訴訟': ['民事訴訟法'], '刑事訴訟': ['刑事訴訟法'], '民法': ['民法'],
      # 法學緒論、法學大意、法學知識：科目名稱看不出是哪部法，用最常考的幾部
      '法學': ['中華民國憲法', '中華民國憲法增修條文', '民法', '中華民國刑法', '民事訴訟法', '刑事訴訟法',
               '行政程序法', '行政罰法', '訴願法', '行政訴訟法', '國家賠償法', '中央法規標準法']}
ART = re.compile(r'第\s*[\d一二三四五六七八九十百千零〇兩]+\s*條(?:\s*之\s*[\d一二三四五六七八九十]+)?'
                 r'(?:\s*第\s*[\d一二三四五六七八九十]+\s*項)?(?:\s*第\s*[\d一二三四五六七八九十]+\s*款)?')


class Inferer:
    def __init__(self, laws_dir):
        from sklearn.feature_extraction.text import TfidfVectorizer
        idx = json.load(open(os.path.join(laws_dir, 'index.json'), encoding='utf-8'))
        self.laws = {}
        for l in idx['laws']:
            if l['x']:
                continue
            f = os.path.join(laws_dir, 'law', l['p'] + '.json')
            if not os.path.exists(f):
                continue
            d = json.load(open(f, encoding='utf-8'))
            arts = [(a['no'], re.sub(r'\s+', '', ''.join(a['lines']))) for a in d['arts'] if a['lines']]
            arts = [(n, t) for n, t in arts if t and not t.startswith(('（刪除）', '刪除'))]
            if arts:
                self.laws[l['n']] = arts
        self.names = sorted(self.laws, key=len, reverse=True)
        self.vec = TfidfVectorizer(analyzer='char', ngram_range=(2, 3), sublinear_tf=True, min_df=2)
        self.vec.fit([t for n in self.laws for _, t in self.laws[n]])
        self.M = {n: self.vec.transform([t for _, t in self.laws[n]]) for n in self.laws}
        self._subj = {}

    def subject_laws(self, s):
        if s not in self._subj:
            out = [n for n in self.names if len(n) >= 3 and n in s]
            for k, v in KW.items():
                if k in s:
                    out += [x for x in v if x in self.laws]
            self._subj[s] = list(dict.fromkeys(out))
        return self._subj[s]

    def infer(self, q, subject):
        """回傳 (法規名稱, 條號, 分數) 或 None。題目有寫條號的不推定。"""
        refs = q.get('refs') or []
        if any(r.get('art') for r in refs):
            return None
        letters = re.sub(r'[^A-E]', '', q.get('ans') or '')
        if not letters or q.get('broken'):
            return None
        cand = list(dict.fromkeys([r['law'] for r in refs if r['law'] in self.laws] + self.subject_laws(subject)))
        if not cand:
            return None
        right = ' '.join(q['opts']['ABCDE'.index(k)] for k in letters if 'ABCDE'.index(k) < len(q['opts']))
        text = ART.sub('', q['q'].split('\n')[-1] + ' ' + right)
        if len(text) < 16:
            return None
        X = self.vec.transform([text])
        best = []
        for n in cand:
            s = (self.M[n] @ X.T).toarray().ravel()
            for i in np.argsort(-s)[:2]:
                best.append((float(s[i]), n, self.laws[n][i][0]))
        best.sort(reverse=True)
        if not best:
            return None
        sc, law, art = best[0]
        second = best[1][0] if len(best) > 1 else 0.0
        return (law, art, round(sc, 3)) if sc >= T_SCORE and sc - second >= T_MARGIN else None


def infer_freq(paper_ids, load, laws_dir):
    """{法規名稱: {條號: [試卷序號, 題號, …]}}，格式跟 artfreq 的 laws 一樣（只有選擇題）"""
    inf, freq = Inferer(laws_dir), {}
    for pi, pid in enumerate(paper_ids):
        try:
            paper = load(pid)
        except Exception:
            continue
        for q in paper.get('mcq', []):
            r = inf.infer(q, paper.get('subject', ''))
            if r:
                freq.setdefault(r[0], {}).setdefault(r[1], []).extend([pi, q['no']])
    return freq
