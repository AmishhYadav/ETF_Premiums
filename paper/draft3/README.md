# Draft 3 (15 Nov 2026) — IEEE two-column, Word

- `ETF_Premiums_Paper_draft3_IEEE.docx` — the paper. `.pdf` is Word's own export of it.
- `paper/` — source: `p1.md p2.md p3.md` (concatenate to `paper.md`), `build.py` (python-docx + pandoc for equations), `refs.bib`, `refs_new.bib`.
  Rebuild: `cat p1.md p2.md p3.md > paper.md && python3 build.py paper.md out.docx`
- `analysis/` — `common.py` (panel loader), `a1..a5` (descriptives, P1, P2, bootstrap, variants, P3/money, S1), `censar.py` (censored AR(1) filter), `figs.py` (`python3 figs.py <name>`).
  Reads `exports/research/2026-11-14/`. Paths inside the scripts point at the sandbox copy; change `common.py` to run here.
- `out/` — results JSON behind every number in Section VIII. `figures/` — PNG, 300 dpi.
- `_to_delete/` — scratch files; safe to remove.
