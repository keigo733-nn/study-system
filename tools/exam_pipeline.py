#!/usr/bin/env python3
"""
考古題自動抓取：照考選部官方清單（ExamQandA_Csv.csv）下載試題與答案 PDF，解析成題庫。

  python3 exam_pipeline.py --csv data/ExamQandA_Csv.csv --laws laws/index.json --out docs/exams \
      [--since 101] [--minutes 300] [--max 0] [--sleep 1.0] [--only-law] [--mock-dir 測試用PDF資料夾]

  --only-law  只收法律科目（行政法、民法、法學知識、各種法規…），其他科目的試卷會從題庫移除

輸出（--out 底下）：
  index.json        題庫目錄（精簡格式，給讀書系統用）
  artfreq.json      每一條法條被考過幾次（題目裡明文提到的才算），以及是哪幾題
  p/<id>.json       每份試卷一個檔
  state.json        處理紀錄（可中斷續跑；更正答案會自動重抓）
  report.md         本次結果與失敗清單

特性：同一份試卷被多個類科共用時只抓一次；新的年度先抓；每次有時間上限，跑不完下次接著跑。
"""
import argparse, csv, gzip, hashlib, io, json, os, re, sys, tempfile, time, urllib.request
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_exam import build_paper, build_law_matcher, PARSER_VERSION, REFS_VERSION

UA = 'Mozilla/5.0 (study-system exam bank; contact via repository issues)'

# 檢查版本：加了新的檢查（例如試題／答案交叉比對）時加 1，已上架的試卷會在沒有新試卷要抓時，慢慢重新檢查一次
CHECK_VERSION = 1

# ---------- 法科判斷（--only-law） ----------
LAW_KW = ('法學', '法律', '法規', '法制', '法令', '憲法', '行政法', '民法', '刑法', '商事法', '訴訟', '程序法', '公務員法',
          '智慧財產', '國際公法', '國際私法', '國際法', '海洋法', '立法', '財稅法', '社會法', '保險法', '票據法', '強制執行')
NOT_LAW = ('研究法', '方法', '平差法', '療法', '語法', '文法', '法醫', '法語', '法文', '教學法', '執法', '算法', '應用法', '測量法', '分析法')


def law_subject_test(laws_index):
    """回傳 is_law(科目名稱)。科目名稱含法律關鍵字，或含任一部法規的全名（例如「公司法」「政府採購法」）就算法科。"""
    names = sorted({l['n'] for l in json.load(open(laws_index, encoding='utf-8'))['laws'] if len(l['n']) >= 3}, key=len, reverse=True)
    rx = re.compile('|'.join(map(re.escape, LAW_KW + tuple(names))))
    def is_law(subject):
        s = subject
        for w in NOT_LAW:
            s = s.replace(w, '')
        return bool(rx.search(s))
    return is_law


def q_param(url, k):
    m = re.search(r'[?&]' + k + r'=([^&]+)', url or '')
    return m.group(1) if m else ''


def load_papers(csv_path, since):
    """把清單合併成「一份試卷一筆」。同考試、同科目代碼 s、同科目名稱＝同一份。"""
    papers = OrderedDict()
    opener = gzip.open if csv_path.endswith('.gz') else open
    with opener(csv_path, 'rt', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            year = int(r['考試年度'] or 0)
            if year < since or not r['試題網址']:
                continue
            code, s = r['考試代碼'], q_param(r['試題網址'], 's')
            subj = r['科目全名'].strip()
            pid = f"{code}-{s}" if s else f"{code}-{hashlib.md5(subj.encode()).hexdigest()[:6]}"
            if pid in papers and papers[pid]['subject'] != subj:       # 少數舊專技考試 s 不唯一
                pid += '-' + hashlib.md5(subj.encode()).hexdigest()[:4]
            p = papers.get(pid)
            if not p:
                p = papers[pid] = {'id': pid, 'year': year, 'code': code, 'exam': r['考試名稱'].strip(),
                                   'level': r['等級分類'].strip(), 'subject': subj, 'type': r['試題型態'].strip(),
                                   'q_url': r['試題網址'].strip(), 'a_url': '', 'fixed': False, 'classes': [], 'levels': []}
            lv = r['等級分類'].strip()
            if lv and lv not in p['levels']:           # 同一份試卷可能同時用在好幾個等級（例如高普考、地特三四五等）
                p['levels'].append(lv)
            cls = r['類科組別'].strip()
            if cls and cls not in p['classes']:
                p['classes'].append(cls)
            a = r['測驗式試題答案網址'].strip()
            # 答案只從「試題網址相同」的那一列拿：同科目不同類科（例如藥師、藥師（二））可能是不同的試卷
            if a and r['試題網址'].strip() == p['q_url'] and (not p['a_url'] or q_param(a, 't') == 'M'):   # 有更正答案就用更正版
                p['a_url'], p['fixed'] = a, q_param(a, 't') == 'M'
    return list(papers.values())


class DownloadError(Exception):
    """下載問題（網路、被擋、回傳不是 PDF）：不記成永久失敗，下次再試。"""


def fetch(url, dest, mock_dir=None, tries=3):
    if mock_dir:                                     # 測試用：依網址參數對應本機檔案
        key = f"{q_param(url, 't')}_{q_param(url, 'code')}_{q_param(url, 's')}.pdf"
        src = os.path.join(mock_dir, key)
        if not os.path.exists(src):
            raise DownloadError('mock 沒有 ' + key)
        data = open(src, 'rb').read()
        if not data.startswith(b'%PDF'):
            raise DownloadError(f'回傳的不是 PDF（mock，{len(data)} bytes）')
        with open(dest, 'wb') as b:
            b.write(data)
        return
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data, ctype = r.read(), r.headers.get('Content-Type', '')
            if not data.startswith(b'%PDF'):
                head = re.sub(rb'\s+', b' ', data[:160]).decode('utf-8', 'replace')
                raise DownloadError(f'回傳的不是 PDF（{ctype}，{len(data)} bytes）：{head}')
            with open(dest, 'wb') as f:
                f.write(data)
            return
        except Exception as e:
            last = e; time.sleep(3 * (i + 1))
    raise last if isinstance(last, DownloadError) else DownloadError(f'下載失敗：{last}')


def compact(p, paper):
    """存檔前精簡：去掉解析用的暫存欄位，補上清單裡的正式資料。"""
    out = {'id': p['id'], 'year': p['year'], 'exam': p['exam'], 'level': '、'.join(p.get('levels') or [p['level']]), 'subject': p['subject'],
           'code': paper.get('code', ''), 'type': p['type'], 'fixed': p['fixed'], 'classes': p['classes'],
           'source': p['q_url'], 'answer': p['a_url'], 'essays': paper['essays'], 'mcq': paper['mcq']}
    return out


def write_index(out_dir, papers, state):
    ok = [p for p in papers if state.get(p['id'], {}).get('status') == 'ok']
    exams, ex_i, cls_t, cls_i, lv_t, lv_i = [], {}, [], {}, [], {}
    rows = []
    for p in sorted(ok, key=lambda p: (-p['year'], p['code'], p['subject'])):
        if p['code'] not in ex_i:
            ex_i[p['code']] = len(exams); exams.append([p['code'], p['year'], p['exam'], p['level']])
        ci = []
        for c in p['classes']:
            if c not in cls_i:
                cls_i[c] = len(cls_t); cls_t.append(c)
            ci.append(cls_i[c])
        li = []
        for lv in p.get('levels') or [p['level']]:
            if lv not in lv_i:
                lv_i[lv] = len(lv_t); lv_t.append(lv)
            li.append(lv_i[lv])
        st = state[p['id']]
        rows.append([p['id'], ex_i[p['code']], p['subject'], st.get('nm', 0), st.get('ne', 0), 1 if p['fixed'] else 0, ci, li])
    idx = {'updated': time.strftime('%Y-%m-%d %H:%M'), 'source': '考選部考畢試題',
           'fields': ['id', 'exam', 'subject', 'nMcq', 'nEssay', 'fixed', 'classes', 'levels'],
           'exams': exams, 'classes': cls_t, 'levels': lv_t, 'papers': rows}
    with open(os.path.join(out_dir, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))
    write_artfreq(out_dir, [r[0] for r in rows])
    return len(rows)


def artfreq_of(paper_ids, load):
    """{法規名稱: {條號: [試卷序號, 題號, 試卷序號, 題號, …]}}；申論題的題號記成負數（-1＝申論第一題）。
    只算題目或選項裡明文寫出條號的（「民法第184條」「同法第5條」），所以實際考到的次數只會更多。"""
    freq = {}
    for pi, pid in enumerate(paper_ids):
        try:
            paper = load(pid)
        except Exception:
            continue
        items = [(q['no'], q.get('refs') or []) for q in paper.get('mcq', [])] + \
                [(-e['no'], e.get('refs') or []) for e in paper.get('essays', [])]
        for no, refs in items:
            seen = set()
            for r in refs:
                if not r.get('art') or (r['law'], r['art']) in seen:
                    continue
                seen.add((r['law'], r['art']))
                freq.setdefault(r['law'], {}).setdefault(r['art'], []).extend([pi, no])
    return freq


FREQ_VERSION = 3          # 2：加上推定（inf）；3：加上大法官解釋（jyi）、憲法法庭判決（xp）。版本不同時會重新統計一次


def jyi_freq_of(paper_ids, load):
    """題目或選項（申論題就是題目）明文提到的釋字、憲判字 → {號次: [試卷序號, 題號, …]}"""
    from jyi_refs import find_jyi, find_xp
    jyi, xp = {}, {}
    for pi, pid in enumerate(paper_ids):
        try:
            paper = load(pid)
        except Exception:
            continue
        items = [(q['no'], q.get('q', '') + ' ' + ' '.join(q.get('opts') or []) + ' ' + q.get('passage', '')) for q in paper.get('mcq', [])] + \
                [(-e['no'], e.get('text', '') + ' ' + e.get('passage', '')) for e in paper.get('essays', [])]
        for no, text in items:
            for n in find_jyi(text):
                jyi.setdefault(str(n), []).extend([pi, no])
            for k in find_xp(text):
                xp.setdefault(k, []).extend([pi, no])
    return jyi, xp


def jyi_count():
    try:
        with open(os.path.join(JYI_DIR, 'index.json'), encoding='utf-8') as f:
            return json.load(f).get('count', 0)
    except (OSError, ValueError):
        return 0


def freq_current(out_dir):
    """統計版本相同、而且大法官解釋資料沒有變多（資料抓到之後要重新推定一次）"""
    try:
        with open(os.path.join(out_dir, 'artfreq.json'), encoding='utf-8') as f:
            d = json.load(f)
        return d.get('v') == FREQ_VERSION and d.get('jyi_n', 0) == jyi_count()
    except (OSError, ValueError):
        return False


LAWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'laws')
JYI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'jyi')


def write_artfreq(out_dir, paper_ids):
    def load(pid):
        with open(os.path.join(out_dir, 'p', pid + '.json'), encoding='utf-8') as f:
            return json.load(f)
    freq = artfreq_of(paper_ids, load)
    out = {'v': FREQ_VERSION, 'updated': time.strftime('%Y-%m-%d %H:%M'), 'ids': paper_ids, 'laws': freq}
    out['jyi'], out['xp'] = jyi_freq_of(paper_ids, load)
    out['jyi_n'] = jyi_count()
    print('提到大法官解釋的題目：', sum(len(v) // 2 for v in out['jyi'].values()), '題；憲判字：', sum(len(v) // 2 for v in out['xp'].values()), '題')
    # 推定：「依司法院憲法解釋，…」沒寫號次的題目（要先有 docs/jyi 的解釋資料，見 build_jyi.py）
    if os.path.exists(os.path.join(JYI_DIR, 'index.json')):
        try:
            from infer_jyi import infer_jyi_freq
            explicit = {}
            for n, arr in out['jyi'].items():
                for i in range(0, len(arr), 2):
                    explicit.setdefault((arr[i], arr[i + 1]), []).append(int(n))
            out['jyi_inf'], out['jyi_check'] = infer_jyi_freq(paper_ids, load, JYI_DIR, explicit)
            print('推定大法官解釋：', sum(len(v) // 2 for v in out['jyi_inf'].values()), '題；驗證', out['jyi_check'])
        except ImportError:
            pass
    # 推定：題目沒寫條號，但題幹＋正確答案跟某一條的文字很像（見 infer_articles.py）
    try:
        from infer_articles import infer_freq
        if os.path.exists(os.path.join(LAWS_DIR, 'index.json')):
            out['inf'] = infer_freq(paper_ids, load, LAWS_DIR)
            print('推定條文：', sum(len(v) // 2 for a in out['inf'].values() for v in a.values()), '題')
    except ImportError:
        print('沒有安裝 scikit-learn，這次只統計題目明文寫出的條號')
    with open(os.path.join(out_dir, 'artfreq.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))


def refresh_refs(out_dir, papers, state, find):
    """法條辨識改版：用試卷檔裡存好的題目文字重新找出提到的法條（不用重新下載 PDF）"""
    n = 0
    for p in papers:
        st = state.get(p['id'])
        if not st or st.get('status') != 'ok' or st.get('rv', 1) >= REFS_VERSION:
            continue
        f = os.path.join(out_dir, 'p', p['id'] + '.json')
        try:
            with open(f, encoding='utf-8') as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        for q in d.get('mcq', []):
            q['refs'] = find(q['q'] + ' ' + ' '.join(q['opts']))
        for e in d.get('essays', []):
            e['refs'] = find(e['text'])
        with open(f, 'w', encoding='utf-8') as fh:
            json.dump(d, fh, ensure_ascii=False, separators=(',', ':'))
        st['rv'] = REFS_VERSION; n += 1
    if n:
        print(f'重新辨識題目裡的法條：{n} 份')
    return n


def backfill_answer_links(out_dir, papers, state):
    """已上架的試卷補上官方答案 PDF 網址（不用重新下載），讀書系統就能顯示「官方答案」連結。"""
    n = 0
    for p in papers:
        st = state.get(p['id'])
        if not st or st.get('status') != 'ok' or st.get('al'):
            continue
        f = os.path.join(out_dir, 'p', p['id'] + '.json')
        try:
            with open(f, encoding='utf-8') as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        d['answer'] = st.get('a_url') or p['a_url']
        with open(f, 'w', encoding='utf-8') as fh:
            json.dump(d, fh, ensure_ascii=False, separators=(',', ':'))
        st['al'] = 1; n += 1
    if n:
        print(f'已補上官方答案連結：{n} 份')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', required=True)
    ap.add_argument('--laws', required=True, help='laws/index.json（用來辨識題目裡的法條）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--since', type=int, default=101)
    ap.add_argument('--minutes', type=float, default=300, help='這次最多跑幾分鐘')
    ap.add_argument('--max', type=int, default=0, help='這次最多處理幾份（0＝不限）')
    ap.add_argument('--sleep', type=float, default=1.0, help='每次下載間隔秒數（對考選部網站客氣一點）')
    ap.add_argument('--retry-failed', action='store_true', help='連先前失敗的也重試')
    ap.add_argument('--only-law', action='store_true', help='只收法律科目')
    ap.add_argument('--mock-dir')
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out, 'p'), exist_ok=True)
    state_path = os.path.join(a.out, 'state.json')
    state = json.load(open(state_path, encoding='utf-8')) if os.path.exists(state_path) else {}
    papers = load_papers(a.csv, a.since)
    if a.only_law:
        is_law = law_subject_test(a.laws)
        n0 = len(papers); papers = [p for p in papers if is_law(p['subject'])]
        print(f'只收法律科目：{n0} 份 → {len(papers)} 份')
    find = build_law_matcher(a.laws)
    backfill_answer_links(a.out, papers, state)
    refs_changed = refresh_refs(a.out, papers, state, find)

    def todo(p):
        st = state.get(p['id'])
        if p['type'] == '實地考試':
            return False
        if not st:
            return True
        if st.get('a_url') != p['a_url']:          # 答案網址變了（例如公布更正答案）
            return True
        if st['status'] == 'missing':              # 考選部連續多次沒有給 PDF：不再嘗試
            return False
        if st['status'] == 'net':                  # 之前下載有問題：重試
            return True
        if st['status'] == 'fail' and st.get('pv', 1) < PARSER_VERSION:   # 解析程式改版 → 自動重試
            return True
        if st['status'] == 'ok' and st.get('cv', 0) < CHECK_VERSION:     # 已上架、但還沒經過新的檢查 → 重新檢查
            return True
        return st['status'] == 'fail' and a.retry_failed
    # 新試卷優先；已上架的重新檢查排在最後，有空才做
    queue = sorted([p for p in papers if todo(p)],
                   key=lambda p: (state.get(p['id'], {}).get('status') == 'ok', -p['year'], p['code']))
    recheck_n = sum(1 for p in queue if state.get(p['id'], {}).get('status') == 'ok')
    print(f'清單共 {len(papers)} 份試卷，待處理 {len(queue)} 份（其中 {recheck_n} 份是已上架試卷的重新檢查）')

    t0, done, ok_n, fail, net_fail, streak = time.time(), 0, 0, [], 0, 0
    pulled = []                                     # 重新檢查後下架的試卷
    with tempfile.TemporaryDirectory() as tmp:
        for p in queue:
            if a.max and done >= a.max:
                break
            if (time.time() - t0) / 60 > a.minutes:
                print('達到時間上限，下次接著跑'); break
            done += 1
            qf, af = os.path.join(tmp, 'q.pdf'), os.path.join(tmp, 'a.pdf')
            try:
                fetch(p['q_url'], qf, a.mock_dir); time.sleep(0 if a.mock_dir else a.sleep)
                has_a = bool(p['a_url'])
                if has_a:
                    fetch(p['a_url'], af, a.mock_dir); time.sleep(0 if a.mock_dir else a.sleep)
                paper, problems = build_paper(qf, af if has_a else None, find, p['subject'], p['year'])
                if p['type'] == '申論題' and not paper['essays'] and not problems:
                    problems.append('申論題卻解析不出題目')
                if problems:
                    raise ValueError('；'.join(problems))
                with open(os.path.join(a.out, 'p', p['id'] + '.json'), 'w', encoding='utf-8') as f:
                    json.dump(compact(p, paper), f, ensure_ascii=False, separators=(',', ':'))
                state[p['id']] = {'status': 'ok', 'a_url': p['a_url'], 'nm': len(paper['mcq']), 'ne': len(paper['essays']),
                                  'cv': CHECK_VERSION, 'al': 1, 'rv': REFS_VERSION}
                ok_n += 1; streak = 0
            except DownloadError as e:                 # 下載問題：下次重試；連續 3 次都回傳網頁（不是 PDF）就不再嘗試
                net_fail += 1; streak += 1
                prev = state.get(p['id'], {})
                if prev.get('status') == 'ok':         # 重新檢查時下載失敗：不要把已上架的試卷下架，下次再檢查
                    print(f'  ⚠ 重新檢查時下載失敗 {p["id"]}：{str(e)[:120]}')
                    if streak >= 30:
                        print('連續 30 份下載失敗，這次先停'); break
                    continue
                nf = (prev.get('nf', 0) + 1) if '不是 PDF' in str(e) else 0
                state[p['id']] = {'status': 'missing' if nf >= 3 else 'net', 'a_url': p['a_url'], 'nf': nf, 'why': str(e)[:200]}
                if '不是 PDF' in str(e):
                    streak = 0                         # 單份沒有檔案，不代表被擋
                print(f'  ⚠ 下載問題 {p["id"]}｜{p["subject"][:20]}：{str(e)[:220]}')
                if streak >= 30:
                    print('連續 30 份下載失敗，考選部可能暫時擋下或連不上，這次先停，下次再接著跑'); break
                if streak >= 5:
                    print(f'  連續 {streak} 份下載失敗，暫停 {min(300, streak * 20)} 秒'); time.sleep(0 if a.mock_dir else min(300, streak * 20))
            except Exception as e:                     # 解析問題：記成失敗，不上架
                streak = 0
                if state.get(p['id'], {}).get('status') == 'ok':
                    pulled.append((p, str(e)[:200]))
                    try: os.remove(os.path.join(a.out, 'p', p['id'] + '.json'))
                    except OSError: pass
                state[p['id']] = {'status': 'fail', 'a_url': p['a_url'], 'why': str(e)[:300], 'pv': PARSER_VERSION}
                fail.append((p, str(e)[:200]))
                print(f'  ✗ 解析失敗 {p["id"]}｜{p["year"]} {p["level"]}｜{p["subject"][:20]}｜{p["type"]}：{str(e)[:200]}')
            if done % 25 == 0:                       # 定期存檔，中途被中斷也不會白做
                json.dump(state, open(state_path, 'w', encoding='utf-8'), ensure_ascii=False)
                print(f'  進度 {done}/{len(queue)}，成功 {ok_n}，解析失敗 {len(fail)}，下載問題 {net_fail}')

    json.dump(state, open(state_path, 'w', encoding='utf-8'), ensure_ascii=False)
    stale = a.only_law and any(f[:-5] not in {p['id'] for p in papers} for f in os.listdir(os.path.join(a.out, 'p')))
    if done == 0 and not stale and not refs_changed and os.path.exists(os.path.join(a.out, 'index.json')) and freq_current(a.out):
        print('沒有新的試卷，題庫不變'); return
    total = write_index(a.out, papers, state)
    if a.only_law:                                 # 清掉不在題庫裡的試卷檔（非法科），網站容量才會變小
        keep = {p['id'] for p in papers if state.get(p['id'], {}).get('status') == 'ok'}
        gone = [f for f in os.listdir(os.path.join(a.out, 'p')) if f.endswith('.json') and f[:-5] not in keep]
        for f in gone:
            os.remove(os.path.join(a.out, 'p', f))
        if gone:
            print(f'移除非法科試卷檔 {len(gone)} 個')
    remain = len([p for p in papers if todo(p) and state.get(p['id'], {}).get('status') != 'ok'])
    unchecked = sum(1 for p in papers if state.get(p['id'], {}).get('status') == 'ok' and state[p['id']].get('cv', 0) < CHECK_VERSION)
    all_fail = [(pid, s['why']) for pid, s in state.items() if s['status'] == 'fail']
    missing = sum(1 for s in state.values() if s['status'] == 'missing')
    rep = [f'# 題庫更新報告（{time.strftime("%Y-%m-%d %H:%M")}）', '',
           f'- 本次處理 {done} 份：成功 {ok_n}、解析失敗 {len(fail)}、下載問題 {net_fail}（下載問題下次會自動重試）',
           f'- 題庫目前共 {total} 份試卷；清單中還有 {remain} 份待處理',
           f'- 累計解析失敗 {len(all_fail)} 份（不會上架；解析程式改版後會自動重試）',
           f'- 考選部沒有提供 PDF 的試卷 {missing} 份（連續 3 次都下載不到，已不再嘗試）',
           f'- 已上架試卷還有 {unchecked} 份等待重新檢查（試題／答案交叉比對）；本次重新檢查後下架 {len(pulled)} 份', '']
    if pulled:
        rep += ['## 重新檢查後下架', ''] + [f'- {p["year"]} {p["exam"][:30]}｜{p["subject"][:30]}：{why}' for p, why in pulled[:200]] + ['']
    if fail:
        rep += ['## 本次失敗', ''] + [f'- {p["year"]} {p["exam"][:30]}｜{p["subject"][:30]}：{why}' for p, why in fail[:200]]
    open(os.path.join(a.out, 'report.md'), 'w', encoding='utf-8').write('\n'.join(rep) + '\n')
    print('\n'.join(rep[:5]))


if __name__ == '__main__':
    main()
