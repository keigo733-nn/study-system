#!/usr/bin/env python3
"""
把讀書系統打包成「單一 HTML 檔」：CSS、所有 JS 模組、法規資料、題庫全部內嵌。
點兩下就能開，離線可用（就像原本的 v9）。

用法：python3 build_single.py <app資料夾> <laws資料夾(含 index.json 與 law/)> <題庫資料夾(index.json＋p/)> <輸出.html> [--keep-abolished] [--bank-url=https://…/exams/]
"""
import base64, gzip, json, os, re, sys

ORDER = ['util', 'db', 'laws', 'editor', 'settings', 'notes', 'lawpage', 'exam', 'print', 'app']


def convert_module(name, src):
    exports = []
    def imp_ns(m): return f"const {m.group(1)} = __M[{json.dumps(m.group(2))}];"
    def imp_named(m):
        parts = []
        for p in m.group(1).split(','):
            p = p.strip()
            if not p: continue
            a = re.match(r'(\w+)\s+as\s+(\w+)', p)
            parts.append(f'{a.group(1)}: {a.group(2)}' if a else p)
        return f"const {{ {', '.join(parts)} }} = __M[{json.dumps(m.group(2))}];"
    src = re.sub(r"import\s+\*\s+as\s+(\w+)\s+from\s+'\./(\w+)\.js';", imp_ns, src)
    src = re.sub(r"import\s+\{([^}]*)\}\s+from\s+'\./(\w+)\.js';", imp_named, src)
    if re.search(r'^\s*import\s', src, re.M):
        raise SystemExit(f'{name}.js 有無法轉換的 import')

    def exp(m):
        kw, ident = m.group(1), m.group(2)
        exports.append(ident)
        return f'{kw} {ident}'
    src = re.sub(r'^export\s+((?:async\s+)?function\*?|const|let|class)\s+([\w$]+)', exp, src, flags=re.M)
    if re.search(r'^export\s', src, re.M):
        raise SystemExit(f'{name}.js 有無法轉換的 export')
    getters = ', '.join(f'get {e}() {{ return {e}; }}' for e in exports)
    return f"/* ===== {name}.js ===== */\n(() => {{\n{src}\n__M[{json.dumps(name)}] = {{ {getters} }};\n}})();\n"


def main(app, laws_dir, exams_dir, out, keep_abolished=False, bank_url=''):
    html = open(os.path.join(app, 'index.html'), encoding='utf-8').read()
    css = open(os.path.join(app, 'css', 'app.css'), encoding='utf-8').read()

    js = 'const __M = {};\n' + ''.join(
        convert_module(n, open(os.path.join(app, 'js', n + '.js'), encoding='utf-8').read()) for n in ORDER)
    js = js.replace('</script', '<\\/script')

    # 法規資料
    idx = json.load(open(os.path.join(laws_dir, 'index.json'), encoding='utf-8'))
    idx.pop('bundle', None)
    if not keep_abolished:
        idx['laws'] = [l for l in idx['laws'] if not l['x']]
        live = {l['p'] for l in idx['laws']}
        idx['alias'] = {k: v for k, v in idx['alias'].items() if v in live}
    laws = {}
    for l in idx['laws']:
        d = json.load(open(os.path.join(laws_dir, 'law', l['p'] + '.json'), encoding='utf-8'))
        for k in ('hist', 'pre', 'effNote', 'eff', 'url'):   # 介面用不到的欄位
            d.pop(k, None)
        laws[l['p']] = d
    blob = gzip.compress(json.dumps({'index': idx, 'laws': laws}, ensure_ascii=False, separators=(',', ':')).encode(), 9)
    b64 = base64.b64encode(blob).decode()

    # 內建範例題庫（exam_pipeline.py 的輸出格式：index.json ＋ p/<id>.json）
    ex_index = json.load(open(os.path.join(exams_dir, 'index.json'), encoding='utf-8'))
    papers = {r[0]: json.load(open(os.path.join(exams_dir, 'p', r[0] + '.json'), encoding='utf-8')) for r in ex_index['papers']}
    exjson = json.dumps({'index': ex_index, 'papers': papers}, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    bank = f'<script>window.BANK_URL = {json.dumps(bank_url)};</script>\n' if bank_url else ''

    html = html.replace('<link rel="stylesheet" href="css/app.css">', '<style>\n' + css + '\n</style>')
    html = html.replace('<script type="module" src="js/app.js"></script>',
                        f'<script id="lawdata" type="application/octet-stream">{b64}</script>\n'
                        f'<script id="examdata" type="application/json">{exjson}</script>\n' + bank +
                        f'<script>\n{js}\n</script>')
    assert 'src="js/' not in html and 'href="css/' not in html
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'完成：{out}（{os.path.getsize(out) / 1e6:.1f} MB，{len(laws)} 部法規，{len(papers)} 份試卷）')


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    bank = next((x.split('=', 1)[1] for x in sys.argv if x.startswith('--bank-url=')), '')
    main(*a[:4], keep_abolished='--keep-abolished' in sys.argv, bank_url=bank)
