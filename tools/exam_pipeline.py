#!/usr/bin/env python3
"""
考古題自動抓取：照考選部官方清單（ExamQandA_Csv.csv）下載試題與答案 PDF，解析成題庫。

  python3 exam_pipeline.py --csv data/ExamQandA_Csv.csv --laws laws/index.json --out docs/exams \
      [--since 101] [--minutes 300] [--max 0] [--sleep 1.0] [--mock-dir 測試用PDF資料夾]

輸出（--out 底下）：
  index.json        題庫目錄（精簡格式，給讀書系統用）
  p/<id>.json       每份試卷一個檔
  state.json        處理紀錄（可中斷續跑；更正答案會自動重抓）
  report.md         本次結果與失敗清單

特性：同一份試卷被多個類科共用時只抓一次；新的年度先抓；每次有時間上限，跑不完下次接著跑。
"""
import argparse, csv, gzip, hashlib, io, json, os, re, sys, tempfile, time, urllib.request
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_exam import build_paper, build_law_matcher

UA = 'Mozilla/5.0 (study-system exam bank; contact via repository issues)'


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
                                   'q_url': r['試題網址'].strip(), 'a_url': '', 'fixed': False, 'classes': []}
            cls = r['類科組別'].strip()
            if cls and cls not in p['classes']:
                p['classes'].append(cls)
            a = r['測驗式試題答案網址'].strip()
            if a and (not p['a_url'] or q_param(a, 't') == 'M'):               # 有更正答案就用更正版
                p['a_url'], p['fixed'] = a, q_param(a, 't') == 'M'
    return list(papers.values())


def fetch(url, dest, mock_dir=None, tries=3):
    if mock_dir:                                     # 測試用：依網址參數對應本機檔案
        key = f"{q_param(url, 't')}_{q_param(url, 'code')}_{q_param(url, 's')}.pdf"
        src = os.path.join(mock_dir, key)
        if not os.path.exists(src):
            raise FileNotFoundError('mock 沒有 ' + key)
        with open(src, 'rb') as a, open(dest, 'wb') as b:
            b.write(a.read())
        return
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
                data = r.read()
                if not data.startswith(b'%PDF'):
                    raise ValueError('下載內容不是 PDF')
                f.write(data)
            return
        except Exception as e:
            last = e; time.sleep(3 * (i + 1))
    raise last


def compact(p, paper):
    """存檔前精簡：去掉解析用的暫存欄位，補上清單裡的正式資料。"""
    out = {'id': p['id'], 'year': p['year'], 'exam': p['exam'], 'level': p['level'], 'subject': p['subject'],
           'code': paper.get('code', ''), 'type': p['type'], 'fixed': p['fixed'], 'classes': p['classes'],
           'source': p['q_url'], 'essays': paper['essays'], 'mcq': paper['mcq']}
    return out


def write_index(out_dir, papers, state):
    ok = [p for p in papers if state.get(p['id'], {}).get('status') == 'ok']
    exams, ex_i, cls_t, cls_i = [], {}, [], {}
    rows = []
    for p in sorted(ok, key=lambda p: (-p['year'], p['code'], p['subject'])):
        if p['code'] not in ex_i:
            ex_i[p['code']] = len(exams); exams.append([p['code'], p['year'], p['exam'], p['level']])
        ci = []
        for c in p['classes']:
            if c not in cls_i:
                cls_i[c] = len(cls_t); cls_t.append(c)
            ci.append(cls_i[c])
        st = state[p['id']]
        rows.append([p['id'], ex_i[p['code']], p['subject'], st.get('nm', 0), st.get('ne', 0), 1 if p['fixed'] else 0, ci])
    idx = {'updated': time.strftime('%Y-%m-%d %H:%M'), 'source': '考選部考畢試題',
           'fields': ['id', 'exam', 'subject', 'nMcq', 'nEssay', 'fixed', 'classes'],
           'exams': exams, 'classes': cls_t, 'papers': rows}
    with open(os.path.join(out_dir, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))
    return len(rows)


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
    ap.add_argument('--mock-dir')
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out, 'p'), exist_ok=True)
    state_path = os.path.join(a.out, 'state.json')
    state = json.load(open(state_path, encoding='utf-8')) if os.path.exists(state_path) else {}
    papers = load_papers(a.csv, a.since)
    find = build_law_matcher(a.laws)

    def todo(p):
        st = state.get(p['id'])
        if p['type'] == '實地考試':
            return False
        if not st:
            return True
        if st.get('a_url') != p['a_url']:          # 答案網址變了（例如公布更正答案）
            return True
        return st['status'] == 'fail' and a.retry_failed
    queue = sorted([p for p in papers if todo(p)], key=lambda p: (-p['year'], p['code']))
    print(f'清單共 {len(papers)} 份試卷，待處理 {len(queue)} 份')

    t0, done, ok_n, fail = time.time(), 0, 0, []
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
                paper, problems = build_paper(qf, af if has_a else None, find)
                if p['type'] == '申論題' and not paper['essays'] and not problems:
                    problems.append('申論題卻解析不出題目')
                if problems:
                    raise ValueError('；'.join(problems))
                with open(os.path.join(a.out, 'p', p['id'] + '.json'), 'w', encoding='utf-8') as f:
                    json.dump(compact(p, paper), f, ensure_ascii=False, separators=(',', ':'))
                state[p['id']] = {'status': 'ok', 'a_url': p['a_url'], 'nm': len(paper['mcq']), 'ne': len(paper['essays'])}
                ok_n += 1
            except Exception as e:
                state[p['id']] = {'status': 'fail', 'a_url': p['a_url'], 'why': str(e)[:300]}
                fail.append((p, str(e)[:200]))
            if done % 25 == 0:                       # 定期存檔，中途被中斷也不會白做
                json.dump(state, open(state_path, 'w', encoding='utf-8'), ensure_ascii=False)
                print(f'  進度 {done}/{len(queue)}，成功 {ok_n}')

    json.dump(state, open(state_path, 'w', encoding='utf-8'), ensure_ascii=False)
    if done == 0 and os.path.exists(os.path.join(a.out, 'index.json')):
        print('沒有新的試卷，題庫不變'); return
    total = write_index(a.out, papers, state)
    remain = len([p for p in papers if todo(p)])
    all_fail = [(pid, s['why']) for pid, s in state.items() if s['status'] == 'fail']
    rep = [f'# 題庫更新報告（{time.strftime("%Y-%m-%d %H:%M")}）', '',
           f'- 本次處理 {done} 份：成功 {ok_n}、失敗 {len(fail)}',
           f'- 題庫目前共 {total} 份試卷；清單中還有 {remain} 份待處理',
           f'- 累計失敗 {len(all_fail)} 份（不會上架，修好解析程式後加 --retry-failed 重跑）', '']
    if fail:
        rep += ['## 本次失敗', ''] + [f'- {p["year"]} {p["exam"][:30]}｜{p["subject"][:30]}：{why}' for p, why in fail[:200]]
    open(os.path.join(a.out, 'report.md'), 'w', encoding='utf-8').write('\n'.join(rep) + '\n')
    print('\n'.join(rep[:5]))


if __name__ == '__main__':
    main()
