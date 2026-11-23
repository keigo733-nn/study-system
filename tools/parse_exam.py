#!/usr/bin/env python3
"""
把考選部「考畢試題」PDF（試題＋測驗式試題標準答案）轉成題庫 JSON。

用法：python3 parse_exam.py 試題.pdf 答案.pdf laws/index.json 輸出.json
需要：poppler 的 pdftotext（Ubuntu: apt install poppler-utils）

輸出：
{ year, exam, subject, code, title,
  essays: [{no, text, score, refs}],
  mcq:    [{no, q, opts:[A,B,C,D], ans, refs}] }
refs = 題目中出現的法條，例如 {"q":"行政程序法102", "law":"行政程序法", "art":"102"}
"""
import json, re, subprocess, sys

OPT_MARKS = {'': 'A', '': 'B', '': 'C', '': 'D', '': 'E'}
CN = {'零': 0, '〇': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn2num(s):
    if s.isdigit():
        return int(s)
    sec = num = 0
    for ch in s:
        if ch in CN:
            num = CN[ch]
        elif ch == '十':
            sec += (num or 1) * 10; num = 0
        elif ch == '百':
            sec += (num or 1) * 100; num = 0
        elif ch == '千':
            sec += (num or 1) * 1000; num = 0
    return sec + num


def pdftext(path, layout=False):
    args = ['pdftotext'] + (['-layout'] if layout else []) + [path, '-']
    return subprocess.run(args, capture_output=True, check=True).stdout.decode('utf-8')


def strip_page_headers(text):
    """移除每頁上方重複的「代號：…／頁次：…」區塊。"""
    out, skipping = [], False
    lines = text.split('\n')
    for i, ln in enumerate(lines):
        s = ln.strip()
        if re.match(r'^代號：[\d\-、]+$', s) and not skipping and \
                any(l.strip().startswith('頁次：') for l in lines[i + 1:i + 10]):
            skipping = True
            continue
        if skipping:
            if s.startswith('頁次：'):
                skipping = False
            continue
        out.append(ln)
    return '\n'.join(out)


def parse_questions(path):
    raw = pdftext(path)
    head = re.sub(r'[ \u3000]', '', raw[:1500])
    meta = {}
    m = re.search(r'(\d{2,3})年(.+?)試題', head)
    if m:
        meta['year'] = int(m.group(1)); meta['title'] = m.group(1) + '年' + m.group(2)
    m = re.search(r'科\s*目：\s*(\S+)', head)
    if m:
        meta['subject'] = m.group(1)
    text = strip_page_headers(raw.replace('\f', '\n'))

    essays, mcq = [], []
    i_mcq = re.search(r'[乙丙]、\s*測驗題部分', text)
    i_essay = re.search(r'[甲乙]、\s*申論題部分', text)
    essay_txt = text[i_essay.end(): i_mcq.start() if i_mcq else len(text)] if i_essay else ''
    mcq_txt = text[i_mcq.end():] if i_mcq else ''
    if not i_essay and not i_mcq:          # 純測驗或純申論的試卷
        if any(c in text for c in OPT_MARKS):
            mcq_txt = text
        else:
            essay_txt = text

    # 申論題：「一、」「二、」開頭
    if essay_txt:
        parts = re.split(r'\n(?=[一二三四五六七八九十]+、)', '\n' + essay_txt)
        for p in parts:
            m = re.match(r'([一二三四五六七八九十]+)、(.*)', p.strip(), re.S)
            if not m:
                continue
            body = re.sub(r'\n(?!「)', '', m.group(2)).strip()
            sm = re.search(r'（(\d+)\s*分）\s*$', body)
            essays.append({'no': cn2num(m.group(1)), 'text': body, 'score': int(sm.group(1)) if sm else None})

    # 測驗題：用每個字的座標重組成行（不同試卷的文字順序不一定可靠）
    if mcq_txt:
        cm = re.search(r'代號：\s*(\d+)', mcq_txt)
        if cm:
            meta['code'] = cm.group(1)
        else:
            cm = re.search(r'代號：\s*(\d+)', head)
            if cm:
                meta['code'] = cm.group(1)
        mcq = parse_mcq_by_position(path, has_essay=bool(i_essay))
    meta['essays'] = essays
    meta['mcq'] = mcq
    return meta


def positioned_lines(path):
    """回傳 [(x0, 文字)]：同一基線（±4pt）的字合成一行，依 x 排序；字距過大處補空格。"""
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            chars = sorted((c for c in pg.chars if c['text'].strip()), key=lambda c: (c['top'], c['x0']))
            rows = []
            for c in chars:
                if rows and c['top'] - rows[-1][0] <= 4:
                    rows[-1][1].append(c)
                else:
                    rows.append([c['top'], [c]])
            for _, cs in rows:
                cs.sort(key=lambda c: c['x0'])
                t, prev = '', None
                for c in cs:
                    if prev is not None:
                        gap = c['x0'] - prev['x1']
                        size = max(prev['size'], 1)
                        if c['text'] in OPT_MARKS:
                            pass
                        elif gap > size * 2.5:
                            t += '＿＿＿'          # 填空用的空白格
                        elif gap > size * 0.3:
                            t += ' '
                    t += c['text']; prev = c
                out.append((cs[0]['x0'], t))
    return out


def parse_mcq_by_position(path, has_essay):
    lines = positioned_lines(path)
    if has_essay:                                   # 跳過申論部分
        for i, (_, t) in enumerate(lines):
            if re.search(r'測驗題部分', t):
                lines = lines[i + 1:]; break
    opt_xs = [x for x, t in lines if t[0] in OPT_MARKS]
    if not opt_xs:
        return []
    opt_x = min(opt_xs)
    mcq, cur, opt, expect = [], None, None, 1
    for x, t in lines:
        s = t.strip()
        if x > 400 or re.match(r'^(代號：|頁次：|說明：|本試題為|共\s*\d+\s*題)', s) or re.fullmatch(r'[\d、\-]{5,}', s):
            continue                                # 頁首、說明文字
        if x < opt_x - 5:
            qm = re.match(r'^(\d{1,3})\s*(.*)$', s)
            if qm and int(qm.group(1)) == expect:
                cur = {'no': expect, 'q': qm.group(2).strip(), 'opts': []}
                mcq.append(cur); opt = None; expect += 1
            else:
                cur = None                          # 「二、複選題」這類段落標題
            continue
        if cur is None:
            continue
        if s[0] in OPT_MARKS:                       # 一行可能有好幾個選項
            for piece in re.split('(?=[' + ''.join(OPT_MARKS) + '])', s):
                if piece and piece[0] in OPT_MARKS:
                    cur['opts'].append(piece[1:].strip()); opt = len(cur['opts']) - 1
        elif opt is None:
            cur['q'] += ('\n' if s.startswith('「') or cur['q'].endswith('」') else '') + s
        else:
            cur['opts'][opt] += s
    return mcq


def parse_answers(path):
    t = pdftext(path, layout=True)
    info = {}
    m = re.search(r'考試名稱：\s*(.+)', t)
    if m:
        info['exam'] = m.group(1).strip()
    m = re.search(r'試題代號：\s*(\d+)', t)
    if m:
        info['code'] = m.group(1)
    ans = {}
    lines = t.split('\n')
    for i, ln in enumerate(lines):
        if ln.strip().startswith('題號'):
            nos = [int(x) for x in re.findall(r'第\s*(\d+)\s*題', ln)]
            for j in range(i + 1, min(i + 3, len(lines))):
                if lines[j].strip().startswith('答案'):
                    toks = lines[j].strip()[2:].split()
                    for n, a in zip(nos, toks):
                        ans[n] = a
                    break
    info['answers'] = ans
    # 單選／複選題範圍與配分
    info['ranges'] = []
    for kind, label in (('single', '單選'), ('multi', '複選')):
        m = re.search(label + r'題數：\s*(\d+)題(?:\(第(\d+)~(\d+)題\))?', t)
        pm = re.search(label + r'每題配分：\s*([\d.]+)', t)
        if m:
            lo, hi = (int(m.group(2)), int(m.group(3))) if m.group(2) else (1, int(m.group(1)))
            info['ranges'].append({'kind': kind, 'from': lo, 'to': hi, 'pts': float(pm.group(1)) if pm else None})
    return info


def build_law_matcher(index_path):
    idx = json.load(open(index_path, encoding='utf-8'))
    names = sorted({l['n'] for l in idx['laws'] if not l['x']}, key=len, reverse=True)
    extra = {'憲法': '中華民國憲法', '刑法': '中華民國刑法'}
    alt = '|'.join(re.escape(n) for n in names + list(extra))
    num = r'(\d+|[零〇一二兩三四五六七八九十百千]+)'
    # 法規名稱可省略：「第 21 條」沿用前面最近提到的法規
    rx = re.compile(rf'({alt}|同法|本法)?(?:\s*第\s*{num}\s*條(?:\s*之\s*{num})?(?:\s*第\s*{num}\s*項)?)|({alt})')

    def find(text):
        refs, seen, last = [], set(), None
        for m in rx.finditer(text):
            if m.group(5):                       # 只有法規名稱
                law = extra.get(m.group(5), m.group(5)); last = law
                if law not in seen:
                    seen.add(law); refs.append({'q': law, 'law': law, 'art': None})
                continue
            law = m.group(1) or '同法'
            if law in ('同法', '本法'):
                if not last or not m.group(2):
                    continue
                law = last
            law = extra.get(law, law)
            last = law
            if m.group(2):
                art = str(cn2num(m.group(2))) + ('-' + str(cn2num(m.group(3))) if m.group(3) else '')
                key = law + art
            else:
                art, key = None, law
            if key in seen:
                continue
            seen.add(key)
            refs.append({'q': key, 'law': law, 'art': art})
        # 有具體條號時，就不另外列同一部法的「整部」標籤
        with_art = {r['law'] for r in refs if r['art']}
        return [r for r in refs if r['art'] or r['law'] not in with_art]
    return find


def build_paper(qpdf, apdf, find):
    """解析一份試卷（apdf 可為 None：純申論）。回傳 (paper, problems)。problems 為空代表通過檢查。"""
    paper = parse_questions(qpdf)
    ans = parse_answers(apdf) if apdf else {'answers': {}, 'ranges': []}
    problems = []
    if paper.get('code') and ans.get('code') and paper['code'] != ans['code']:
        problems.append(f'試題代號 {paper["code"]} 與答案代號 {ans["code"]} 不同')
    paper['exam'] = ans.get('exam', paper.get('title', ''))
    for e in paper['essays']:
        e['refs'] = find(e['text'])
    for q in paper['mcq']:
        q['ans'] = ans['answers'].get(q['no'])
        rg = next((r for r in ans['ranges'] if r['from'] <= q['no'] <= r['to']), None)
        q['multi'] = bool(rg and rg['kind'] == 'multi') or bool(q['ans'] and len(q['ans']) > 1 and q['ans'].isalpha())
        q['pts'] = rg['pts'] if rg else None
        q['refs'] = find(q['q'] + ' ' + ' '.join(q['opts']))
    bad = [q['no'] for q in paper['mcq']
           if len(q['opts']) not in (4, 5) or (not q['multi'] and len(q['opts']) != 4) or not q['ans']]
    if bad:
        problems.append(f'第 {bad[:10]} 題選項或答案不完整')
    if apdf and len(ans['answers']) != len(paper['mcq']):
        problems.append(f'答案 {len(ans["answers"])} 題，試題解析出 {len(paper["mcq"])} 題')
    if not paper['mcq'] and not paper['essays']:
        problems.append('解析不出任何題目（可能是掃描檔）')
    return paper, problems


def main(qpdf, apdf, index_path, out):
    find = build_law_matcher(index_path)
    paper, problems = build_paper(qpdf, apdf if apdf not in ('', '-') else None, find)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(paper, f, ensure_ascii=False, indent=1)
    print(f"{paper.get('title','')} {paper.get('subject','')}：申論 {len(paper['essays'])} 題、測驗 {len(paper['mcq'])} 題"
          + (f"；需人工檢查：{'；'.join(problems)}" if problems else '；全部選項與答案都對上'))


if __name__ == '__main__':
    main(*sys.argv[1:5])
