%%TITLE: Test Title of the Paper
%%AUTHOR: Amish Yadav
%%AFFIL: School
%%NOTE: Third draft
%%HEADER: THIRD DRAFT
!ABSTRACT This is the abstract with $\pi_{i,t}$ math and a cite [@shleifer1997limits].
!INDEX terms, here.
# Introduction {#sec:intro}
First paragraph with *italic*, **bold**, math $c_{i,t}=\ln(1+b)$ and refs {eq:fence}, {fig:a}, {tab:t}, {sec:intro} and [@lee1991investor; @pontiff1996costly; @delong1990noise].
$$\pi_{i,t} \le c_{i,t} \equiv \ln(1+b) - \ln\!\left(\frac{V_{i,t}}{V_{i,t-\ell}}\right)$$ {fence}
!PROP Proposition 1 (Fence) :: Under a value anchor, $\pi\le c$.
Second paragraph follows here and is long enough to wrap across the column so we can see justification and the first-line indent working as intended in the output.
## Sub heading {#sec:sub}
1) First item with text.
2) Second item.
!FIG[fig_glide.png|w1|a|Caption of a single column figure with $x$.]
!TABLE[t|w1|A small table|2,1,1|]
| Fund | A | B |
|---|---|---|
| MONQ50 | 1.0 | 2.0 |
| >> Panel B |
| MON100 | 3.0 | 4.0 |
!NOTE Note text here [@lee1991investor].
!FIG[fig_history.png|w2|b|Wide figure caption.]
Text after the wide figure continues in two columns. More and more text to fill.
# References {#nonum}
!REFERENCES
