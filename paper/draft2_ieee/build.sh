#!/bin/sh
# Build the paper: figures (optional), LaTeX, BibTeX. Usage: sh build.sh
set -e
pdflatex -interaction=nonstopmode -halt-on-error paper.tex > build.log 2>&1 || { tail -30 build.log; exit 1; }
bibtex paper >> build.log 2>&1 || true
pdflatex -interaction=nonstopmode paper.tex >> build.log 2>&1
pdflatex -interaction=nonstopmode paper.tex >> build.log 2>&1
pdflatex -interaction=nonstopmode paper.tex > build.log 2>&1
grep -E "^!|Warning: (Citation|Reference)|multiply defined" build.log | sort | uniq -c | head -20 || true
echo "overfull boxes: $(grep -c Overfull build.log)"; pdfinfo paper.pdf | grep Pages
