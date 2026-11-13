const fs = require("fs");
const d = require("docx");
const {
  Document, Packer, Paragraph, TextRun, AlignmentType, SectionType, Footer, PageNumber,
  Table, TableRow, TableCell, WidthType, BorderStyle, TabStopType,
  Math: M, MathRun, MathFraction, MathSubScript, MathSuperScript, MathSubSuperScript, MathSum,
} = d;
const C = require("./content.js");
const REFS = require("./refs.js");

const FONT = "Times New Roman";
const PAGE_W = 11906, PAGE_H = 16838;          // A4
const MARGIN_LR = 1080, MARGIN_T = 1080, MARGIN_B = 1300, GAP = 360;
const COL_W = Math.floor((PAGE_W - 2 * MARGIN_LR - GAP) / 2);   // 4693

// ---------- citations ----------
const order = [];
function citeNum(key) {
  if (!REFS[key]) throw new Error("Unknown reference key: " + key);
  let i = order.indexOf(key);
  if (i < 0) { order.push(key); i = order.length - 1; }
  return i + 1;
}

// ---------- inline markup -> TextRuns ----------
function runs(text, base = {}) {
  // table notes use [key]; convert to [[key]] when key exists
  text = text.replace(/(?<!\[)\[([a-z0-9_]+)\](?!\])/g, (m, k) => (REFS[k] ? `[[${k}]]` : m));
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|\[\[[^\]]+\]\])/g;
  let last = 0, m;
  const push = (t, extra = {}) => { if (t) out.push(new TextRun({ text: t, font: FONT, ...base, ...extra })); };
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) push(tok.slice(2, -2), { bold: true });
    else if (tok.startsWith("[[")) {
      const keys = tok.slice(2, -2).split(",").map((s) => s.trim());
      push(keys.map((k) => `[${citeNum(k)}]`).join(", "));
    } else push(tok.slice(1, -1), { italics: true });
    last = m.index + tok.length;
  }
  push(text.slice(last));
  return out;
}

// ---------- paragraph helpers ----------
const body = (text, indent = true) => new Paragraph({
  alignment: AlignmentType.JUSTIFIED,
  indent: indent ? { firstLine: 200 } : undefined,
  spacing: { before: 0, after: 0, line: 240 },
  children: runs(text, { size: 20 }),
});
const ROMAN = ["I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII","XIII"];
let h1n = 0, h2n = 0;
const h1 = (title) => { h1n++; h2n = 0; return new Paragraph({
  alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 200, after: 80 },
  children: [new TextRun({ text: `${ROMAN[h1n - 1]}.  `, font: FONT, size: 20 }), new TextRun({ text: title, font: FONT, size: 20, smallCaps: true })],
}); };
const h1x = (title, sub) => { h2n = 0; const kids = [new TextRun({ text: title, font: FONT, size: 20, smallCaps: true })];
  if (sub) kids.push(new TextRun({ text: sub, font: FONT, size: 20, smallCaps: true, break: 1 }));
  return new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 200, after: 80 }, children: kids }); };
const h2 = (title) => { h2n++; return new Paragraph({
  alignment: AlignmentType.LEFT, keepNext: true, spacing: { before: 120, after: 40 },
  children: [new TextRun({ text: `${String.fromCharCode(64 + h2n)}. ${title}`, font: FONT, size: 20, italics: true })],
}); };

// ---------- equations (plain runs with sub/superscripts: renders the same in Word and LibreOffice) ----------
// token syntax: "_{x}" subscript, "^{x}" superscript, everything else italic for letters
function eqRuns(src) {
  const out = []; const re = /(_\{[^}]*\}|\^\{[^}]*\})/g; let last = 0, m;
  const push = (t, extra = {}) => { if (t) out.push(new TextRun({ text: t, font: FONT, size: 20, italics: true, ...extra })); };
  while ((m = re.exec(src)) !== null) {
    push(src.slice(last, m.index));
    const tok = m[0]; const inner = tok.slice(2, -1);
    push(inner, tok[0] === "_" ? { subScript: true } : { superScript: true });
    last = m.index + tok.length;
  }
  push(src.slice(last));
  return out;
}
const EQ = {
  band: "P_{i,t} ≤ (1 + b_{t}) B_{i,t}",
  raw: "π^{raw}_{i,t} = P_{i,t} / NAV_{i,t} − 1",
  fv: "FV_{i,t} = NAV_{i,a} × (F^{IN}_{t} / F^{cl}_{a}) × (S^{IN}_{t} / S^{cl}_{a})",
  pi: "π_{i,t} = ln(P_{i,t} / FV_{i,t})",
  compound: "P_{i,t} ≤ (1.2)^{k} P_{i,t−k}",
  event: "π_{i,t} = α_{i} + λ_{t} + Σ_{k≠−1} β_{k} D^{k}_{i,t} + ε_{i,t}",
  trade: "Δπ_{i,t} = b_{1}T_{i,t} + b_{2}d_{i,t} + b_{3}r_{i,t−1} + b_{4}A_{t} + α_{i} + ε_{i,t}",
  excess: "X_{i,t} = ln(P_{i,t} / (1.2 FV_{i,t}))",
};
let eqn = 0;
const eq = (name) => { eqn++; return new Paragraph({
  spacing: { before: 100, after: 100 },
  tabStops: [{ type: TabStopType.CENTER, position: Math.floor(COL_W / 2) }, { type: TabStopType.RIGHT, position: COL_W }],
  children: [new TextRun({ text: "\t", font: FONT, size: 20 }), ...eqRuns(EQ[name]), new TextRun({ text: `\t(${eqn})`, font: FONT, size: 20 })],
}); };

// ---------- tables ----------
const NONE = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const LINE = { style: BorderStyle.SINGLE, size: 6, color: "000000" };
function table(key) {
  const t = C.TABLES[key];
  const total = t.widths.reduce((a, b) => a + b, 0);
  if (total > COL_W) throw new Error(`Table ${key} too wide: ${total} > ${COL_W}`);
  const cell = (txt, w, opts) => new TableCell({
    width: { size: w, type: WidthType.DXA },
    margins: { top: 30, bottom: 30, left: 50, right: 50 },
    borders: { top: opts.top ? LINE : NONE, bottom: opts.bottom ? LINE : NONE, left: NONE, right: NONE },
    children: [new Paragraph({ alignment: opts.first ? AlignmentType.LEFT : AlignmentType.CENTER, spacing: { before: 0, after: 0 },
      children: runs(txt, { size: 16, bold: !!opts.head })})],
  });
  const rows = [new TableRow({ tableHeader: true, cantSplit: true, children: t.head.map((h, i) => cell(h, t.widths[i], { head: true, top: true, bottom: true, first: i === 0 })) })];
  t.rows.forEach((row, ri) => rows.push(new TableRow({ cantSplit: true, children: row.map((c, i) => cell(c, t.widths[i], { bottom: ri === t.rows.length - 1, first: i === 0 })) })));
  const out = [
    new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 160, after: 0 },
      children: [new TextRun({ text: `TABLE ${t.num}`, font: FONT, size: 16 })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 0, after: 60 },
      children: [new TextRun({ text: t.title, font: FONT, size: 16, smallCaps: true })] }),
    new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: t.widths, alignment: AlignmentType.CENTER, rows }),
  ];
  out.push(new Paragraph({ alignment: AlignmentType.LEFT, spacing: { before: 40, after: 160 },
    children: t.note ? runs(t.note, { size: 14 }) : [new TextRun({ text: "", font: FONT, size: 14 })] }));
  return out;
}

// ---------- assemble body ----------
const kids = [];
kids.push(new Paragraph({ alignment: AlignmentType.JUSTIFIED, indent: { firstLine: 200 }, spacing: { before: 0, after: 0 },
  children: [new TextRun({ text: "Abstract", font: FONT, size: 18, bold: true, italics: true }), new TextRun({ text: "—" + C.ABSTRACT, font: FONT, size: 18, bold: true })] }));
kids.push(new Paragraph({ alignment: AlignmentType.JUSTIFIED, indent: { firstLine: 200 }, spacing: { before: 80, after: 0 },
  children: [new TextRun({ text: "Index Terms", font: FONT, size: 18, bold: true, italics: true }), new TextRun({ text: "—" + C.INDEX_TERMS, font: FONT, size: 18, bold: true })] }));

for (const b of C.BODY) {
  if (b.h1) kids.push(h1(b.h1));
  else if (b.h1x) kids.push(h1x(b.h1x, b.sub));
  else if (b.h2) kids.push(h2(b.h2));
  else if (b.p) kids.push(body(b.p, true));
  else if (b.p0) kids.push(body(b.p0, false));
  else if (b.eq) kids.push(eq(b.eq));
  else if (b.table) kids.push(...table(b.table));
  else throw new Error("Unknown block " + JSON.stringify(b));
}

// ---------- references ----------
kids.push(h1x("References"));
order.slice().forEach((key, i) => {
  kids.push(new Paragraph({ alignment: AlignmentType.LEFT, indent: { left: 360, hanging: 360 }, spacing: { before: 0, after: 20 },
    children: [new TextRun({ text: `[${i + 1}]\t`, font: FONT, size: 16 }), ...runs(REFS[key], { size: 16 })],
    tabStops: [{ type: TabStopType.LEFT, position: 360 }] }));
});
const unused = Object.keys(REFS).filter((k) => !order.includes(k));
if (unused.length) console.log("Unused refs:", unused.join(", "));
console.log("References cited:", order.length, "| equations:", eqn);

// ---------- document ----------
const footer = () => new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
  children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16 })] })] });
const page = { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN_T, bottom: MARGIN_B, left: MARGIN_LR, right: MARGIN_LR, footer: 600 } };

const titleBlock = [
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 200 }, children: [new TextRun({ text: C.TITLE, font: FONT, size: 44 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 20 }, children: [new TextRun({ text: C.AUTHOR, font: FONT, size: 22 })] }),
  ...C.AFFIL.map((a) => new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 0 }, children: [new TextRun({ text: a, font: FONT, size: 20, italics: true })] })),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 240 }, children: [new TextRun({ text: C.DRAFT_NOTE, font: FONT, size: 16 })] }),
];

const doc = new Document({
  creator: C.AUTHOR, title: C.TITLE,
  styles: { default: { document: { run: { font: FONT, size: 20 } } } },
  sections: [
    { properties: { page }, footers: { default: footer() }, children: titleBlock },
    { properties: { type: SectionType.CONTINUOUS, page, column: { count: 2, space: GAP, equalWidth: true } }, footers: { default: footer() }, children: kids },
  ],
});
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(process.argv[2] || "paper.docx", buf); console.log("written"); });
