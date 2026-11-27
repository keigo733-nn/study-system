#!/usr/bin/env python3
"""
推定「這題在考哪一號大法官解釋」：近年考題常寫「依司法院憲法解釋，…」卻不寫號次。

做法跟 infer_articles.py 一樣：把每號解釋的「爭點＋解釋文」切成 2～3 字的片段（TF-IDF），
跟「題幹最後一句＋所有選項」比對（考的是一號解釋的主題，選項通常都在同一個主題裡）。
只處理題目有明說「憲法解釋、大法官、釋字、憲法法庭」的題目（寧可少推定，也不要亂掛）；
最像的那號分數夠高、而且明顯比第二名高，才算「推定」。

驗證：題目自己有寫號次的，拿來當標準答案，算推定的命中率（印在執行紀錄和 artfreq.json 的 jyi_check）。
"""
import json, os, re
import numpy as np

T_SCORE, T_MARGIN = 0.22, 0.06
TRIGGER = re.compile(r'憲法解釋|大法官|釋字|解釋意旨|憲法法庭|憲判字|司法院.{0,6}解釋')


class JyiInferer:
    def __init__(self, jyi_dir):
        from sklearn.feature_extraction.text import TfidfVectorizer
        idx = json.load(open(os.path.join(jyi_dir, 'index.json'), encoding='utf-8'))
        self.nos, docs = [], []
        for it in idx['items']:
            f = os.path.join(jyi_dir, 't', f"{it['no']}.json")
            try:
                d = json.load(open(f, encoding='utf-8'))
            except (OSError, ValueError):
                continue
            t = re.sub(r'\s+', '', (d.get('issue', '') + d.get('issue', '') + d.get('text', '')))   # 爭點加重
            if len(t) > 10:
                self.nos.append(it['no']); docs.append(t)
        big = len(docs) >= 100                       # 資料少（測試）時不能過濾太兇
        self.vec = TfidfVectorizer(analyzer='char', ngram_range=(2, 3), sublinear_tf=True, min_df=2 if big else 1, max_df=0.3 if big else 1.0)
        self.M = self.vec.fit_transform(docs)

    def infer(self, q, subject):
        stem = q.get('q', '')
        if not TRIGGER.search(stem):                  # 寧可少推定，也不要亂掛：題目要明說是依解釋／判決
            return None
        text = re.sub(r'\s+', '', stem.split('\n')[-1] + ''.join(q.get('opts') or []))
        if len(text) < 20:
            return None
        s = (self.M @ self.vec.transform([text]).T).toarray().ravel()
        order = np.argsort(-s)[:2]
        sc, second = float(s[order[0]]), (float(s[order[1]]) if len(order) > 1 else 0.0)
        return (self.nos[order[0]], round(sc, 3)) if sc >= T_SCORE and sc - second >= T_MARGIN else None


def infer_jyi_freq(paper_ids, load, jyi_dir, explicit):
    """回傳 (freq, check)。freq：{號次: [試卷序號, 題號, …]}，只放題目沒寫號次、被推定的選擇題。
    explicit：{(試卷序號, 題號): [題目寫出的號次]}，用來驗證命中率。"""
    inf, freq = JyiInferer(jyi_dir), {}
    hit = tried = 0
    for pi, pid in enumerate(paper_ids):
        try:
            paper = load(pid)
        except Exception:
            continue
        for q in paper.get('mcq', []):
            r = inf.infer(q, paper.get('subject', ''))
            have = explicit.get((pi, q['no']))
            if have:                                  # 有寫號次：只拿來驗證，不重複計入
                if r:
                    tried += 1; hit += r[0] in have
                continue
            if r:
                freq.setdefault(str(r[0]), []).extend([pi, q['no']])
    check = {'tried': tried, 'hit': hit, 'rate': round(hit / tried, 3) if tried else None}
    return freq, check
