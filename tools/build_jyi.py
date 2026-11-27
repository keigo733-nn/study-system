#!/usr/bin/env python3
"""
大法官解釋（釋字第 1～813 號）→ 白紙六法用的資料

資料來源：憲法法庭網站 https://cons.judicial.gov.tw/docdata.aspx?fid=100&id=<310181＋號次>
（司法院網站資料開放宣告：可無償重製、改作，使用時註明出處）

輸出（--out，預設 docs/jyi）：
  index.json      全部解釋的目錄：號次、公布日期、爭點、解釋文引用的法條、理由書另外引用的法條
  t/<號次>.json   單號全文：爭點、解釋文、理由書
  report.md       這次的結果；解析失敗的號次
  _debug/*.txt    幾份網頁轉成的純文字（第 1、603、813 號，以及前幾個失敗的），給維護的人檢查解析是否正確

已抓過、而且解析版本相同的號次不會重抓。

用法：python3 tools/build_jyi.py --laws laws/index.json --out docs/jyi [--sleep 1.5] [--only 603,748] [--force]
"""
import argparse, html, json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jyi_refs import JYI_MAX                     # noqa: E402

PARSER_VERSION = 1
URL = 'https://cons.judicial.gov.tw/docdata.aspx?fid=100&id={}'
ID_BASE = 310181                                  # 釋字第 n 號 → id = 310181 + n（第 156、264、328、469、601、631、656 號都對得上）
UA = 'Mozilla/5.0 (compatible; baizhi-liufa/1.0; +https://keigo733-nn.github.io/study-system/)'

LABELS = ['解釋字號', '解釋公布院令', '解釋爭點', '解釋文', '理由書']
# 理由書後面接的段落：遇到就停，避免把意見書、附件、網站頁尾抓進來
STOP = re.compile(r'^(意見書|協同意見書|不同意見書|部分協同.*|.*意見書.*|相關附件|附件.*|相關文件|.*聲請書.*|確定終局裁判|抄本.*|解釋摘要|事實摘要|新聞稿|英文.*|English.*|回列表|回頁首|:::|.*網站導覽.*|.*隱私權.*|.*資訊安全.*|.*版權所有.*|.*Copyright.*|司法院地址.*|.*瀏覽人次.*|分享.*|列印.*)$')
BLOCK = re.compile(r'<\s*(br|/p|/div|/li|/dt|/dd|/h[1-6]|/tr|/td|/th|/pre|/section|/article|/table|/ul|/ol)\b[^>]*>', re.I)


def page_lines(raw):
    raw = re.sub(r'(?is)<(script|style|noscript|head)\b.*?</\1>', ' ', raw)
    raw = BLOCK.sub('\n', raw)
    raw = re.sub(r'<[^>]+>', ' ', raw)
    out = []
    for ln in html.unescape(raw).split('\n'):
        ln = re.sub(r'[ \t　\xa0]+', ' ', ln).strip()
        if ln:
            out.append(ln)
    return out


def section(lines, start, stop_labels):
    """從 start（標題那一行）的下一行開始，收到下一個標題或停止條件為止"""
    buf = []
    for ln in lines[start + 1:]:
        if ln in stop_labels or STOP.match(ln):
            break
        if re.fullmatch(r'\d{1,2}', ln):          # 段落編號
            continue
        buf.append(ln)
    return buf


def parse(n, raw):
    lines = page_lines(raw)
    head = next((i for i, l in enumerate(lines) if re.search(rf'釋字第\s*{n}\s*號', l)), None)
    if head is None:
        raise ValueError(f'網頁裡找不到「釋字第{n}號」')
    pos = {}
    for lab in LABELS:
        i = next((j for j in range(head, len(lines)) if lines[j] == lab), None)
        if i is not None:
            pos[lab] = i
    if '解釋文' not in pos:
        raise ValueError('找不到「解釋文」段落')
    others = set(LABELS)
    date = ''
    m = re.search(r'中華民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日', ' '.join(lines[pos.get('解釋公布院令', head):pos['解釋文']]))
    if m:
        date = f'{int(m.group(1)):03d}{int(m.group(2)):02d}{int(m.group(3)):02d}'
    issue = ' '.join(section(lines, pos['解釋爭點'], others)) if '解釋爭點' in pos else ''
    text = '\n'.join(section(lines, pos['解釋文'], others))
    reason = '\n'.join(section(lines, pos['理由書'], others)) if '理由書' in pos else ''
    issue = re.sub(r'^[-–•・\s]+', '', issue).strip()
    if len(text) < 15:
        raise ValueError(f'解釋文太短（{len(text)} 字），可能沒抓對')
    return {'no': n, 'date': date, 'issue': issue, 'text': text, 'reason': reason}, lines


def fetch(url, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'zh-TW,zh;q=0.9'})
            with urllib.request.urlopen(req, timeout=40) as r:
                b = r.read()
            return b.decode('utf-8', 'replace')
        except Exception as e:                    # noqa: BLE001
            last = e; time.sleep(3 * (k + 1))
    raise last


def ref_keys(refs):
    return [f"{r['law']}|{r['art']}" for r in refs if r.get('art')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--laws', default='laws/index.json')
    ap.add_argument('--out', default='docs/jyi')
    ap.add_argument('--sleep', type=float, default=1.5)
    ap.add_argument('--only', default='')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--minutes', type=float, default=80)
    a = ap.parse_args()

    from parse_exam import build_law_matcher
    find = build_law_matcher(a.laws)
    os.makedirs(os.path.join(a.out, 't'), exist_ok=True)
    dbg = os.path.join(a.out, '_debug'); os.makedirs(dbg, exist_ok=True)
    nums = [int(x) for x in a.only.split(',') if x.strip()] or list(range(1, JYI_MAX + 1))
    t0, fetched, failed, ndbg = time.time(), 0, {}, 0

    for n in nums:
        f = os.path.join(a.out, 't', f'{n}.json')
        if not a.force and os.path.exists(f):
            try:
                if json.load(open(f, encoding='utf-8')).get('pv') == PARSER_VERSION:
                    continue
            except ValueError:
                pass
        if (time.time() - t0) / 60 > a.minutes:
            print('時間到，剩下的下次再抓'); break
        url = URL.format(ID_BASE + n)
        raw = None
        try:
            raw = fetch(url)
            d, lines = parse(n, raw)
            d['url'] = url; d['pv'] = PARSER_VERSION
            json.dump(d, open(f, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
            fetched += 1
            if n in (1, 603, JYI_MAX):
                open(os.path.join(dbg, f'{n}.txt'), 'w', encoding='utf-8').write('\n'.join(lines[:600]))
            if fetched % 50 == 0:
                print(f'已抓 {fetched} 號（目前第 {n} 號）', flush=True)
        except Exception as e:                    # noqa: BLE001
            failed[n] = str(e)
            print(f'✗ 釋字第 {n} 號：{e}', flush=True)
            if ndbg < 5 and raw:
                open(os.path.join(dbg, f'fail_{n}.txt'), 'w', encoding='utf-8').write('\n'.join(page_lines(raw)[:600]))
                ndbg += 1
            if len(failed) >= 20 and not fetched:
                print('連續失敗 20 號、一號都沒成功：可能網站擋住或改版，先停下來'); break
        time.sleep(a.sleep)

    # 目錄：從 t/ 重建（含之前抓好的）
    items = []
    for n in range(1, JYI_MAX + 1):
        f = os.path.join(a.out, 't', f'{n}.json')
        if not os.path.exists(f):
            continue
        d = json.load(open(f, encoding='utf-8'))
        r1 = ref_keys(find(d['text']))
        r2 = [k for k in ref_keys(find(d.get('reason', ''))) if k not in r1]
        items.append({'no': n, 'd': d['date'], 'i': d['issue'], 'r': r1, 'r2': r2})
    json.dump({'updated': time.strftime('%Y-%m-%d %H:%M'), 'source': '憲法法庭網站（司法院）', 'count': len(items), 'items': items},
              open(os.path.join(a.out, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    miss = [n for n in range(1, JYI_MAX + 1) if not os.path.exists(os.path.join(a.out, 't', f'{n}.json'))]
    rep = [f'# 大法官解釋資料（{time.strftime("%Y-%m-%d %H:%M")}）', '',
           f'- 這次新抓：{fetched} 號', f'- 已有資料：{len(items)} / {JYI_MAX} 號',
           f'- 沒有爭點的：{sum(1 for x in items if not x["i"])} 號',
           f'- 解釋文有引用法條的：{sum(1 for x in items if x["r"])} 號', '']
    if failed:
        rep += ['## 這次失敗', ''] + [f'- 第 {n} 號：{e}' for n, e in failed.items()] + ['']
    if miss:
        rep += [f'## 還沒有資料：{len(miss)} 號', '', '、'.join(map(str, miss[:200])), '']
    open(os.path.join(a.out, 'report.md'), 'w', encoding='utf-8').write('\n'.join(rep))
    print('\n'.join(rep))


if __name__ == '__main__':
    main()
