# Paper source

`ETF_Premiums_Paper_IEEE_draft.docx` is generated from these three files.

- `content.js` — title, abstract, body text, tables. Markup: `**bold**`, `*italic*`, `[[key]]` for a citation.
- `refs.js` — reference list keyed by the names used in `[[key]]`. References are numbered in order of first citation.
- `build.js` — layout (A4, two columns, Times New Roman 10 pt, IEEE-style headings, tables and equations).

Rebuild: `npm install docx` once, then `node build.js ETF_Premiums_Paper_IEEE_draft.docx`.

Edits made directly in Word are lost on rebuild; change `content.js` instead, or stop rebuilding once you edit in Word.
