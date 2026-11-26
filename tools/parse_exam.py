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
import json, re, subprocess, sys, unicodedata

# 解析程式版本：改版後，先前解析失敗的試卷會自動重試
PARSER_VERSION = 8          # 8：加上試題／答案檔交叉比對
REFS_VERSION = 2            # 法條辨識改版時加 1：已上架的試卷會用存好的文字重新辨識（不用重新下載）

SUB_MARKS = {'\ue129': '①', '\ue12a': '②', '\ue12b': '③', '\ue12c': '④', '\ue12d': '⑤', '\ue12e': '⑥'}   # 子題、注意事項的圈號

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
    m = re.search(r'科\s*目(?:名稱)?：\s*(\S+)', head)
    if m:
        meta['subject'] = m.group(1)
    m = re.search(r'代號：(\d{3,5})(?!\d)', head)
    if m:
        meta['code'] = m.group(1)
    # 試題上出現的所有代號（頁首的類科代號、測驗部分的試題代號），用來跟答案檔交叉比對
    meta['codes'] = sorted({c for m in re.finditer(r'代\s*號\s*[:：]\s*((?:\d[\d\s\-、,，]*\n?)+)', unicodedata.normalize('NFKC', raw))
                            for c in re.findall(r'\d{4,5}', m.group(1))})
    text = strip_page_headers(raw.replace('\f', '\n'))

    essays, mcq = [], []
    i_mcq = re.search(r'(?:[乙丙丁]|[二三四])、\s*測驗(?:題)?(?:部分)?\s*[：:（(\n]', text)
    i_essay = re.search(r'(?:[甲乙]|[一二])、\s*(?:申論|作文|公文|翻譯)(?:題)?(?:部分)?\s*[：:（(\n]', text)
    essay_txt = text[i_essay.end(): i_mcq.start() if i_mcq else len(text)] if i_essay else ''
    mcq_txt = text[i_mcq.end():] if i_mcq else ''
    if not i_essay and not i_mcq:          # 純測驗或純申論的試卷
        if any(c in text for c in OPT_MARKS) or len(re.findall(r'(?m)^\s*[A-D][.．]', text)) >= 8 \
                or (re.search(r'選擇題', head) and not re.search(r'(?m)^\s*一、', text)):
            mcq_txt = text
        else:
            essay_txt = text

    # 申論題：「一、」「二、」開頭
    if essay_txt:
        essay_txt = essay_txt.translate(str.maketrans(SUB_MARKS))
        parts = re.split(r'\n(?=[一二三四五六七八九十]+、)', '\n' + essay_txt)
        pre = strip_notices(parts[0]) if parts else ''
        by_heading = False
        for p in parts:
            m = re.match(r'([一二三四五六七八九十]+)、(.*)', p.strip(), re.S)
            if not m:
                continue
            body = re.sub(r'\n(?![「①②③④⑤⑥]|[（(][一二三四五六七八九十]+[）)])', '', m.group(2)).strip()
            sm = re.search(r'（(\d+)\s*分）\s*$', body)
            essays.append({'no': cn2num(m.group(1)), 'text': body, 'score': int(sm.group(1)) if sm else None})
        by_heading = bool(essays)
        if not essays:
            parts = re.split(r'\n(?=\d{1,2}[.．])', '\n' + strip_notices(essay_txt))
            for p in parts:
                m = re.match(r'(\d{1,2})[.．](.*)', p.strip(), re.S)
                if not m:
                    continue
                body = re.sub(r'\n(?![「①②③④⑤⑥]|[（(][一二三四五六七八九十]+[）)])', '', m.group(2)).strip()
                sm = re.search(r'（(\d+)\s*分）\s*$', body)
                essays.append({'no': int(m.group(1)), 'text': body, 'score': int(sm.group(1)) if sm else None})
            if not all(e['score'] for e in essays):
                essays = []
        if not essays:
            essays = split_essays_by_score(essay_txt)
        if not essays and i_essay:
            body = strip_notices(essay_txt)
            if len(body) > 20:
                hm = re.search(r'[（(](\d+)\s*分[）)]', text[i_essay.start():i_essay.start() + 40])
                essays = [{'no': 1, 'text': re.sub(r'\n(?![「①②③④⑤⑥]|[（(][一二三四五六七八九十]+[）)])', '', body), 'score': int(hm.group(1)) if hm else None}]
        if by_heading and len(pre) > 60:          # 多題共用的引文（作文題常見）
            essays[0]['passage'] = re.sub(r'\n', '', pre)

    # 測驗題：用每個字的座標重組成行（不同試卷的文字順序不一定可靠）
    if mcq_txt:
        cm = re.search(r'代\s*號：\s*(\d+)\s*\n', mcq_txt)
        if cm and not meta.get('code'):
            meta['code'] = cm.group(1)
        elif cm and i_mcq:
            meta['code'] = cm.group(1)
        mcq = parse_mcq_by_position(path, has_essay=bool(i_essay))
    for e in essays:                               # 配分：子題分數加總
        sc = [int(x) for x in re.findall(r'[（(](\d+)\s*分[）)]', e['text'])]
        if len(sc) > 1:
            e['score'] = sum(sc)
        if re.search(r'(?<![（(\d])\d+\s*分[）)]', e['text']):   # 子題文字是圖片，只剩「15 分）」
            e['broken'] = True
    meta['essays'] = essays
    meta['mcq'] = mcq
    return meta


def strip_notices(txt):
    """去掉開頭的考試資訊、注意事項。"""
    cut = 0
    for kw in ('應使用本國文字作答', '不予計分', '禁止使用電子計算器', '座號', '橫式作答', '姓名或座號', '作答內容'):
        for m in re.finditer(re.escape(kw), txt[:2500]):
            cut = max(cut, txt.find('\n', m.end()) + 1)
    return txt[cut:].strip()


def split_essays_by_score(txt):
    """題號是圖片、文字裡沒有「一、」的試卷：以每題結尾的「（25 分）」切開；①② 子題併回上一題。"""
    # 去掉開頭的考試資訊與注意事項
    body = strip_notices(txt)
    parts = re.split(r'(?<=分）)\s*\n', body)
    essays = []
    for part in parts:
        part = part.strip()
        if not part or not re.search(r'分）$', part):
            continue
        text = re.sub(r'\n(?![「①②③④⑤⑥]|[（(][一二三四五六七八九十]+[）)])', '', part).strip()
        if essays and re.match(r'^[①②③④⑤⑥]|^[（(][一二三四五六七八九十]+[）)]', text):
            essays[-1]['text'] += '\n' + text
            sm = re.search(r'（(\d+)\s*分）$', text)
            if sm and essays[-1]['score']:
                essays[-1]['score'] += int(sm.group(1))
            continue
        sm = re.search(r'（(\d+)\s*分）$', text)
        essays.append({'no': len(essays) + 1, 'text': text, 'score': int(sm.group(1)) if sm else None})
    return essays


def positioned_lines(path, img_marks=False):
    """回傳 [(x0, 文字)]：同一基線（±4pt）的字合成一行，依 x 排序；字距過大處補空格。
    img_marks：選項符號是小圖片的試卷（例如 115 高考三級），把小圖片當成選項符號補進去。"""
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            chars = [c for c in pg.chars if c['text'].strip()]
            if img_marks:
                for im in pg.images:
                    if 15 <= im['width'] <= 36 and 8 <= im['height'] <= 16:
                        chars.append({'text': next(iter(OPT_MARKS)), 'x0': im['x0'], 'x1': im['x1'],
                                      'top': im['top'] + 1, 'size': im['height']})
            chars = sorted(chars, key=lambda c: (c['top'], c['x0']))
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
                        elif gap > size * (0.12 if prev['text'].isascii() and c['text'].isascii() else 0.3):
                            t += ' '
                    t += c['text']; prev = c
                out.append((cs[0]['x0'], t))
    return out


def parse_mcq_by_position(path, has_essay, img_marks=False):
    lines = positioned_lines(path, img_marks)
    if has_essay:                                   # 跳過申論部分
        for i, (_, t) in enumerate(lines):
            if re.search(r'(?:[乙丙丁]|[二三四])、\s*測驗', t):
                lines = lines[i + 1:]; break
    # 選項格式一：特殊符號（多數公務人員考試）；格式二：「A.」「B.」（專技高考等）
    pua = any(t[0] in OPT_MARKS for _, t in lines)
    OPT_RE = re.compile(r'^([A-E])[.．]\s*')
    if not pua and not any(OPT_RE.match(t) for _, t in lines):
        return parse_mcq_by_position(path, has_essay, img_marks=True) if not img_marks else []
    is_opt = (lambda t: t[0] in OPT_MARKS) if pua else (lambda t: bool(OPT_RE.match(t)))
    opt_xs = [x for x, t in lines if is_opt(t)]
    if not opt_xs:
        return []
    opt_x = min(opt_xs)
    numbered = any(x < opt_x - 5 and re.match(r'^\d{1,3}[.．]?\s*\S', t.strip()) for x, t in lines)
    if not numbered:
        return parse_unnumbered(lines, opt_x, is_opt, pua, OPT_RE)
    mcq, cur, opt, expect = [], None, None, 1
    group = None                                   # 題組：{'from','to','text'}
    def next_starts_opt(i):
        for x2, t2 in lines[i + 1:i + 3]:
            t2 = t2.strip()
            if t2:
                return is_opt(t2)
        return False
    for li, (x, t) in enumerate(lines):
        s = t.strip().translate(str.maketrans(SUB_MARKS))
        gm = re.match(r'^請(?:依|閱讀)下(?:文|列短文|列文章)?.*?回答第\s*(\d+)\s*題(?:至|到|～|~|-)第?\s*(\d+)\s*題', s.replace(' ', ''))
        if gm:
            group = {'from': int(gm.group(1)), 'to': int(gm.group(2)), 'text': ''}
            cur = None
            continue
        ns = re.sub(r'^[①②③④⑤⑥]', '', s.replace(' ', ''))
        if x > 400 or re.match(r'^(代號：|頁次：|說明：|本試題為|共\d+題|※|須用2B鉛筆|不必抄題)', ns) or re.fullmatch(r'[\d、\-]{5,}', s):
            continue                                # 頁首、說明文字
        if x < opt_x - 5:
            qm = re.match(r'^(\d{1,3})[.．]?\s*(.*)$', s)
            if group and qm and int(qm.group(1)) > group['to']:
                group = None
            # 讀題組短文時，行首剛好是空格編號（例如「26 speed」）不算題號：題號行必須帶選項或下一行就是選項
            if qm and group is not None and cur is None and int(qm.group(1)) == expect and not (
                    any(c in qm.group(2) for c in OPT_MARKS) or next_starts_opt(li) or not qm.group(2).strip()):
                qm = None
            if not qm and group is not None and (cur is None or (mcq and mcq[-1].get('passage') is not None and expect <= group['to'] and False)):
                group['text'] += ('' if not group['text'] or group['text'].endswith(('-', '—')) else ' ' if s[:1].isascii() and group['text'][-1:].isascii() else '') + s
                continue
            if qm and int(qm.group(1)) == expect:
                stem = qm.group(2).strip()
                cur = {'no': expect, 'q': stem, 'opts': []}
                if pua and any(c in stem for c in OPT_MARKS):     # 題號與選項同一行（克漏字）
                    i0 = min(stem.index(c) for c in OPT_MARKS if c in stem)
                    cur['q'] = stem[:i0].strip()
                    for piece in re.split('(?=[' + ''.join(OPT_MARKS) + '])', stem[i0:]):
                        if piece and piece[0] in OPT_MARKS:
                            cur['opts'].append(piece[1:].strip())
                    opt = len(cur['opts']) - 1 if cur['opts'] else None
                else:
                    opt = None
                if group and group['from'] <= expect <= group['to']:
                    cur['passage'] = group['text'].strip()
                    if not cur['q']:
                        cur['q'] = f'（題組第 {expect} 題：依上方短文作答）'
                mcq.append(cur); expect += 1
            else:
                cur = None                          # 「二、複選題」這類段落標題
            continue
        if cur is None:
            if group is not None:                  # 題組短文內容
                group['text'] += ('' if not group['text'] or group['text'].endswith(('-', '—')) else ' ' if s[:1].isascii() else '') + s
            continue
        if pua and s[0] in OPT_MARKS:               # 一行可能有好幾個選項
            for piece in re.split('(?=[' + ''.join(OPT_MARKS) + '])', s):
                if piece and piece[0] in OPT_MARKS:
                    cur['opts'].append(piece[1:].strip()); opt = len(cur['opts']) - 1
        elif not pua and OPT_RE.match(s) and OPT_RE.match(s).group(1) == 'ABCDE'[len(cur['opts'])]:
            for piece in re.split(r'\s+(?=[A-E][.．])', s):
                m = OPT_RE.match(piece)
                if m and m.group(1) == 'ABCDE'[len(cur['opts'])]:
                    cur['opts'].append(piece[m.end():].strip()); opt = len(cur['opts']) - 1
                elif opt is not None:
                    cur['opts'][opt] += piece
        elif opt is None:
            cur['q'] += ('\n' if s.startswith('「') or cur['q'].endswith('」') else '') + s
        else:
            cur['opts'][opt] += s
    for q in mcq:
        if re.search(r'附圖|下圖|如圖|右圖|左圖|上圖|圖中|附表|下表|如表', q['q'] + ''.join(q['opts'])) \
                or any(not o.strip() for o in q['opts']) or re.search('[\ue000-\uf8ff]', q['q'] + ''.join(q['opts'])):
            q['fig'] = True                         # 有圖、選項是圖片（空白），或有讀不出來的特殊字
    return mcq


GROUP_RE = re.compile(r'^請(?:依|閱讀)下(?:文|列短文|列文章)?.*?回答第\s*(\d+)\s*題(?:至|到|～|~|-)第?\s*(\d+)\s*題')


def parse_unnumbered(lines, opt_x, is_opt, pua, OPT_RE):
    """題號是圖片的試卷：題幹＝上一題選項之後、下一組選項之前的文字；支援題組（克漏字、閱讀測驗）。"""
    mcq, cur, opt, buf, group = [], None, None, [], None

    def add_opts(s):
        nonlocal opt
        if pua:
            for piece in re.split('(?=[' + ''.join(OPT_MARKS) + '])', s):
                if piece and piece[0] in OPT_MARKS:
                    cur['opts'].append(piece[1:].strip()); opt = len(cur['opts']) - 1
        else:
            for piece in re.split(r'\s+(?=[A-E][.．])', s):
                m = OPT_RE.match(piece)
                if m:
                    cur['opts'].append(piece[m.end():].strip()); opt = len(cur['opts']) - 1
                elif opt is not None:
                    cur['opts'][opt] += piece

    def join(ls):
        out = ''
        for _, t in ls:
            if out and (t.startswith('「') or out.endswith('」')):
                out += '\n' + t
            else:
                out += ('' if not out or out.endswith(('-', '—')) else ' ' if t[:1].isascii() and out[-1:].isascii() else '') + t
        return out

    for x, t in lines:
        s = t.strip().translate(str.maketrans(SUB_MARKS))
        ns = re.sub(r'^[①②③④⑤⑥]', '', s.replace(' ', ''))
        if x > 400 or re.match(r'^(代號：|頁次：|說明：|本試題為|共\d+題|※|須用2B鉛筆|不必抄題)', ns) or re.fullmatch(r'[\d、\-]{5,}', s):
            continue
        gm = GROUP_RE.match(ns)
        if gm:
            group = {'from': int(gm.group(1)), 'to': int(gm.group(2)), 'text': None}
            buf, cur, opt = [], None, None
            continue
        if is_opt(s):
            starting = cur is None or len(cur['opts']) >= 4 and not (cur['opts'] and s.lstrip()[:1] in ('\ue190', 'E'))
            if starting:
                no = len(mcq) + 1
                if group and no > group['to']:
                    group = None
                stem_ls, passage = buf, None
                if group and group['from'] <= no <= group['to']:
                    if group['text'] is None:                       # 這個題組的第一題：buf 裡是短文（＋題幹）
                        whole = join(buf)
                        if re.search(r'(?<!\d)' + str(group['from']) + r'(?!\d)', whole) or not buf:
                            group['text'], stem_ls = whole, []          # 克漏字：短文裡有空格編號，沒有題幹
                        else:                                         # 閱讀測驗：最後一段是題幹
                            k = max([i for i, (bx, _) in enumerate(buf) if bx >= opt_x - 2] or [0])
                            group['text'], stem_ls = join(buf[:k]), buf[k:]
                    passage = group['text']
                cur = {'no': no, 'q': join(stem_ls) if stem_ls else '', 'opts': []}
                if passage:
                    cur['passage'] = passage
                    if not cur['q']:
                        cur['q'] = f'（題組第 {no} 題：依上方短文作答）'
                mcq.append(cur); buf, opt = [], None
            add_opts(s)
            continue
        if cur is not None and opt is not None and len(cur['opts']) < 4 and x > opt_x + 2 and not buf:
            cur['opts'][opt] += s                                   # 選項換行
            continue
        if cur is not None and opt is not None and len(cur['opts']) >= 4 and x > opt_x + 2 and not buf:
            cur['opts'][opt] += s                                   # 最後一個選項換行
            continue
        buf.append((x, s))
    return mcq


def parse_answers(path):
    t = unicodedata.normalize('NFKC', pdftext(path, layout=True))   # 全形 Ｂ、＃ → B、#
    info = {}
    m = re.search(r'考試名稱：\s*(.+)', t)
    if m:
        info['exam'] = m.group(1).strip()
    m = re.search(r'試題代號[:：]\s*(\d+)', t)
    if m:
        info['code'] = m.group(1)
    head = t[:t.find('類科') if '類科' in t[:800] else 400]  # 考試名稱裡的年度（可能有兩個：「108年…及108年…」）
    info['years'] = sorted({int(y) for y in re.findall(r'(?<!\d)(\d{2,3})\s*年', head)})
    lines0 = t.split('\n')                                  # 科目名稱：排版時可能在標籤的上一行、同一行或跨好幾行
    for i, ln in enumerate(lines0):
        if '科目名稱' in ln:
            j0 = i - 1 if i and lines0[i - 1].strip() and not re.search(r'名稱[:：]', lines0[i - 1]) else i
            blk = []
            for ln2 in lines0[j0:i + 6]:
                if re.search(r'題數|標準答案|題號', ln2):
                    break
                blk.append(re.sub(r'科目名稱[:：]?', '', ln2))
            info['subject'] = ''.join(blk)
            break
    ans = {}
    lines = t.split('\n')
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st.startswith('題號') or st.startswith('題序'):
            nos = [int(x) for x in re.findall(r'第\s*(\d+)\s*題', st)] or [int(x) for x in re.findall(r'\b(\d{1,3})\b', st)]
            for j in range(i + 1, min(i + 3, len(lines))):
                if lines[j].strip().startswith('答案'):
                    toks = lines[j].strip()[2:].split()
                    for n, a in zip(nos, toks):
                        ans[n] = a
                    break
    # 「#」＝有更正，內容在備註，例如「第6題一律給分」「第12題答A或B者均給分」
    nm = list(re.finditer(r'備\s*註\s*[:：]', t))          # 取最後一個「備註：」，避開「詳見備註」
    note = re.sub(r'\s+', '', t[nm[-1].end():]) if nm else ''
    for n, a in list(ans.items()):
        if '#' not in a:
            continue
        m = re.search(r'第0*' + str(n) + r'題([^，；。]*)', note)
        clause = m.group(1) if m else ''
        letters = re.findall(r'[A-E]', clause)
        if '一律給分' in clause or '均給分' in clause and not letters:
            ans[n] = '送分'
        elif letters:
            ans[n] = '或'.join(dict.fromkeys(letters))
        else:
            ans[n] = '送分' if clause else '#'
    info['answers'] = ans
    info['ranges'] = []
    for kind, label in (('single', '單選'), ('multi', '複選')):
        m = re.search(label + r'題數[:：]\s*(\d+)題(?:\(第(\d+)~(\d+)題\))?', t)
        pm = re.search(label + r'每題配分[:：]\s*([\d.]+)', t)
        if m:
            lo, hi = (int(m.group(2)), int(m.group(3))) if m.group(2) else (1, int(m.group(1)))
            info['ranges'].append({'kind': kind, 'from': lo, 'to': hi, 'pts': float(pm.group(1)) if pm else None})
    return info


def build_law_matcher(index_path):
    idx = json.load(open(index_path, encoding='utf-8'))
    names = {l['n'] for l in idx['laws'] if not l['x']}
    # 簡稱：「憲法增修條文第10條」不能被當成「憲法第10條」，所以全部名稱一起由長到短排
    extra = {'憲法增修條文': '中華民國憲法增修條文', '增修條文': '中華民國憲法增修條文', '憲法': '中華民國憲法', '刑法': '中華民國刑法'}
    alt = '|'.join(re.escape(n) for n in sorted(names | set(extra), key=len, reverse=True))
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


def _norm_subject(s):
    return re.sub(r'[\s（）()\[\]【】、，,・·:：;；]', '', unicodedata.normalize('NFKC', s or ''))


def check_pair(paper, ans, expect_subject=None, expect_year=None):
    """確認答案檔和試題是同一份：年度要相同；再比試題代號，答案檔沒印代號時改比科目名稱。回傳問題說明或 None。"""
    yr, ay = expect_year or paper.get('year'), ans.get('years') or []
    if yr and ay and yr not in ay:
        return f'答案檔是 {"、".join(map(str, ay))} 年的，這份試題是 {yr} 年，可能配錯答案'
    ac, qc = ans.get('code'), set(paper.get('codes') or [])
    if ac and qc:
        return None if ac in qc else f'答案檔的試題代號 {ac} 跟試題上的代號（{"、".join(sorted(qc)[:6])}）對不上，可能配錯答案'
    want, got = _norm_subject(expect_subject), _norm_subject(ans.get('subject'))
    if want and got:
        return None if (want in got or (len(got) >= 4 and got in want)) else \
            f'答案檔的科目「{(ans.get("subject") or "").strip()[:30]}」跟這份試題不同，可能配錯答案'
    if ac or qc:                                   # 只有一邊有代號、又沒有科目名稱可比：無法確認，但也沒有矛盾
        return None
    return '答案檔沒有試題代號也沒有科目名稱，無法確認是同一份試題'


def build_paper(qpdf, apdf, find, expect_subject=None, expect_year=None):
    """解析一份試卷（apdf 可為 None：純申論）。回傳 (paper, problems)。problems 為空代表通過檢查。
    expect_subject：官方清單上的科目名稱，答案檔沒印試題代號時用來確認配對。"""
    paper = parse_questions(qpdf)
    ans = parse_answers(apdf) if apdf else {'answers': {}, 'ranges': []}
    problems = []
    if apdf and paper['mcq']:
        why = check_pair(paper, ans, expect_subject, expect_year)
        if why:
            problems.append(why)
    if ans.get('code'):                            # 以答案檔的試題代號為準（試卷上常有多個代號）
        paper['code'] = ans['code']
    paper['exam'] = ans.get('exam', paper.get('title', ''))
    for e in paper['essays']:
        e['refs'] = find(e['text'])
    for q in paper['mcq']:
        q['ans'] = ans['answers'].get(q['no'])
        rg = next((r for r in ans['ranges'] if r['from'] <= q['no'] <= r['to']), None)
        q['multi'] = bool(rg and rg['kind'] == 'multi') or bool(q['ans'] and re.fullmatch(r'[A-E]{2,5}', q['ans']))
        q['pts'] = rg['pts'] if rg else None
        q['refs'] = find(q['q'] + ' ' + ' '.join(q['opts']))
    def ans_fits(q):                               # 答案的字母不能超出選項數（例如只有 4 個選項卻答 E）
        return all('ABCDE'.index(c) < len(q['opts']) for c in re.findall(r'[A-E]', q['ans'] or ''))
    bad = [q['no'] for q in paper['mcq']
           if len(q['opts']) not in (4, 5) or (not q['multi'] and len(q['opts']) != 4) or not q['ans'] or q['ans'] == '#'
           or not ans_fits(q)]
    if bad and len(bad) <= max(1, len(paper['mcq']) // 10) and len(paper['mcq']) >= 5:
        for q in paper['mcq']:                     # 一成以內的題目有問題：標示後照樣上架
            if q['no'] in bad:
                q['broken'] = True
                need = 5 if q['multi'] else 4
                q['opts'] = (q['opts'] + ['（選項請看原始試卷）'] * need)[:max(need, len(q['opts']))]
        paper['broken'] = bad
    elif bad:
        problems.append(f'第 {bad[:10]} 題選項或答案不完整')
    # 題號要一一對上（不只是題數相同）：答案檔有第 1～N 題，試題也要剛好是第 1～N 題
    off = set(ans['answers']) ^ {q['no'] for q in paper['mcq']}
    if apdf and off and not (paper.get('broken') and len(off) <= len(paper['broken'])):
        problems.append(f'答案 {len(ans["answers"])} 題，試題解析出 {len(paper["mcq"])} 題'
                        + (f'（題號對不上：{sorted(off)[:8]}）' if len(ans['answers']) == len(paper['mcq']) else ''))
    paper.pop('codes', None)
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
