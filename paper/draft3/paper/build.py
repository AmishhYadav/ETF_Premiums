#!/usr/bin/env python3
"""Build the IEEE two-column Word file of the paper from paper.md, refs.bib (+ refs_new.bib) and ../figures.
Math is converted LaTeX -> OMML by pandoc in one batch; everything else is written with python-docx."""
import re, sys, os, subprocess, tempfile, zipfile, copy, json
from docx import Document
from docx.shared import Pt, Inches, Emu, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement, parse_xml
from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__)); FIGDIR = os.path.join(HERE, '..', 'figures')
FONT = 'Times New Roman'; COLW = 3.5; GAP = 0.25; FULLW = 7.25
M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII', 'XIII', 'XIV', 'XV', 'XVI', 'XVII', 'XVIII', 'XIX', 'XX']

# ------------------------------------------------------------------ bibliography
def parse_bib(path):
    txt = open(path, encoding='utf8').read(); out = {}
    i = 0
    while True:
        m = re.search(r'@(\w+)\{([^,]+),', txt[i:])
        if not m: break
        typ, key = m.group(1).lower(), m.group(2).strip(); j = i + m.end(); depth = 1; k = j
        while depth and k < len(txt):
            if txt[k] == '{': depth += 1
            elif txt[k] == '}': depth -= 1
            k += 1
        body = txt[j:k - 1]; i = k; f = {'type': typ}
        for fm in re.finditer(r'(\w+)\s*=\s*', body):
            name = fm.group(1).lower(); p = fm.end()
            if p >= len(body): continue
            if body[p] == '{':
                d = 1; q = p + 1
                while d and q < len(body):
                    if body[q] == '{': d += 1
                    elif body[q] == '}': d -= 1
                    q += 1
                val = body[p + 1:q - 1]
            else:
                q = body.find(',', p); q = len(body) if q < 0 else q; val = body[p:q].strip().strip('"')
            if name not in f: f[name] = val
        out[key] = f
    return out

def detex(s):
    s = s.replace('\\&', '&').replace('---', '\u2014').replace('--', '\u2013').replace("``", '\u201c').replace("''", '\u201d').replace('`', '\u2018').replace('\\_', '_').replace('~', ' ')
    acc = {"\\'e": 'é', "\\'c": 'ć', '\\"u': 'ü', '\\"o': 'ö', "\\'a": 'á', "\\'o": 'ó', '\\v{c}': 'č'}
    for a, b in acc.items(): s = s.replace('{' + a + '}', b).replace(a, b)
    s = re.sub(r'\\url\{([^}]*)\}', r'\1', s); s = re.sub(r'\\emph\{([^}]*)\}', r'*\1*', s); s = re.sub(r'\\texttt\{([^}]*)\}', r'\1', s)
    s = s.replace('{', '').replace('}', '').replace('\\$', '$').replace('\\%', '%')
    return re.sub(r'\s+', ' ', s).strip()

def authors(a):
    names = [n.strip() for n in a.split(' and ')]; out = []
    for n in names:
        n = detex(n)
        if ',' in n:
            last, first = [x.strip() for x in n.split(',', 1)]
            parts = []
            for w in first.split():
                if '-' in w: parts.append('-'.join(x[0] + '.' for x in w.split('-') if x))
                elif w.endswith('.') and len(w) <= 3: parts.append(w)
                else: parts.append(w[0] + '.')
            ini = ' '.join(parts)
            out.append(f'{ini} {last}')
        else: out.append(n)
    if len(out) == 1: return out[0]
    if len(out) == 2: return f'{out[0]} and {out[1]}'
    return ', '.join(out[:-1]) + ', and ' + out[-1]

def fmt_ref(f):
    t = f['type']; g = lambda k: detex(f[k]) if k in f else ''
    au = authors(f['author']) if 'author' in f else g('institution') or g('organization')
    ti = g('title'); yr = g('year')
    if t == 'article':
        s = f'{au}, \u201c{ti},\u201d *{g("journal")}*'
        if g('volume'): s += f', vol. {g("volume")}'
        if g('number'): s += f', no. {g("number")}'
        if g('pages'): s += f', pp. {g("pages")}'
        s += f', {yr}.'
    elif t == 'incollection':
        s = f'{au}, \u201c{ti},\u201d in *{g("booktitle")}*'
        if g('editor'): s += f', {authors(f["editor"])}, Ed{"s" if " and " in f["editor"] else ""}.'
        s += f' {g("address")}: {g("publisher")}, {yr}' + (f', pp. {g("pages")}.' if g('pages') else '.')
    elif t == 'book': s = f'{au}, *{ti}*. {g("address")}: {g("publisher")}, {yr}.'
    elif t == 'phdthesis': s = f'{au}, \u201c{ti},\u201d Ph.D. dissertation, {g("school")}, {yr}.'
    elif t == 'techreport':
        s = f'{au}, \u201c{ti},\u201d {g("institution")}, {g("address") + ", " if g("address") else ""}{g("type") or "Tech. Rep."} {g("number")}, {yr}.'.replace(' ,', ',')
    else:
        how = g('howpublished') or g('note')
        s = f'{au}, \u201c{ti},\u201d' if au else f'\u201c{ti},\u201d'
        if how: s += f' {how},'
        s += f' {yr}.'
        if g('note') and g('howpublished'): s = s[:-1] + f'. {g("note")}.'
    if g('url') and t != 'article': s += f' [Online]. Available: {g("url")}'
    return s.replace(',,', ',').replace('..', '.').replace('.,', ',')

# ------------------------------------------------------------------ source parsing
class Ctx:
    def __init__(self):
        self.cites = []; self.labels = {}; self.math = []; self.h1 = 0; self.h2 = 0; self.app = 0; self.eq = 0; self.fig = 0; self.tab = 0; self.inapp = False

def read_blocks(src):
    """Split markdown-like source into blocks."""
    lines = src.split('\n'); blocks = []; i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip(): i += 1; continue
        if ln.startswith('%%'): i += 1; continue
        if ln.startswith('# ') or ln.startswith('## ') or ln.startswith('### '):
            lvl = len(ln) - len(ln.lstrip('#')); t = ln[lvl:].strip(); lab = None
            m = re.search(r'\{#([^}]+)\}\s*$', t)
            if m: lab = m.group(1); t = t[:m.start()].strip()
            blocks.append(dict(k='h', lvl=lvl, text=t, label=lab)); i += 1; continue
        if ln.startswith('$$'):
            buf = [ln[2:]]; 
            while not buf[-1].rstrip().endswith('$$') and not re.search(r'\$\$\s*\{[^}]*\}\s*$', buf[-1]):
                i += 1; buf.append(lines[i])
            t = '\n'.join(buf); lab = None; m = re.search(r'\$\$\s*(\{([^}]*)\})?\s*$', t); lab = m.group(2); t = t[:m.start()]
            blocks.append(dict(k='eq', tex=t.strip(), label=lab)); i += 1; continue
        if ln.startswith('!FIG['):
            a = ln[5:ln.rindex(']')].split('|', 3); blocks.append(dict(k='fig', file=a[0], wide=a[1] == 'w2', label=a[2], cap=a[3])); i += 1; continue
        if ln.startswith('!TABLE['):
            a = ln[7:ln.rindex(']')].split('|', 4); rows = []; note = None; i += 1
            while i < len(lines) and lines[i].startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', c) for c in cells): rows.append(cells)
                i += 1
            if i < len(lines) and lines[i].startswith('!NOTE '): note = lines[i][6:]; i += 1
            blocks.append(dict(k='tab', label=a[0], wide=a[1] == 'w2', title=a[2], widths=[float(x) for x in a[3].split(',')], opts=a[4] if len(a) > 4 else '', rows=rows, note=note)); continue
        if ln.startswith('!'):
            m = re.match(r'!(\w+)\s*(.*)', ln); blocks.append(dict(k='cmd', name=m.group(1), arg=m.group(2))); i += 1; continue
        if re.match(r'\d+\)\s', ln) or ln.startswith('- '):
            blocks.append(dict(k='li', text=ln)); i += 1; continue
        buf = [ln]; i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'(#|\$\$|!|\d+\)\s|- )', lines[i]): buf.append(lines[i]); i += 1
        blocks.append(dict(k='p', text=' '.join(buf)))
    return blocks

TOKEN = re.compile(r'(\$[^$]+\$|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[@[^\]]+\]|\{(?:fig|tab|eq|sec|app):[^}]+\})')

def number_pass(blocks, C, bib):
    """Assign numbers to sections, equations, figures and tables; collect citations in order of appearance."""
    def scan(text):
        for m in re.finditer(r'\[@([^\]]+)\]', text or ''):
            for k in [x.strip().lstrip('@') for x in m.group(1).split(';')]:
                if k not in bib: raise SystemExit(f'unknown citation {k}')
                if k not in C.cites: C.cites.append(k)
    for b in blocks:
        if b['k'] == 'h':
            if b['text'].lower().startswith('appendix'): C.inapp = True
            if b.get('label') == 'nonum' or b['text'] in ('References', 'Acknowledgment and Disclosure'): b['num'] = None
            elif b['lvl'] == 1:
                if C.inapp: C.app += 1; b['num'] = None; ref = 'Appendix ' + chr(64 + C.app)
                else: C.h1 += 1; C.h2 = 0; b['num'] = ROMAN[C.h1 - 1]; ref = 'Section ' + b['num']
                if b['label']: C.labels[b['label']] = ref
            elif b['lvl'] == 2:
                C.h2 += 1; b['num'] = chr(64 + C.h2)
                if b['label']: C.labels[b['label']] = ('Appendix ' + chr(64 + C.app) if C.inapp else f'Section {ROMAN[C.h1 - 1]}-{b["num"]}')
        elif b['k'] == 'eq':
            C.eq += 1; b['num'] = C.eq
            if b['label']: C.labels['eq:' + b['label']] = f'({C.eq})'
        elif b['k'] == 'fig': C.fig += 1; b['num'] = C.fig; C.labels['fig:' + b['label']] = f'Fig. {C.fig}'
        elif b['k'] == 'tab': C.tab += 1; b['num'] = C.tab; C.labels['tab:' + b['label']] = f'Table {ROMAN[C.tab - 1]}'
    for b in blocks:   # citations in reading order
        if b['k'] in ('p', 'li'): scan(b['text'])
        elif b['k'] == 'fig': scan(b['cap'])
        elif b['k'] == 'tab':
            scan(b['title']); [scan(c) for r in b['rows'] for c in r]; scan(b['note'])
        elif b['k'] == 'cmd': scan(b['arg'])

def collect_math(blocks, C):
    def add(tex, display):
        key = (tex, display)
        if key not in C.mathidx: C.mathidx[key] = len(C.math); C.math.append(key)
    C.mathidx = {}
    def scan(text):
        for m in re.finditer(r'\$([^$]+)\$', text or ''): add(m.group(1), False)
    for b in blocks:
        if b['k'] in ('p', 'li'): scan(b['text'])
        elif b['k'] == 'h': scan(b['text'])
        elif b['k'] == 'eq': add(b['tex'], True)
        elif b['k'] == 'fig': scan(b['cap'])
        elif b['k'] == 'tab': scan(b['title']); [scan(c) for r in b['rows'] for c in r]; scan(b['note'])
        elif b['k'] == 'cmd': scan(b['arg'])

def convert_math(C):
    """One pandoc run: each formula is its own paragraph; pull the m:oMath elements back in order."""
    md = '\n\n'.join(('$$' + t + '$$') if disp else ('M$' + t + '$') for t, disp in C.math)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'm.md'); o = os.path.join(td, 'm.docx'); open(p, 'w', encoding='utf8').write(md)
        r = subprocess.run(['pandoc', p, '-f', 'markdown', '-o', o], capture_output=True, text=True)
        if r.returncode: raise SystemExit(r.stderr)
        if r.stderr.strip(): print('pandoc:', r.stderr.strip()[:2000])
        xml = zipfile.ZipFile(o).read('word/document.xml')
    root = etree.fromstring(xml); body = root.find(f'{{{W_NS}}}body'); out = []
    for p in body.findall(f'{{{W_NS}}}p'):
        ms = p.findall(f'.//{{{M_NS}}}oMath')
        if ms: out.append(ms[0])
    if len(out) != len(C.math): raise SystemExit(f'math count mismatch {len(out)} vs {len(C.math)}')
    C.omml = out

# ------------------------------------------------------------------ docx helpers
def set_font(run, size, bold=None, italic=None, smallcaps=None, font=FONT):
    run.font.name = font; run.font.size = Pt(size)
    rpr = run._r.get_or_add_rPr(); rf = rpr.find(qn('w:rFonts'))
    if rf is None: rf = OxmlElement('w:rFonts'); rpr.insert(0, rf)
    for a in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'): rf.set(qn(a), font)
    if bold is not None: run.bold = bold
    if italic is not None: run.italic = italic
    if smallcaps: run.font.small_caps = True

def pfmt(p, align=None, before=0, after=0, first=None, left=None, hanging=None, keep_next=False, line=None, keep_lines=False, widow=True):
    f = p.paragraph_format
    if align is not None: p.alignment = align
    f.space_before = Pt(before); f.space_after = Pt(after)
    if first is not None: f.first_line_indent = Inches(first)
    if left is not None: f.left_indent = Inches(left)
    if hanging is not None: f.first_line_indent = Inches(-hanging)
    if keep_next: f.keep_with_next = True
    if keep_lines: f.keep_together = True
    if line: f.line_spacing = line
    f.widow_control = widow

def cite_str(keys, C):
    nums = sorted(C.cites.index(k) + 1 for k in keys); parts = []; i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1: j += 1
        parts.append(f'[{nums[i]}]' if j == i else (f'[{nums[i]}], [{nums[j]}]' if j == i + 1 else f'[{nums[i]}]\u2013[{nums[j]}]')); i = j + 1
    return ', '.join(parts)

def add_inline(p, text, C, size=10, bold=None, italic=None, smallcaps=None):
    """Write text with *italic*, **bold**, `code`, $math$, [@cite] and {ref} tokens into paragraph p."""
    pos = 0
    def put(t, **kw):
        if not t: return
        t = t.replace('---', '\u2014').replace('--', '\u2013').replace("\\$", '$').replace('~', '\u00a0')
        r = p.add_run(t); set_font(r, size, kw.get('bold', bold), kw.get('italic', italic), smallcaps)
        if kw.get('mono'): set_font(r, size - 1, font='Courier New')
    for m in TOKEN.finditer(text):
        put(text[pos:m.start()]); tok = m.group(0); pos = m.end()
        if tok.startswith('$'):
            el = copy.deepcopy(C.omml[C.mathidx[(tok[1:-1], False)]])
            for r in el.iter(f'{{{M_NS}}}r'):   # font size for math runs
                rpr = r.find(f'{{{W_NS}}}rPr')
                if rpr is None:
                    rpr = etree.SubElement(r, f'{{{W_NS}}}rPr'); r.remove(rpr)
                    idx = 1 if r.find(f'{{{M_NS}}}rPr') is not None else 0; r.insert(idx, rpr)
                    rf = etree.SubElement(rpr, f'{{{W_NS}}}rFonts'); rf.set(f'{{{W_NS}}}ascii', 'Cambria Math'); rf.set(f'{{{W_NS}}}hAnsi', 'Cambria Math')
                sz = etree.SubElement(rpr, f'{{{W_NS}}}sz'); sz.set(f'{{{W_NS}}}val', str(int(size * 2)))
            p._p.append(el)
        elif tok.startswith('**'): put(tok[2:-2], bold=True)
        elif tok.startswith('*'): put(tok[1:-1], italic=True)
        elif tok.startswith('`'): put(tok[1:-1], mono=True)
        elif tok.startswith('[@'): put(cite_str([k.strip().lstrip('@') for k in tok[2:-1].split(';')], C))
        else:
            key = tok[1:-1]; kind, name = key.split(':', 1)
            lab = C.labels.get(key)
            if lab is None: raise SystemExit(f'unknown label {key}')
            put(lab)
    put(text[pos:])

def sect_xml(cols, first=False):
    s = OxmlElement('w:sectPr')
    if not first:
        t = OxmlElement('w:type'); t.set(qn('w:val'), 'continuous'); s.append(t)
    pg = OxmlElement('w:pgSz'); pg.set(qn('w:w'), '12240'); pg.set(qn('w:h'), '15840'); s.append(pg)
    mar = OxmlElement('w:pgMar')
    for k, v in dict(top=1080, right=900, bottom=1300, left=900, header=540, footer=600, gutter=0).items(): mar.set(qn('w:' + k), str(v))
    s.append(mar)
    c = OxmlElement('w:cols'); c.set(qn('w:num'), str(cols)); c.set(qn('w:space'), str(int(GAP * 1440))); c.set(qn('w:equalWidth'), '1'); s.append(c)
    return s

class Builder:
    def __init__(self, C, bib, meta):
        self.C = C; self.bib = bib; self.meta = meta; self.doc = Document(); self.cols = 1; self.last_p = None
        st = self.doc.styles['Normal']; st.font.name = FONT; st.font.size = Pt(10)
        st.element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
        self.body = self.doc.element.body
        for el in list(self.body):
            if el.tag != qn('w:sectPr'): self.body.remove(el)
        self.hdr_refs = None

    def para(self): 
        p = self.doc.add_paragraph(); self.last_p = p; return p

    def end_section(self, cols):
        """Close the current section (which has `cols` columns) at the last paragraph."""
        p = self.doc.add_paragraph(); pfmt(p, WD_ALIGN_PARAGRAPH.LEFT, 0, 0, line=Pt(1)); r = p.add_run(''); set_font(r, 1)
        rpr = p._p.get_or_add_pPr(); rr = OxmlElement('w:rPr'); sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '2'); rr.append(sz); rpr.append(rr)
        ppr = p._p.get_or_add_pPr(); s = sect_xml(cols); self._hdr(s); ppr.append(s)

    def _hdr(self, s):
        if self.hdr_refs is not None:
            for r in self.hdr_refs: s.insert(0, copy.deepcopy(r))

    def switch(self, cols):
        if cols == self.cols: return
        self.end_section(self.cols); self.cols = cols

    # ---- blocks
    def title_block(self):
        m = self.meta
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 0, 10); r = p.add_run(m['title']); set_font(r, 24)
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 0, 2); r = p.add_run(m['author']); set_font(r, 11)
        for a in m['affil']:
            p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 0, 0); r = p.add_run(a); set_font(r, 10, italic=True)
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 6, 12); r = p.add_run(m['note']); set_font(r, 8)

    def heading(self, b):
        C = self.C
        if b['lvl'] == 1:
            p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 10, 4, keep_next=True)
            if b['num']: r = p.add_run(b['num'] + '.\u2002'); set_font(r, 10)
            add_inline(p, b['text'], C, 10, smallcaps=True)
        elif b['lvl'] == 2:
            p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.LEFT, 6, 2, keep_next=True)
            add_inline(p, (b['num'] + '. ' if b['num'] else '') + b['text'], C, 10, italic=True)
        else:
            self.pending_runin = b['text']

    def paragraph(self, b, size=10, indent=True):
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 0, 0, first=0.14 if indent else 0)
        t = b['text']
        if getattr(self, 'pending_runin', None):
            add_inline(p, self.pending_runin + ': ', self.C, size, italic=True); self.pending_runin = None
        add_inline(p, t, self.C, size); return p

    def listitem(self, b):
        t = b['text']; m = re.match(r'(\d+\))\s+(.*)', t)
        lab, rest = (m.group(1), m.group(2)) if m else ('\u2022', t[2:])
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 0, 1, left=0.28, hanging=0.18)
        r = p.add_run(lab + '\u2002'); set_font(r, 10); add_inline(p, rest, self.C, 10)

    def equation(self, b):
        C = self.C; w = COLW if self.cols == 2 else FULLW
        t = self.doc.add_table(rows=1, cols=2); t.alignment = WD_TABLE_ALIGNMENT.CENTER; t.autofit = False
        self._tbl_layout(t, [w - 0.42, 0.42], borders=False, cellmar=(0, 0))
        c0, c1 = t.rows[0].cells
        p = c0.paragraphs[0]; pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 3, 3)
        om = copy.deepcopy(C.omml[C.mathidx[(b['tex'], True)]]); mp = etree.SubElement(p._p, f'{{{M_NS}}}oMathPara'); mp.append(om)
        p = c1.paragraphs[0]; pfmt(p, WD_ALIGN_PARAGRAPH.RIGHT, 3, 3); r = p.add_run(f'({b["num"]})'); set_font(r, 10)
        c1.vertical_alignment = 1
        tcPr = c1._tc.get_or_add_tcPr(); va = OxmlElement('w:vAlign'); va.set(qn('w:val'), 'center'); tcPr.append(va)
        self.last_p = None

    def _tbl_layout(self, t, widths, borders=True, cellmar=(40, 40)):
        tbl = t._tbl; pr = tbl.tblPr
        for tag in ('w:tblStyle',):
            e = pr.find(qn(tag))
            if e is not None: pr.remove(e)
        tw = pr.find(qn('w:tblW'))
        if tw is None: tw = OxmlElement('w:tblW'); pr.append(tw)
        tw.set(qn('w:w'), str(int(sum(widths) * 1440))); tw.set(qn('w:type'), 'dxa')
        lay = OxmlElement('w:tblLayout'); lay.set(qn('w:type'), 'fixed'); pr.append(lay)
        b = OxmlElement('w:tblBorders')
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            e = OxmlElement('w:' + side); e.set(qn('w:val'), 'nil'); b.append(e)
        pr.append(b)
        cm = OxmlElement('w:tblCellMar')
        for side, v in (('top', 10), ('left', cellmar[0]), ('bottom', 10), ('right', cellmar[1])):
            e = OxmlElement('w:' + side); e.set(qn('w:w'), str(v)); e.set(qn('w:type'), 'dxa'); cm.append(e)
        pr.append(cm)
        grid = tbl.tblGrid
        for g in list(grid): grid.remove(g)
        for wd in widths:
            g = OxmlElement('w:gridCol'); g.set(qn('w:w'), str(int(wd * 1440))); grid.append(g)
        for row in t.rows:
            for c, wd in zip(row.cells, widths):
                c.width = Inches(wd)

    def _cell_border(self, cell, **sides):
        tcPr = cell._tc.get_or_add_tcPr(); b = tcPr.find(qn('w:tcBorders'))
        if b is None: b = OxmlElement('w:tcBorders'); tcPr.append(b)
        for side, sz in sides.items():
            e = OxmlElement('w:' + side); e.set(qn('w:val'), 'single'); e.set(qn('w:sz'), str(sz)); e.set(qn('w:space'), '0'); e.set(qn('w:color'), '000000'); b.append(e)

    def table(self, b):
        C = self.C; wide = b['wide']; fs = 7.5 if 'small' in b['opts'] else 8
        if wide: self.switch(1)
        W = FULLW if wide else COLW; rel = b['widths']; widths = [W * r / sum(rel) for r in rel]
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 8, 0, keep_next=True); r = p.add_run(f'TABLE {ROMAN[b["num"] - 1]}'); set_font(r, 8)
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 0, 3, keep_next=True); add_inline(p, b['title'], C, 8, smallcaps=True)
        rows = b['rows']; t = self.doc.add_table(rows=len(rows), cols=len(rel)); t.alignment = WD_TABLE_ALIGNMENT.CENTER; t.autofit = False
        self._tbl_layout(t, widths)
        nhead = 2 if 'group' in b['opts'] else 1
        for i, row in enumerate(rows):
            sub = len(row) >= 1 and row[0].startswith('>>')   # panel header row spanning the table
            for j, cell in enumerate(t.rows[i].cells):
                txt = row[j] if j < len(row) else ''
                if sub and j == 0: txt = txt[2:].strip()
                cp = cell.paragraphs[0]
                left = (j == 0) or 'left' in b['opts'] or len(txt) > 28
                pfmt(cp, WD_ALIGN_PARAGRAPH.LEFT if left else WD_ALIGN_PARAGRAPH.CENTER, 0.5, 0.5, keep_next=(i < len(rows) - 1))
                add_inline(cp, txt, C, fs, bold=(i < nhead) or None, italic=sub or None)
                if i == 0: self._cell_border(cell, top=8, bottom=4)
                if i == 1 and nhead == 2: self._cell_border(cell, bottom=4)
                if i == len(rows) - 1: self._cell_border(cell, bottom=8)
            if sub:
                a = t.rows[i].cells[0]; a.merge(t.rows[i].cells[-1])
            if i == 0 and 'group' in b['opts']:
                cells = t.rows[0].cells; j = 0
                while j < len(row):
                    k = j
                    while k + 1 < len(row) and row[k + 1] == '' and row[j] != '': k += 1
                    if k > j:
                        m = cells[j].merge(cells[k])
                        for extra in m.paragraphs[1:]: extra._p.getparent().remove(extra._p)
                        m.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                        self._cell_border(m, top=8, bottom=4)
                    j = k + 1
            trPr = t.rows[i]._tr.get_or_add_trPr(); cs = OxmlElement('w:cantSplit'); trPr.append(cs)
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 2, 8)
        if b['note']: add_inline(p, b['note'], C, 7)
        else: r = p.add_run(''); set_font(r, 4)
        if wide: self.switch(2)

    def figure(self, b):
        C = self.C; wide = b['wide']
        if wide: self.switch(1)
        path = os.path.join(FIGDIR, b['file'])
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.CENTER, 6, 2, keep_next=True)
        p.add_run().add_picture(path, width=Inches((FULLW - 0.1) if wide else COLW - 0.02))
        p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 0, 8)
        r = p.add_run(f'Fig. {b["num"]}.\u2002'); set_font(r, 8); add_inline(p, b['cap'], C, 8)
        if wide: self.switch(2)

    def references(self):
        for i, k in enumerate(self.C.cites):
            p = self.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 0, 1, left=0.32, hanging=0.32)
            r = p.add_run(f'[{i + 1}]\t'); set_font(r, 8)
            ts = p.paragraph_format.tab_stops; ts.add_tab_stop(Inches(0.32))
            add_inline(p, fmt_ref(self.bib[k]).replace('$', '\\$'), self.C, 8)

    def header(self, text):
        sec = self.doc.sections[0]; sec.different_first_page_header_footer = False
        hp = sec.header.paragraphs[0]; pfmt(hp, WD_ALIGN_PARAGRAPH.LEFT)
        r = hp.add_run(text + '\t\t'); set_font(r, 8)
        hp.paragraph_format.tab_stops.add_tab_stop(Inches(FULLW), alignment=2)
        r = hp.add_run(); set_font(r, 8)
        for tag, txt in (('begin', None), (None, 'PAGE'), ('end', None)):
            if tag:
                e = OxmlElement('w:fldChar'); e.set(qn('w:fldCharType'), tag); r._r.append(e)
            else:
                e = OxmlElement('w:instrText'); e.set(qn('xml:space'), 'preserve'); e.text = txt; r._r.append(e)
        final = self.body.find(qn('w:sectPr'))
        self.hdr_refs = [copy.deepcopy(x) for x in final if x.tag in (qn('w:headerReference'), qn('w:footerReference'))]

def build(src_path, out_path):
    src = open(src_path, encoding='utf8').read()
    bib = {}
    for f in ('refs.bib', 'refs_new.bib'):
        if os.path.exists(os.path.join(HERE, f)): bib.update(parse_bib(os.path.join(HERE, f)))
    meta = {}; 
    for k in ('TITLE', 'AUTHOR', 'NOTE', 'HEADER'):
        m = re.search(rf'^%%{k}:\s*(.*)$', src, re.M); meta[k.lower()] = m.group(1).strip()
    meta['affil'] = re.findall(r'^%%AFFIL:\s*(.*)$', src, re.M)
    blocks = read_blocks(src); C = Ctx(); number_pass(blocks, C, bib); collect_math(blocks, C); convert_math(C)
    B = Builder(C, bib, meta); B.header(meta['header']); B.title_block(); B.switch(2)
    for b in blocks:
        k = b['k']
        if k == 'h': B.heading(b)
        elif k == 'p': B.paragraph(b)
        elif k == 'li': B.listitem(b)
        elif k == 'eq': B.equation(b)
        elif k == 'tab': B.table(b)
        elif k == 'fig': B.figure(b)
        elif k == 'cmd':
            if b['name'] == 'ABSTRACT':
                p = B.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 0, 0, first=0.14); r = p.add_run('Abstract'); set_font(r, 9, bold=True, italic=True); add_inline(p, '\u2014' + b['arg'], C, 9, bold=True)
            elif b['name'] == 'INDEX':
                p = B.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 4, 4, first=0.14); r = p.add_run('Index Terms'); set_font(r, 9, bold=True, italic=True); add_inline(p, '\u2014' + b['arg'], C, 9, bold=True)
            elif b['name'] == 'REFERENCES': B.references()
            elif b['name'] == 'SMALL': B.paragraph(dict(text=b['arg']), size=8, indent=False)
            elif b['name'] == 'PROP':
                p = B.para(); pfmt(p, WD_ALIGN_PARAGRAPH.JUSTIFY, 3, 3, first=0.14); nm, rest = b['arg'].split('::', 1)
                r = p.add_run(nm.strip() + '. '); set_font(r, 10, bold=True); add_inline(p, rest.strip(), C, 10, italic=True)
            else: raise SystemExit('unknown command ' + b['name'])
    # final section properties: two columns
    final = B.body.find(qn('w:sectPr')); new = sect_xml(2); B._hdr(new); B.body.replace(final, new)
    # first section must not be 'continuous'
    B.doc.save(out_path)
    print('figures', C.fig, 'tables', C.tab, 'equations', C.eq, 'references', len(C.cites), 'math', len(C.math))
    unused = [k for k in bib if k not in C.cites]; print('unused refs:', len(unused), ' '.join(unused[:80]))

if __name__ == '__main__':
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'paper.md'), sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'out.docx'))
