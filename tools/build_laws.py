#!/usr/bin/env python3
"""
把全國法規資料庫開放資料（FalV.xml，中文法規_法律資料檔）轉成網站用的小檔案：

  out/index.json        法規目錄：pcode、名稱、簡稱、最新異動日期、廢止註記、條數
  out/law/<pcode>.json  每部法規一個檔：編章節、條文（條號、各項內容）

用法：python3 build_laws.py <FalV.xml | 法務部 API 下載的 zip | ChLaw.json> out [--bundle] [--min=1000]
  支援兩種格式：政府資料開放平台的舊版 XML（中文標籤），以及全國法規資料庫 API
  （https://law.moj.gov.tw/api/ch/law/json 或 /xml，回傳 zip，內含英文欄位的 JSON/XML）。
資料來源：全國法規資料庫（https://law.moj.gov.tw/），政府資料開放授權條款-第1版
"""
import json, os, re, sys
import xml.etree.ElementTree as ET

# 常用簡稱 → 法規全名。只在全名確實存在於資料中時才會寫入目錄。
ALIASES = {
    '民': '民法', '民法': '民法',
    '刑': '中華民國刑法', '刑法': '中華民國刑法',
    '憲': '中華民國憲法', '憲法': '中華民國憲法',
    '增修': '中華民國憲法增修條文', '憲增': '中華民國憲法增修條文', '增修條文': '中華民國憲法增修條文',
    '民訴': '民事訴訟法', '刑訴': '刑事訴訟法',
    '行程': '行政程序法', '行訴': '行政訴訟法', '行罰': '行政罰法', '行執': '行政執行法',
    '國賠': '國家賠償法', '訴願': '訴願法',
    '公司': '公司法', '票據': '票據法', '票': '票據法', '保險': '保險法', '海商': '海商法',
    '證交': '證券交易法', '強執': '強制執行法', '家事': '家事事件法', '非訟': '非訟事件法',
    '憲訴': '憲法訴訟法', '地制': '地方制度法', '中標': '中央法規標準法',
    '勞基': '勞動基準法', '勞事': '勞動事件法', '消保': '消費者保護法', '公平': '公平交易法',
    '個資': '個人資料保護法', '採購': '政府採購法', '政採': '政府採購法',
    '涉外': '涉外民事法律適用法', '涉民': '涉外民事法律適用法',
    '公寓': '公寓大廈管理條例', '消債': '消費者債務清理條例',
    '少事': '少年事件處理法', '洗防': '洗錢防制法', '毒品': '毒品危害防制條例', '毒危': '毒品危害防制條例',
    '貪污': '貪污治罪條例', '貪': '貪污治罪條例', '槍砲': '槍砲彈藥刀械管制條例',
    '社維': '社會秩序維護法', '道交': '道路交通管理處罰條例', '道交條例': '道路交通管理處罰條例',
    '公服': '公務員服務法', '公懲': '公務員懲戒法', '保障': '公務人員保障法', '公保障': '公務人員保障法',
    '任用': '公務人員任用法', '考績': '公務人員考績法', '中立': '公務人員行政中立法',
    '政資': '政府資訊公開法', '法組': '法院組織法', '通保': '通訊保障及監察法',
    '速審': '刑事妥速審判法', '刑補': '刑事補償法', '民總施': '民法總則施行法',
    '智審': '智慧財產案件審理法', '企併': '企業併購法', '動擔': '動產擔保交易法',
    '著作': '著作權法', '著': '著作權法', '專利': '專利法', '商標': '商標法', '營秘': '營業秘密法',
    '土地': '土地法', '土': '土地法', '都計': '都市計畫法', '國籍': '國籍法',
    '所得稅': '所得稅法', '稅稽': '稅捐稽徵法', '信託': '信託法', '仲裁': '仲裁法',
    '監刑': '監獄行刑法', '羈押': '羈押法', '律師': '律師法', '公證': '公證法', '提存': '提存法',
    '選罷': '公職人員選舉罷免法', '集遊': '集會遊行法', '兒少': '兒童及少年福利與權益保障法',
    '就服': '就業服務法', '性工': '性別平等工作法', '勞保': '勞工保險條例',
}

ART_RE = re.compile(r'^(?:第\s*)?(\d+)(?:\s*-\s*(\d+))?(?:\s*條)?$')


def txt(el, tag):
    v = el.findtext(tag)
    return (v or '').strip()


def pcode_of(url):
    m = re.search(r'pcode=([A-Za-z0-9]+)', url or '')
    return m.group(1) if m else None


def parse_law(law):
    name = txt(law, '法規名稱')
    url = txt(law, '法規網址')
    pcode = pcode_of(url)
    content = law.find('法規內容')
    chapters, articles = [], []
    cur_ch = -1
    if content is not None:
        for el in content:
            if el.tag == '編章節':
                raw = (el.text or '').rstrip()
                if not raw.strip():
                    continue
                indent = len(raw) - len(raw.lstrip(' '))
                chapters.append({'t': re.sub(r'\s+', ' ', raw.strip()), 'lv': indent // 3})
                cur_ch = len(chapters) - 1
            elif el.tag == '條文':
                no_raw = txt(el, '條號')
                body = (el.findtext('條文內容') or '').strip('\n')
                m = ART_RE.match(no_raw)
                if m:
                    key = m.group(1) + ('-' + m.group(2) if m.group(2) else '')
                else:
                    key = re.sub(r'\s+', '', no_raw)
                art = {'no': key, 'lines': body.split('\n') if body else []}
                if cur_ch >= 0:
                    art['c'] = cur_ch
                articles.append(art)
    meta = {
        'p': pcode, 'n': name,
        'kind': txt(law, '法規性質'), 'cat': txt(law, '法規類別'),
        'd': txt(law, '最新異動日期'), 'eff': txt(law, '生效日期'),
        'x': txt(law, '廢止註記'),
        'url': url,
    }
    full = dict(meta)
    full.update({
        'hist': (law.findtext('沿革內容') or '').strip(),
        'pre': (law.findtext('前言') or '').strip(),
        'effNote': (law.findtext('生效內容') or '').strip(),
        'ch': chapters, 'arts': articles,
    })
    return meta, full


# 新版 API 的英文欄位 → 舊版中文標籤
EN2ZH = {'LawLevel': '法規性質', 'LawName': '法規名稱', 'LawURL': '法規網址', 'LawCategory': '法規類別',
         'LawModifiedDate': '最新異動日期', 'LawEffectiveDate': '生效日期', 'LawEffectiveNote': '生效內容',
         'LawAbandonNote': '廢止註記', 'LawHistories': '沿革內容', 'LawForeword': '前言'}
CH_RANK = {'編': 0, '章': 1, '節': 2, '款': 3, '目': 4}


def law_from_dict(d):
    """新版 JSON 的一部法規 → 跟舊版 XML 一樣的 <法規> 元素"""
    law = ET.Element('法規')
    for en, zh in EN2ZH.items():
        ET.SubElement(law, zh).text = str(d.get(en) or '')
    content = ET.SubElement(law, '法規內容')
    arts = d.get('LawArticles') or []
    # 編章節的層級：有前置空白就照空白，沒有就照「編、章、節、款、目」
    chs = [a for a in arts if (a.get('ArticleType') or '').upper() == 'C']
    use_kw = chs and not any((a.get('ArticleContent') or '').startswith(' ') for a in chs)
    for a in arts:
        typ = (a.get('ArticleType') or '').upper()
        if typ == 'C':
            t = a.get('ArticleContent') or ''
            if use_kw:
                m = re.match(r'\s*第\s*\S+?\s*([編章節款目])', t)
                lv = CH_RANK.get(m.group(1), 1) if m else 1     # 跟舊版 XML 一樣用絕對層級（章＝1）
                t = ' ' * (3 * lv) + t.strip()
            ET.SubElement(content, '編章節').text = t
        else:
            el = ET.SubElement(content, '條文')
            ET.SubElement(el, '條號').text = a.get('ArticleNo') or ''
            ET.SubElement(el, '條文內容').text = a.get('ArticleContent') or ''
    return law


def load_root(src):
    """回傳 (UpdateDate, [<法規> 元素])"""
    import io, zipfile
    data, name = open(src, 'rb').read(), src.lower()
    if data[:2] == b'PK':                                   # zip：找裡面的 json 或 xml
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in z.namelist() if n.lower().endswith(('.json', '.xml')) and 'manifest' not in n.lower() and 'schema' not in n.lower()]
        names.sort(key=lambda n: (not n.lower().endswith('.json'), -z.getinfo(n).file_size))
        if not names:
            raise SystemExit('zip 裡找不到法規資料檔：' + ', '.join(z.namelist()))
        data, name = z.read(names[0]), names[0].lower()
        print('使用 zip 內的', names[0], file=sys.stderr)
    data = data.lstrip(b'\xef\xbb\xbf').lstrip()
    if data[:1] in (b'{', b'['):
        j = json.loads(data.decode('utf-8-sig'))
        laws = j.get('Laws', j) if isinstance(j, dict) else j
        return (j.get('UpdateDate', '') if isinstance(j, dict) else ''), [law_from_dict(d) for d in laws]
    root = ET.fromstring(data)
    if root.findall('法規'):
        return root.get('UpdateDate', ''), root.findall('法規')
    laws = [l for l in root.iter() if l.find('LawName') is not None]   # 新版 XML（英文標籤）
    out = []
    for l in laws:
        d = {c.tag: (c.text or '') for c in l if c.tag != 'LawArticles'}
        arts = l.find('LawArticles')
        d['LawArticles'] = [{c.tag: (c.text or '') for c in a} for a in (arts if arts is not None else [])]
        out.append(law_from_dict(d))
    return root.get('UpdateDate', '') or (root.findtext('UpdateDate') or ''), out


def main(src, out):
    updated, laws = load_root(src)
    minimum = int(next((a.split('=')[1] for a in sys.argv if a.startswith('--min=')), '0'))
    if len(laws) < minimum:
        raise SystemExit(f'只讀到 {len(laws)} 部法規（少於 {minimum}），資料可能不完整，這次不更新。')
    os.makedirs(os.path.join(out, 'law'), exist_ok=True)
    index, names = [], {}
    for law in laws:
        meta, full = parse_law(law)
        if not meta['p']:
            print('略過（無 pcode）：', meta['n'], file=sys.stderr)
            continue
        with open(os.path.join(out, 'law', meta['p'] + '.json'), 'w', encoding='utf-8') as f:
            json.dump(full, f, ensure_ascii=False, separators=(',', ':'))
        entry = {k: meta[k] for k in ('p', 'n', 'kind', 'cat', 'd', 'x')}
        entry['cnt'] = len(full['arts'])
        index.append(entry)
        names[meta['n']] = entry
    alias_map = {}
    for short, fullname in ALIASES.items():
        if fullname in names:
            alias_map[short] = names[fullname]['p']
        else:
            print('簡稱對不到法規，已略過：', short, '→', fullname, file=sys.stderr)
    data = {
        'updated': updated,
        'source': '全國法規資料庫（https://law.moj.gov.tw/）',
        'laws': index,
        'alias': alias_map,
    }
    # --bundle：另外把同一 pcode 前三碼的法規合成一檔（檔案數少，適合不能放上千個檔案的空間）
    if '--bundle' in sys.argv:
        bdir = os.path.join(out, 'bundle'); os.makedirs(bdir, exist_ok=True)
        groups = {}
        for e in index:
            groups.setdefault(e['p'][:3], []).append(e['p'])
        for pre, ps in groups.items():
            pack = {}
            for p in ps:
                with open(os.path.join(out, 'law', p + '.json'), encoding='utf-8') as f:
                    pack[p] = json.load(f)
            with open(os.path.join(bdir, pre + '.json'), 'w', encoding='utf-8') as f:
                json.dump(pack, f, ensure_ascii=False, separators=(',', ':'))
        data['bundle'] = 3
    with open(os.path.join(out, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print(f'完成：{len(index)} 部法規，{sum(e["cnt"] for e in index)} 條，簡稱 {len(alias_map)} 個')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    main(args[0] if args else 'FalV.xml', args[1] if len(args) > 1 else 'out')
