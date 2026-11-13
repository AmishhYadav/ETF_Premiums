# Paper, second draft (12 Nov 2026)

"Anchored to What? Price Limits, Rationed Creation and the Premiums on India's International ETFs"

Single-column working paper, typeset in LaTeX. The first draft (IEEE two-column, Word) is untouched in `paper/` and `paper/source/`.

## Files

- `paper.tex` — preamble, title page, Sections 1-4.
- `body2.tex` — Sections 5-11 and Appendices A-D.
- `refs.bib` — references: academic works first, then regulators, fund houses and press.
- `figures/` — six figures (PDF for LaTeX, PNG copies).
- `analysis/spot_checks.py` — recomputes every number taken from exchange and AMFI files (Tables 3, 5, 7, 8; iNAV check) from `tests/fixtures`. Standard library only.
- `analysis/mc_censored.py`, `mc_merge.py`, `mc_summary.py` — Monte Carlo of Section 7.3. `mc_results.json` holds the 720 replications behind Table 4.
- `analysis/figures.py` — draws the figures.
- `analysis/mc_table.tex`, `analysis/mc_macros.tex` — written by `mc_summary.py`; the paper reads its Monte Carlo numbers from them.

## Rebuild

```
sh build.sh                      # pdflatex + bibtex; needs a TeX distribution (MacTeX, or upload the folder to Overleaf)
python3 analysis/spot_checks.py  # prints the file-based numbers
cd analysis
python3 mc_censored.py 0.1 240 && python3 mc_censored.py 0.2 240 && python3 mc_censored.py 0.3 240   # about 7 min each on 2 cores
python3 mc_merge.py && python3 mc_summary.py && python3 figures.py
```

Python needs numpy, scipy and matplotlib. Seeds are fixed, so the Monte Carlo reproduces.

## What is and is not in this draft

- No estimate from the study's own panel. Tables 5, 7 and 8 come from fixture files for 1 Jul 2024, 2 Jan 2025, 4 Sep 2026 and 9 Nov 2026; Table 6 mixes those with press figures.
- When the pipeline's export exists, the planned exhibits in `research/BLUEPRINT.md` replace Section 8 and the press figures.
