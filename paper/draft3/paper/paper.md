%%TITLE: Anchored to What? Price Limits, Rationed Creation and the Premiums on India's International ETFs
%%AUTHOR: Amish Yadav
%%AFFIL: Mukesh Patel School of Technology Management & Engineering, SVKM's NMIMS, Mumbai, India
%%NOTE: Third draft, 15 November 2026. First results from the study's own panel; fair value is not yet adjusted for index futures.
%%HEADER: THIRD DRAFT, 15 NOVEMBER 2026

!ABSTRACT India caps what its mutual funds may invest abroad, and the cap has rationed the creation of units in its six listed international exchange-traded funds; the last unit in any of them was created in November 2025. Until 4 September 2026 a daily price band anchored to a lagged net asset value (NAV) capped their premiums near 20%. On 7 September the band was re-anchored to the previous close. Using a daily panel built from exchange and fund-industry files for January 2021 to 13 November 2026, this paper reports four results. First, the old band was a binding price ceiling: in its last 120 sessions five of the six funds closed at the upper limit on 85% of fund-days, with trading reduced to a trickle. Second, the break is on the rule date. On 7 September, a US holiday on which NAV could not move, the five funds rose by 3.6% to 15.6%, and the event statistic ranks second among 950 dates since 2023, behind only the following session. Third, the ceiling hid less than the later premiums suggest. Four funds traded freely on the first day at premiums of 23% to 29%, inside the interval that a censored autoregression fitted to pre-change data assigns to the hidden premium. Over sessions 16 to 40 the premium of the three small funds on US indices then stood at 46% to 88%, above a bootstrap-calibrated forecast band, while the largest fund fell back below the old ceiling. Fourth, buyers paid Rs 1,668 crore above NAV in 47 sessions, of which Rs 929 crore was above the price the old band would have allowed. A limit anchored to value is a fence; a limit anchored to price is a speed limit; and the planned return to a NAV anchor on 1 April 2027 forces a one-day fall of 14% to 40% in four funds at current premiums unless it is phased in. Tests for that date are fixed here in advance.

!INDEX exchange-traded funds, price limits, reference price, limits to arbitrage, capital controls, censored regression, price ceilings, pre-registration, India. JEL: G12, G14, G18, G23, F38.

# Introduction {#sec:intro}

On 18 September 2026 a unit of the Motilal Oswal Nasdaq Q50 ETF closed at Rs 396.30 on the National Stock Exchange of India (NSE). The last net asset value (NAV) published before that close was Rs 119.15. Buyers paid Rs 3.33 for each rupee of Nasdaq-listed shares the fund held. Two weeks earlier, on 4 September, the same unit had closed 19.4% above NAV. The fifty companies in the fund did not triple in value in between. A trading rule changed.

An exchange-traded fund (ETF) normally trades near NAV because market makers can create new units at NAV when the price is higher and redeem units when it is lower [@bendavid2017exchange]. India caps what its mutual funds may invest abroad, at USD 7 billion for the industry and USD 1 billion for a fund house [@bs2021limit], and the caps have bound since early 2022 [@pti2022resume]. An ETF that holds foreign shares must buy more of them to create a unit, so for India's six listed international ETFs creation has been rationed for most of the period since. Redemption still works. The funds behave like closed-end funds whose supply can shrink and can grow only when their fund house finds room under its cap.

A second rule hid the consequences. Exchanges allowed an ETF to trade only within 20% of its NAV of two days earlier [@sebi2026circular], and the premiums of these funds pressed against that ceiling. On 7 September 2026 the Securities and Exchange Board of India (SEBI) moved the band's base to the previous day's closing price [@sebi2026circular; @sebi2026ext]. The change answered a problem in gold and silver ETFs, whose prices had outrun a stale NAV base early in 2026 [@sebi2026cp]. For a fund that cannot create units it removed the ceiling: each close became the next day's base, so a premium could compound by up to 20% a day.

This paper is about that base. Research on price limits asks whether limits should exist and how wide they should be; in the cases studied the limit is anchored to the previous close or settlement price, and the asset has no published value against which a limit could be set instead. India's ETFs had a limit anchored to value, then one anchored to price, in the same securities, with the barrier to arbitrage unchanged. The two anchors do different jobs. A limit anchored to value is a fence: it bounds how far price can stand from value, whatever buyers want. A limit anchored to price is a speed limit: it bounds each day's move and places no bound on where the price ends up. The fence was never designed as one. SEBI's stated concern was the staleness of the base, and neither its consultation paper nor its circular mentions funds that cannot create units.

The second draft of this paper set out a design and had no data of its own. This draft has the data. A pipeline built for the project now assembles NSE's daily price and security-master files, BSE's price files and the fund industry's NAV files into a fund-day panel from January 2021 to 13 November 2026 [@yadav2026panel]. The hypotheses and tests were fixed on 11 and 12 November, before the panel existed ({app:protocol}). What follows are first results, with one known shortcoming: fair value is the last NAV known at the Indian close and is not yet moved to the close with index futures, because the terminal export that supplies them is due in December. At premiums of 20% to 230% the omission is small. Five findings stand out.

1) The fence was a binding price ceiling, and it rationed quantity as well as price. In the last 120 sessions of the old band the five funds other than the Hang Seng fund closed at the upper limit on 85% of fund-days. On those days median turnover was 0.14% of units outstanding, against 0.34% on other days, and the Nasdaq Q50 ETF traded 184 times on a median day. After the change it traded 14,480 times ({sec:fence}).
2) The break falls on the rule date. On 7 September 2026 US markets were closed and the NAV of the four US-linked funds could not move; their prices rose by 3.6% to 15.6%, the largest or near-largest daily rise since 2023 for each. A break-date search selects 7 September in four funds, and the event statistic computed at every date since January 2023 ranks second of 950, behind only 8 September. Domestic ETFs under the same rule did not move ({sec:p1}).
3) The fence hid a real but modest premium. Four funds did not touch even the initial 10% limit on the first day: their closes that day were market-clearing prices, at premiums of 23% to 29%, four to ten points above the ceiling they had been pressed against. These first free closes fall inside the 90% interval that a censored autoregression, fitted only to pre-change data, gives for the hidden premium of each fund. That is an out-of-sample check that such models rarely get ({sec:p2}).
4) What came afterwards was more than revelation in the small funds and less in the large one. Over sessions 16 to 40 the premiums of the three smaller US-linked funds averaged 46%, 52% and 88%, above a forecast band whose critical values come from a parametric bootstrap of the whole procedure (p-values 0.01, 0.04 and below 0.01). The Hang Seng TECH fund lies inside its band. The largest fund, the Nasdaq 100 ETF, pinned at the ceiling on 75% of the last 120 sessions, traded at 10% once the ceiling was gone. Premium changes, which reverted before the change, followed the fund's own previous-day return after it.
5) The sums are large and the exit is narrow. In 47 sessions buyers of the six funds paid Rs 1,668 crore above NAV, Rs 929 crore of it above the old ceiling. On 13 November the funds were priced Rs 4,941 crore above the Rs 19,647 crore they held. A return to a NAV anchor at those premiums forces a one-day fall of at least 14% to 40% in four funds ({sec:april}).

The paper makes five contributions, and it is careful about what it does not claim.

1) The anchor as the treatment. It is, to our knowledge, the first study of a change in the reference price of a daily price limit, from a lagged fundamental value to the previous close, in the same securities. {sec:framework} states what each anchor can and cannot bound, shows that a return from a price anchor to a value anchor cannot be smooth, and derives the notice a regulator must give to make it so.
2) Revealed against induced. It estimates the premium that a value-anchored limit censored, with a ceiling that moves each day, and splits the later premium into the part the rule revealed and the part that arose after it. It adds a model-free counterpart, the first free close, and shows that the two agree.
3) A fence is a price ceiling. It documents the quantity side of the old band, which the literature on price limits does not treat: units went to whoever was first in the queue at the limit price, and most days almost none changed hands.
4) Rationing dated from exchange records. It dates every creation and redemption from the exchange's count of units outstanding. Creation was not shut in 2022 or 2024, as commonly reported; one fund house recycled the room freed by redemptions into new units until November 2025, and the other never did.
5) A test fixed before the outcome. It specifies tests for April 2027, with three branches and five predictions, before any 2027 observation exists ({sec:april}).

The paper does not claim to be the first on suspended creation, which Johnson [@johnson2016essays] studies in exchange-traded notes, or on premiums behind capital controls, which the literature on closed-end country funds established [@bonserneal1990international]. It does not claim that buyers were irrational: daily data cannot identify who bought, and the April switch is a target that may be delayed. It does not claim that 233% is a record for any listed product; notes on collapsing assets have traded further from value. And six funds from three fund houses are a case, not a sample of ETFs.

India has seen the mirror image. In the 1990s and 2000s foreigners could buy depositary receipts of Indian companies abroad more freely than the shares themselves, and the receipts traded above the local shares [@jithendranathan2000barriers; @stigler2010understanding]; those of Infosys averaged a premium of about 50% in New York [@financeasia2003], which Puthenpurackal [@puthenpurackal2006explaining] attributes to limited supply, downward-sloping demand and trend-chasing by small investors. Then foreigners overpaid in New York for an Indian share; now residents overpay in Mumbai for American ones.

{sec:setting} sets out the institutions. {sec:framework} gives the framework. {sec:lit} places the paper in the literature. {sec:hyp} states the hypotheses, {sec:data} the data and measures and {sec:design} the empirical design. {sec:results} reports the results. {sec:april} fixes the tests for April 2027 and discusses the transition. {sec:limits} states the limitations and {sec:concl} concludes.

# Institutional Setting {#sec:setting}

## Creation, redemption and who may use them

An ETF publishes a NAV once a day and an indicative NAV (iNAV) during trading hours. Units are created and redeemed in blocks: 250,000 units for the Motilal Oswal Nasdaq 100 ETF [@motilal2026sid], 600,000 for the Nasdaq Q50 ETF [@indmoney2026unwind]. Only market makers and investors dealing in Rs 25 crore or more transact directly with the fund house [@mirae2026factsheet]; everyone else trades on the exchange. Arbitrage therefore rests on a few market makers and on the fund house's ability to issue. {fig:mechanism} sets out the parties.

!FIG[fig_mechanism.png|w2|mechanism|The valve and the band. Creation needs room under the fund house's overseas limit; redemption does not. With creation rationed, the daily price band is the only rule that stands between the exchange price and NAV.]

## Caps on overseas investment

The industry limit of USD 7 billion dates from 2008 [@lawasia2026]. In November 2020 SEBI raised the limit per fund house to USD 600 million [@bs2020limit] and in June 2021 to USD 1 billion, with a separate industry pool of USD 1 billion, and USD 300 million per house, for investment in foreign-listed ETFs [@bs2021limit]. On 18 January 2022 Motilal Oswal suspended the issuance of units in its two Nasdaq ETFs to manage its limit [@bs2022motilal], and fresh overseas subscriptions stopped across the industry from 1 February 2022. In June 2022 SEBI let funds resume, but only up to the room each fund house had on 1 February 2022 [@pti2022resume]. Mirae Asset stopped accepting direct creation requests in its three overseas ETFs from 5 January 2024 [@mirae2024addendum], and the pool for foreign-listed ETFs filled in April 2024 [@zerodha2024etf]. The Reserve Bank of India has declined requests to raise the limits [@bs2023rbi]. Several fund houses shut overseas schemes again between April and July 2026 [@outlook2026menu], and no increase had been announced by 14 November 2026.

Which limit binds the six funds is often misreported. Press accounts blame the USD 1 billion pool for foreign-listed ETFs and date the end of creation to April 2024 (for example [@vr2026up41]). That pool governs Indian funds that buy ETFs listed abroad. The six funds studied here hold foreign shares directly, so they draw on their fund house's room under the USD 7 billion limit, frozen at its level of 1 February 2022. Motilal Oswal suspended ETF issuance in January 2022, when the pool for foreign-listed ETFs was not yet full [@bs2022bind]; Mirae Asset's notice of January 2024 ties its suspension to SEBI's instruction of January 2022; and Mirae stopped creation three months before that pool filled.

The distinction has a consequence that the data bear out. Room is counted per fund house, not per scheme, and is freed by redemptions from any of the house's overseas schemes. A fund house can therefore still create ETF units when room appears elsewhere in its range. Creation is rationed by the fund house; it is not prohibited scheme by scheme. How a fund house allocates freed room among its schemes and market makers is not disclosed.

Residents have another legal route abroad. The Liberalised Remittance Scheme allows USD 250,000 a year, with tax collected at source of 20% on the amount above Rs 10 lakh, creditable against income tax [@bt2026lrs]. A premium paid only for access to foreign shares should be bounded by the cost of this route. For a large investor that cost is a few per cent; for the trades of Rs 2,000 to Rs 13,000 that are typical in these funds ({tab:desc}) fixed remittance charges weigh more, but not enough to account for premiums of 50% or 100%.

## Three price-band regimes

{tab:regimes} sets out the regimes. In each, the price $P_{i,t}$ of fund $i$ on day $t$ must satisfy

$$(1-b_t)\,B_{i,t} \le P_{i,t} \le (1+b_t)\,B_{i,t}$$ {band}

where $B_{i,t}$ is the base and $b_t$ the half-width of the band.

!TABLE[regimes|w2|Price-band regimes for equity ETFs|1.1,1.1,2.2,3.2,2.2|left]
| Regime | In force | Base $B_{i,t}$ | Band | Bound on the premium |
|---|---|---|---|---|
| NAV, lagged | To 4 Sep 2026 | Closing NAV of $t-2$ | Fixed ±20% | Level bounded; the bound moves with the two-day change in NAV |
| Previous close | From 7 Sep 2026 | Average price of the last 30 minutes of $t-1$; else last trade; else latest NAV | ±10%; widened by 5% of the base after a cooling-off of 15 minutes (5 in the last half-hour), at most twice in a direction | None on the level; at most 20% a day |
| NAV, previous day | Target: 1 Apr 2027 | Closing NAV of $t-1$ | As above | Level bounded again; a forced jump on the first day |
!NOTE Sources: SEBI [@sebi2026circular], paragraphs 2, 4.1, 4.2, 4.4 and 5.1; NSE [@nse2026circular]. The average price is volume-weighted. In the panel, prices never leave the first band in any session from January 2021, and the Hang Seng fund touches its upper limit from March 2022; the date on which that regime began has not been established from a circular. Overnight and liquid ETFs keep a fixed 5% band; gold and silver ETFs start at 6% and widen in steps of 3% without a cap.

Under the first regime the base was the closing NAV of two days earlier and the band a fixed 20%. The lag was not arbitrary. A scheme investing overseas may publish its NAV until 10 a.m. on the next business day [@sebi2023nav], after the market has opened, so when the band for day $t$ is set the newest NAV in existence is that of $t-2$ ({fig:timing}). In the last week of January 2026 gold and silver moved faster than that base, and exchanges used a more recent one for bullion ETFs as an interim step [@sebi2026cp]. SEBI's consultation paper of 13 February 2026 proposed three alternatives: the previous close, the average iNAV of the last half hour, or the previous day's NAV where available. Its evidence was that more than 99.8% of single-day moves in equity and debt ETFs between April and December 2025 stayed within 10%. It compared prices with previous closes. It did not discuss distance from NAV, overseas ETFs or constraints on creation.

!FIG[fig_timing.png|w1|timing|What is known when, for a US-linked fund. Times are Indian Standard Time, with US markets on summer time. The NAV dated $t$ uses the US close of date $t$, which falls ten hours after Indian trading ends, and is published by 10:00 on $t+1$. When the band for day $t$ is set, the newest NAV in existence is that of $t-2$.]

The circular of 15 June 2026 chose the previous close, defined as the volume-weighted average price of the last 30 minutes, and recorded that the previous day's NAV was operationally difficult to use (paragraph 4.1). Equity and debt ETFs start each day with a 10% band. When trades occur at 9.90% from the base a cooling-off of 15 minutes follows, and the band then widens by 5% of the base, at most twice in each direction and only on the side the price is moving (paragraph 5.1). Paragraph 4.4 directs exchanges and fund houses to resolve jointly the operational difficulties of using the previous day's closing NAV as the base, with effect from 1 April 2027. The start was moved from 1 to 7 September at the exchanges' request [@sebi2026ext], and NSE's implementing circular followed on 1 September [@nse2026circular]. The official band in NSE's security master matches the band computed from these rules on all 564 fund-days of the panel after the change.

Whether the rule covered international ETFs was unclear to at least one large broker. On 2 September a user of its forum asked, and warned that prices could run without a NAV anchor; staff first answered that the rule did not apply, and corrected the answer on 7 September [@zerodha2026forum]. Both exchanges issued press releases on 25 September. NSE's states that mutual funds have fully used their overseas limits, that the creation of new units in these ETFs is restricted, and that buyers at steep premiums face an abrupt fall in price unrelated to the underlying shares, including if the limits are raised or when the base migrates to NAV under paragraph 4.4 [@nse2026press; @bse2026press]. NSE repeated the caution on 7 October [@reuters2026nse], and at least one fund house warned investors about its own ETFs [@cafemutual2026mirae].

## How other markets treat the same problem

Cautions were the only instrument India used. {tab:markets} compares the rules of five markets in which funds on foreign assets trade under some limit on supply or on price. Two features stand out. Every market that keeps a daily price limit on such funds anchors it to the previous close, as India now does; India's old band was the exception. And the markets where premiums have been a problem act on the premium itself. China's exchanges and fund managers publish risk warnings and halt trading in cross-border ETFs when premiums are high, usually for an hour and in January 2025 for whole days [@bloomberg2025halts]; one fund issued more than a hundred warnings and was halted nearly forty times in the first quarter of 2026 [@yicai2026quota]. Korea, after leveraged oil notes traded far above their indicative value in April 2020 [@asiae2020etn], designates products whose price strays from value beyond a threshold, moves them to single-price auctions and suspends them if the gap persists [@fsc2020measures; @asiae2022disparity]. Taiwan exempts ETFs that hold foreign securities from its daily limit altogether and relies on creation [@twse2026limits]. These triggers are anchored to value. India's old band was, in effect, a rule of the same family, and the framework that replaced it has none.

!TABLE[markets|w2|Daily price limits and premium rules for funds on foreign assets|1.0,2.3,2.6,3.2|left]
| Market | Daily limit and its anchor | Rule that acts on the premium | Episode |
|---|---|---|---|
| India, to 4 Sep 2026 | ±20% of NAV of $t-2$ | The band itself | Premiums pinned near 20%, 2024 to 2026 |
| India, from 7 Sep 2026 | ±10% of previous close, widening to ±20% | None; investor cautions | 233% in one fund, Sep 2026 |
| China | ±10% of previous close; same-day round trips allowed in cross-border ETFs | Premium warnings and trading halts by exchanges and managers; purchase caps | Above 40% in Jan 2024; about 50% in Jan 2025 |
| Korea | ±30% of previous close | Disparity thresholds: caution designation, single-price auction, suspension | Oil notes, Apr 2020 |
| Taiwan | ±10% for domestic funds; none for funds holding foreign securities | None needed while creation is open | — |
| United States | None; pauses around a five-minute reference price | None; issuers disclose halts in creation | Notes and commodity funds, 2009, 2012, 2020 |
!NOTE Sources: India, {tab:regimes}; China [@bloomberg2025halts; @yicai2026quota; @reuters2024china]; Korea [@fsc2020measures; @asiae2022disparity; @asiae2020etn]; Taiwan [@twse2026limits]; United States [@finra2012etn; @kiplinger2009ung]. The table records the rules as described in these sources and is not a legal summary.

## NAV timing and the indicative NAV

The NAV of a US-linked fund dated $t$ moves with the US close of date $t$ and the exchange rate, not with the previous US session. That close comes about ten hours after Indian trading ends. The premium usually quoted, the close over the same-date NAV, therefore divides a price by a value determined later. In the panel the quoted premium of the four US-linked funds differs from the premium over the last NAV known at the close by 1.7 percentage points on an average day after the change, and by as much as 10.6 points.

The indicative NAV does not fill the gap. Motilal Oswal states that the iNAV of its two Nasdaq ETFs is the previous day's closing NAV adjusted only for currency movement [@motilal2026inav]. Values saved from the fund houses' own pages after the close of 9 November agree: the iNAVs of the two Motilal Oswal funds stood 0.187% below the previous NAV, the same to three decimal places for both, although the two funds' NAVs for that date turned out 3.2% and 3.4% higher. Neither published number is a contemporaneous value.

The timing also bears on April 2027. A US-linked fund's NAV for $t-1$ is not due before the market opens on $t$, so a literal previous-day NAV base cannot be set before trading starts. Exchanges must use an older NAV for these funds, reset the band during the session, or delay. Any NAV anchor restores a ceiling near 20%.

## Feeder funds

Funds of funds that invest in these ETFs value their holdings at the exchange price [@vr2026fof]. The panel confirms it. From 7 September to 12 November the daily return on the NAV of each of four feeders moves one for one with the exchange price of its ETF (coefficients of 1.00) and not at all with the ETF's own NAV (0.00). The NAV of Mirae Asset's S&P 500 Top 50 feeder rose 55.9% between 4 and 18 September, while the ETF it holds gained 1.6% at NAV. The premium sits inside the feeder's NAV.

Feeders were a channel of demand only while they could take new money, and they cannot. Motilal Oswal's Nasdaq 100 fund of funds stopped lump sums in January 2022 and paused instalment plans from 5 January 2025, citing exhausted overseas limits [@et2025motilal]. Mirae Asset's feeders accept only instalment plans registered before its January 2024 suspension [@mirae2024addendum]. What remains is the other direction. An investor who redeems from a feeder is paid the feeder's NAV, struck on the ETF's exchange price, and the feeder sells ETF units on the exchange to pay. Feeder redemptions therefore put units on the market and let long-standing holders cash the premium. Float should be measured net of feeder holdings; the monthly portfolios needed for that are not yet in the panel.

# A Fence and a Speed Limit {#sec:framework}

This section states what a price limit can bound when creation is rationed, as a function of what the limit is anchored to. The results are arithmetic, but they organise the rest of the paper.

Let $V_{i,t}$ be the value of a unit of fund $i$ at the close of day $t$ and $P_{i,t}$ its closing price. The premium is $\pi_{i,t}=\ln(P_{i,t}/V_{i,t})$. With creation rationed, the supply of units is fixed from day to day and price is set by demand. Let $P^{*}_{i,t}$ be the price that would clear the market on day $t$ if no price limit applied, and $\pi^{*}_{i,t}=\ln(P^{*}_{i,t}/V_{i,t})$ the latent premium. The limit has a value anchor with lag $\ell\ge 1$ if $B_{i,t}=V_{i,t-\ell}$, and a price anchor if $B_{i,t}=P_{i,t-1}$.

!PROP Proposition 1 (Fence) :: Under a value anchor with lag $\ell$, the premium satisfies {eq:fence} for every $t$, whatever the latent premium, and the observed premium is $\pi_{i,t}=\min(\pi^{*}_{i,t},c_{i,t})$ whenever $\pi^{*}_{i,t}$ lies above the lower limit.

$$\pi_{i,t}\le c_{i,t}\equiv \ln(1+b)-\ln\!\left(\frac{V_{i,t}}{V_{i,t-\ell}}\right)$$ {fence}

The bound does not depend on demand or on past prices. It does depend on how value has moved over the lag. With $b=0.2$ and $\ell=2$, the ceiling on the premium is 16.5% after a two-day rise in value of 3% and 23.7% after a fall of 3%. The premium is therefore a censored observation of demand, with a censoring point that changes daily for reasons unrelated to demand.

A binding fence is a price ceiling, and it has the quantity effects of one. At the limit, buyers who would pay more cannot bid more, so the units offered go to whoever is first in the queue at the limit price; the number traded is the number that holders are willing to sell at a price below what the marginal buyer would pay, which may be very few. The band then rations by time priority in place of price, as rent control and regulated energy prices ration by queue [@glaeser2003misallocation; @davis2011allocative]. The traded quantity is the short side of the market [@fair1972methods]. {sec:fence} shows how short it was.

!PROP Proposition 2 (Speed limit) :: Under a price anchor, for any $k\ge 1$ the change in the premium satisfies {eq:speed}, and there is no bound on $\pi_{i,t}$. With value constant, a latent premium $\pi^{*}$ is reached from $\pi_0$ in at most $\lceil(\pi^{*}-\pi_0)/\ln(1+b)\rceil$ sessions.

$$\pi_{i,t}-\pi_{i,t-k}\le k\ln(1+b)-\ln\!\left(\frac{V_{i,t}}{V_{i,t-k}}\right)$$ {speed}

A price anchor bounds the day's return, which is what a control on volatility is for. It says nothing about the distance between price and value, because the base inherits whatever distance already exists.

!PROP Proposition 3 (First free close) :: Let the anchor change from value to price after day $T_0$, and let $s$ be the first later session on which the price does not touch the upper limit. Then the close of session $s$ is a market-clearing price, $\pi_{i,s}=\pi^{*}_{i,s}$, and $s\le\lceil(\pi^{*}-\pi_{T_0})/\ln(1+b)\rceil$ when value and the latent premium are constant.

The first free close is a direct reading of the latent premium $s$ sessions after the fence is lifted. If the lifting did nothing but remove the fence, it estimates the premium the fence was hiding, up to $s$ days of ordinary drift. It needs no model, and it gives the model of {sec:design} something to be checked against.

!PROP Proposition 4 (Cliff) :: Let the anchor change from price to value on day $T$, and let $\Pi=P_{i,T-1}/V_{i,T-\ell-1}-1$ be the premium of the last close over the anchoring value. If $\Pi>b$, the price on day $T$ must fall from the previous close by at least the amount in {eq:cliff}. A price path that avoids the jump must satisfy $1+\Pi_{T-k}\le(1+b)/(1-b)^{k}$ for every $k\ge 1$, so bringing the premium inside the new band in advance takes at least the number of lower-limit sessions in {eq:sessions}.

$$1-\frac{1+b}{1+\Pi}$$ {cliff}

$$K(\Pi)=\left\lceil \frac{\ln\!\big((1+\Pi)/(1+b)\big)}{\ln\!\big(1/(1-b)\big)}\right\rceil$$ {sessions}

The return to a value anchor is discontinuous for any fund whose premium exceeds the band. At a premium of 101%, the level of the Nasdaq Q50 ETF on 13 November, the forced fall is 40%; under the present design, in which the band starts the day at 10%, the first trade must be at least 45% below the previous close. With $b=0.2$ the envelope allows premiums of at most 50%, 88% and 134% one, two and three sessions before the switch. {fig:cliff} plots both.

!FIG[fig_cliff.png|w1|cliff|The cliff. (a) The smallest one-day fall forced on a fund when the band is re-anchored from the previous close to NAV, as a function of its premium on the eve of the switch; circles place the six funds at their premiums over the last known NAV on 13 November 2026. (b) The highest premium from which a price falling at the lower limit every day can still reach the new band, by sessions before the switch.]

!PROP Proposition 5 (Glide path) :: Suppose that from session $T-n$ the exchange adds to the price-anchored band an upper limit $(1+c_t)V_{i,t-\ell}$ with $c_{T-n}\ge\Pi$ and $c_T=b$, and lets the ceiling fall by a fraction of at most $\delta$ a session. Then no holder is forced to lose more than $\delta$ in a session by the rule, and the notice needed is the $n$ in {eq:glide}.

$$n\ge \frac{\ln\!\big((1+\Pi)/(1+b)\big)}{\ln\!\big(1/(1-\delta)\big)}$$ {glide}

Proposition 5 is the constructive counterpart of the cliff. From a premium of 101%, a ceiling that falls 5% a session needs 11 sessions and one that falls 2% needs 26 ({fig:glide}). The rule is a value anchor phased in; it requires only that the exchange know a lagged NAV, which it does.

!FIG[fig_glide.png|w1|glide|Notice needed for a smooth return to a NAV anchor (Proposition 5): sessions over which a declining ceiling must be phased in, against the largest fall in the ceiling allowed per session, for three starting premiums.]

### Revealed and induced

Write the premium after the change as the path the latent premium would have followed under the law of motion that governed it before the change, plus a remainder:

$$\pi_{i,t}=\underbrace{E\big[\pi^{*}_{i,t}\mid\mathcal{F}_{T_0}\big]}_{\text{revealed}}+\underbrace{\pi_{i,t}-E\big[\pi^{*}_{i,t}\mid\mathcal{F}_{T_0}\big]}_{\text{induced, plus news}},\qquad t>T_0$$ {split}

where $\mathcal{F}_{T_0}$ is the information in prices and ceilings up to $T_0$. The first term is demand that the fence was hiding. The second is zero on average if the change of anchor did nothing but remove the fence. It is positive if the price-anchored band added demand, as a run of upper-limit days would if it drew attention or was extrapolated.

Nothing here says that a value anchor is the better rule. A fence binds when value itself moves faster than a stale base, which is the problem SEBI set out to fix in bullion ETFs; it prevents the price from showing a shortage that is real; and, as {sec:fence} shows, it closes the market to most trade. The claim is narrower: the two anchors bound different things, and where arbitrage is blocked the difference is the difference between a premium of 20% and one of 233%.
# Related Literature {#sec:lit}

### ETF pricing and suspended creation

ETF prices deviate from NAV most in funds that hold international or illiquid assets [@engle2006premiums; @petajisto2017inefficiencies], in part because NAV is stale when the home market is closed [@levy2013overreaction]. The deviation has been split into price discovery and transitory noise [@madhavan2016price], and creation flows have been shown to reveal non-fundamental demand [@brown2021etf]. Corrections for stale prices were first built for international mutual funds [@goetzmann2001day; @zitzewitz2003who]. Most of this work assumes creation is available; where it strains, as in bond funds in March 2020, prices and NAV part for days [@haddad2021when]. Johnson [@johnson2016essays] documents a premium puzzle in exchange-traded notes and identifies the issuer's suspension of new issuance as its main cause; a volatility note reached a premium near 90% in 2012 and lost more than half its price in two days when issuance resumed [@finra2012etn]. A US natural-gas fund that ran out of registered shares in 2009 [@kiplinger2009ung], a US-listed Egypt fund whose home market closed in 2011 [@etfcom2015egypt], a bitcoin trust with no redemption [@bitmex2020gbtc], Korea's leveraged oil notes of 2020 [@baek2021investment] and China's quota-bound ETFs on foreign indices [@reuters2024china; @caixin2026nov] are cases of the same kind ({fig:precedents}). In none of them did the anchor of a price limit change while the block stayed in place.

!FIG[fig_precedents.png|w1|precedents|Peak premiums in funds and notes whose supply was blocked, from the sources cited in {sec:lit}; the Infosys figure is an average, the others are peaks. The dotted line marks 20%. The products differ in what they hold and how value is measured, so the chart orders the episodes and does not compare them precisely.]

### Funds and shares behind investment barriers

Closed-end fund discounts move together and with small-investor sentiment [@lee1991investor], are larger where arbitrage is costlier [@pontiff1996costly], and can be an equilibrium price of liquidity [@cherkes2009liquidity]. Premiums on closed-end country funds fall when restrictions on foreign investment are loosened [@bonserneal1990international], reached 100% in the Germany Fund in 1989--90 [@hardouvelis1994moves], move with sentiment in the market where the fund trades [@bodurtha1995closed], respond to news mainly when it is salient [@klibanoff1998investor] and widen in currency crises [@chandar2000why]; later work relates them to measured barriers and liquidity [@chan2008market; @kim2010investment; @davies2017time]. A fund with the ticker CUBA jumped from a discount to a 70% premium on news about the country it was named after [@thaler2016behavioral]. Discounts close when a fund announces that it will open-end [@brauer1984open].

The same segmentation prices shares directly. Where foreigners may hold only a restricted class, that class trades at a premium in Finland, Thailand and Mexico [@hietala1989asset; @bailey1994foreign; @domowitz1997market], as models of mild segmentation predict [@errunza1985international]; in China, where it was domestic investors who were confined, the domestic class traded far above the foreign one [@fernald2002puzzles; @mei2009speculative]. Twin shares move with the market in which they trade [@froot1999how], cross-listed pairs diverge where arbitrage is impeded [@gagnon2010multi], and capital controls sustain gaps in Argentine depositary receipts [@auguste2006cross] and across national bitcoin markets [@makarov2020trading]. With supply fixed, price is set along a downward-sloping demand curve [@shleifer1986demand], steeper where the asset has no close substitute [@wurgler2002does]; recent estimates put the elasticity of demand for individual stocks near one and find flows into the aggregate market to move prices several times more [@chang2015regression; @koijen2019demand; @gabaix2021search]. The present case differs in direction, since the quota is on outbound investment by a domestic fund, and in having a daily published value and a daily price limit.

### Price limits, and bands around a fundamental

Limits are modelled as a substitute for margin [@brennan1986theory] and as a possible source of volatility when traders rush to trade ahead of them [@subrahmanyam1994circuit]. Empirically they delay price discovery and move volatility to later days [@kim1997price], and prices accelerate towards a limit as they approach it [@cho2003magnet; @hsieh2009magnet]; Sifat and Mohamad [@sifat2019circuit; @sifat2020survey] survey the field. In China large investors push stocks to the upper limit and sell the next day [@chen2019daily], and upper-limit hits draw first-time individual buyers [@seasholes2007predictable]. The widening of the limit on China's ChiNext board from 10% to 20% in 2020 has been studied as a natural experiment [@wan2022effect; @tang2023price; @zhang2023price]. All of these hold the anchor fixed at the previous close or settlement price and study assets without an observable fundamental. Laboratory markets, where the fundamental is known, still bubble when a rule limits the change in price from the previous period [@king1993robustness], which is what Proposition 2 would lead one to expect.

The closest theory of a band around a fundamental is not in this literature. In models of exchange-rate target zones a credible band changes behaviour inside it: the expectation of intervention at the edge holds the rate away from the edge [@krugman1991target; @svensson1992interpretation]. India's old band is the reverse case. It was credible, mechanical and wholly absorbed by rationing: there was no authority supplying units at the edge, so the price sat on the edge and the queue grew. The economics are those of a price ceiling without a supplier of last resort [@bulow2012regulated; @glaeser2003misallocation; @davis2011allocative], for which the econometrics of markets in disequilibrium were developed [@fair1972methods; @maddala1974maximum].

### Censored prices

A separate line treats limit days as censored observations of an equilibrium price [@kodres1993tests; @morgan1999limit; @wei2002censored], building on the Tobit model [@tobin1958estimation] and its dynamic forms [@lee1999estimation]. The statistical literature on censored autoregressions, written for detection limits in environmental series, supplies likelihood and quasi-likelihood methods for the case in which the lagged state is itself censored [@zeger1986regression; @park2007censored; @wang2018quasi; @wang2017carx]. This paper's case is easier in one respect and harder in another: the censoring point is observed exactly each day, but it is different each day and the process is close to a unit root.

### Bubbles, float and known end dates

When shorting is blocked, prices reflect the most optimistic holders [@miller1977risk] and can exceed every holder's own valuation because of the option to resell [@harrison1978speculative; @scheinkman2003overconfidence]; such bubbles come with heavy trading, are larger when float is small and deflate when float expands [@hong2006asset; @ofek2003dotcom]. Noise traders make the correcting trade risky [@delong1990noise; @shleifer1997limits; @gromb2010limits], and visible mispricing survives when shorting is costly [@lamont2003can; @mitchell2002limited]. Chinese put warrants that were certain to expire worthless traded at high prices with extreme turnover [@xiong2011chinese]. Account records from the same episode show buyers extrapolating from their own past gains after a single triggering event, and the authors reject the resale-option account [@pearson2021chinese]; see also [@li2021winners; @barberis2018extrapolation; @greenwood2014expectations]. A bubble cannot survive a known terminal date among rational traders with common knowledge [@tirole1982possibility], yet rational traders may ride one because they cannot coordinate their exit [@abreu2003bubbles; @brunnermeier2004hedge], and laboratory markets bubble even when the final value and date are known [@smith1988bubbles]. The April 2027 target gives this literature a field setting with a dated regulatory end, and Proposition 4 adds a constraint the models lack: the exit before the date is rationed by the lower limit.

### Attention and Indian investors

Individuals buy what catches their attention [@barber2008all], which search volume measures [@da2011search], and trading applications concentrate that attention on the day's largest movers [@barber2022attention]. Studies of Indian ETF pricing use autoregressive models on funds with working creation; the sample of Reddy and Dhabolkar [@reddy2020pricing] includes the Motilal Oswal Nasdaq 100 ETF in a period when its creation was open. Account-level research on Indian investors [@campbell2019rich; @anagol2021learning] shows what depository data could add to the question of who buys.

As of 14 November 2026 we found journalism and exchange notices on the 2026 episode and no academic study. The search was by web engine, which does not index working-paper archives or Chinese-language journals fully, so the claims of priority above are made to our knowledge.

# Hypotheses {#sec:hyp}

{tab:hyp} states three primary hypotheses, two supporting ones and one registered test, each with the outcome that would refute it, and adds what the first results say. The statements and refutation conditions are those of the second draft, unchanged. They were written after the price path of September and October 2026 was public and before any series from the panel existed; R1 is the only prediction about data that do not yet exist.

!TABLE[hyp|w2|Hypotheses, refutation conditions and first results|0.9,2.5,2.6,3.1|left]
| | Statement | Refuted if | First result ({sec:results}) |
|---|---|---|---|
| P1 Rule effect | From 7 September 2026 the premium of rationed funds rises against its own history and against domestic equity ETFs, and the break falls on the rule date | The break lies more than two sessions from 7 September in half or more of the US-linked funds; or domestic ETFs break by a similar amount; or the true date is outside the top 1% of placebo dates | Not refuted. Break on 7 September in three of four US-linked funds; domestic ETFs move by less than 0.3 log points; rank 2 of 950 dates. The Nasdaq 100 ETF does not fit |
| P2 Hidden premium | Before the change the premium was censored at the day's ceiling; after it, the realised premium exceeds a forecast of the latent premium | Two-sided. Realised premium inside the 90% forecast band: the rule revealed demand. Above it: more than revelation | Above the band in three funds, inside in two. Sensitive to the definition of a capped day in two of the three |
| P3 Supply and float | The premium falls after a creation, and across funds it is lower where float is larger | The mean change after creations is zero or positive; or the rank correlation among US-linked funds is positive | Not refuted, weakly supported. Mean change −0.5 log points (s.e. 1.0); rank correlation −1 on 46 of 47 sessions |
| S1 Limit-hit dynamics | After an upper-limit day: a positive return next day, more trades and smaller trades; premium changes follow the fund's own lagged return after controlling for turnover | No continuation; or a coefficient on the lagged return at or below zero | Lagged-return coefficient 0.39 (t = 2.9), against −0.10 before the change. Eight upper-limit days: too few to judge |
| S2 Feeders | Feeder funds shed ETF units when the premium is high | Feeder holdings rise in high-premium months | Not yet testable; feeder NAVs confirmed to carry the premium |
| R1 April 2027 | If a NAV anchor is applied on 1 April 2027 the premium collapses before the date in a run, not on the date in a jump | By branch; {sec:april} | Registered; no data yet |
!NOTE P: primary. S: supporting. R: registered. Tests are described in {sec:design}.

# Data and Measurement {#sec:data}

## The panel

The study covers the six India-listed ETFs that hold foreign equity ({tab:funds}), four domestic equity ETFs that faced the same change of band with creation open, and two bullion ETFs kept apart because their band differs. The panel has 15,528 fund-days on 1,454 sessions from 1 January 2021 to 13 November 2026, of which 7,870 are in the six funds [@yadav2026panel].

Daily prices, volume, number of trades and delivered quantity come from NSE's full bhavcopy, and BSE's price file adds the second exchange. NSE's daily security master carries two fields on which much of the analysis rests: the official price band for each ETF's next session and its units outstanding [@nse2026files]. The first gives the exact ceiling on each day. The second moves in multiples of the creation unit and so dates every creation and redemption; it is available from 5 February 2024 and is published in batches, at most a week after the change it records. Daily NAVs are from the Association of Mutual Funds in India [@amfi2026nav], together with the NAVs of ten feeder schemes. Every clean row can be traced to a raw file, and the pipeline rebuilds the panel from raw files with one command. Derived tables and code will be released; licensed data will not be redistributed.

Four things planned for the design are not yet in the panel: index futures and exchange rates at the Indian close, which need a terminal export; monthly feeder holdings; a search-interest series; and intraday prices for September. Securities-lending files have been sampled but not backfilled. The limitations that follow from each are stated where they bite.

!TABLE[funds|w2|The six funds on 13 November 2026|1.25,1.1,1.25,0.8,0.85,0.8,0.9,1.5,1.4|]
| Fund (NSE symbol) | Fund house | Index | Listed | Creation unit | Units, million | Value at NAV, Rs crore | Units created (+) and redeemed (−), creation units, Feb 2024 to Nov 2026 | Last change in units |
|---|---|---|---|---|---|---|---|---|
| MONQ50 | Motilal Oswal | Nasdaq Q-50 | Dec 2021 | 600,000 | 14.68 | 167 | +23, −4 | Created, Nov 2025 |
| MASPTOP50 | Mirae Asset | S&P 500 Top 50 | Sep 2021 | 550,000 | 168.62 | 1,089 | 0, −9 | Redeemed, Oct 2024 |
| MAFANG | Mirae Asset | NYSE FANG+ | May 2021 | 200,000 | 247.30 | 3,990 | +3, −30 | Redeemed, Jan 2025 |
| MAHKTECH | Mirae Asset | Hang Seng TECH | Dec 2021 | 800,000 | 193.71 | 298 | +3, −7 | Created, Jan 2025 |
| MON100 | Motilal Oswal | Nasdaq-100 | Mar 2011 | 250,000 | 504.13 | 13,104 | +129, −154 | Redeemed, Mar 2026 |
| HNGSNGBEES | Nippon India | Hang Seng | Mar 2010 | 25,000 | 23.59 | 999 | +377, 0 | Created, Oct 2024 |
!NOTE Units outstanding are from NSE's security master and NAV from AMFI [@nse2026files; @amfi2026nav]; value at NAV uses the last NAV known at the close of 13 November. Creation-unit sizes are from scheme documents; every change in units is an exact multiple. Dates of changes are those of the batch in which the exchange published them.

## Premium measures

The premium quoted in the press is the close over the NAV of the same date, less one. For a US-linked fund the denominator is fixed after the numerator ({fig:timing}). Let $a(t)$ be the date of the last NAV whose underlying market had closed before the Indian close on $t$: normally $t-1$ for a US-linked fund and $t$ for a Hong Kong fund. Fair value at the Indian close is

$$FV_{i,t}=NAV_{i,a(t)}\left(\frac{F^{IN}_{t}}{F^{cl}_{a(t)}}\right)^{\beta_i}\frac{S^{IN}_{t}}{S^{cl}_{a(t)}}$$ {fv}

where $F$ is the front index future and $S$ the rupee exchange rate, each observed at 15:30 Indian time on $t$ and at the underlying market's close on $a(t)$. In this draft the two ratios are set to one, so that $FV_{i,t}=NAV_{i,a(t)}$ and the dependent variable is the log premium over the last known NAV,

$$\pi_{i,t}=\ln\!\left(\frac{P_{i,t}}{NAV_{i,a(t)}}\right)$$ {pi}

The omitted term is the overnight move of the future and the rupee, with a daily standard deviation of about one per cent. It adds noise to $\pi$ on days when the price is free and none on days when the price sits at the limit, since the limit is known exactly. All premiums reported below are over the last known NAV unless they are called quoted.

## The ceiling and capped days

Let $U_{i,t}$ be the official upper limit for session $t$, read from the security master of the previous session. The ceiling on the premium is $c_{i,t}=\ln(U_{i,t}/NAV_{i,a(t)})$, the empirical counterpart of {eq:fence}. NSE's closing price is an average over the last half hour, so three indicators are kept: the day's high reached the limit; the close equals the limit; the last trade was at the limit. A fund-day is treated as capped when the close equals the limit or the last trade was at it, so that the market ended the day on the ceiling. The second draft defined a capped day by the day's high. The change was made on first sight of the data, because on about one in seven of the days on which the high touched the limit the close was below it and is a valid observation of the premium. Results under the earlier definition are reported beside the main ones.

## Units, rationing and turnover

A creation or redemption is a change in units outstanding, measured in creation units. Turnover is units traded on both exchanges over units outstanding; a rupee turnover would mix in the premium. Float is units outstanding valued at the last known NAV, gross of feeder holdings. {app:variables} lists the variables.

# Empirical Design {#sec:design}

## The rule effect (P1)

The cleanest observation is the first day. On 7 September 2026 US markets were closed for Labor Day, so the value of the US-linked funds could not move except through the rupee, and the band changed. Following the logic of single-event inference [@gelbach2013valid], each fund's return on 7 September is ranked within its own daily returns since January 2023. A least-squares search for one break in the level of each fund's premium [@bai1998estimating; @andrews1993tests] is run from January 2025. And an event statistic, the change in the mean premium from the five sessions before a date to the five sessions from it, treated funds less domestic ETFs, is computed for every date since January 2023 to place the true date in a placebo distribution. Intervals built for a small number of treated groups [@conley2011inference; @ferman2019inference] need more control funds than the four in the panel and are left for the next version, as is a synthetic control from Chinese funds on the same indices [@abadie2010synthetic].

## The hidden premium and the induced share (P2)

For each fund the latent premium is modelled on the sample from 6 February 2024 to 4 September 2026 as

$$\pi^{*}_{i,t}=\mu_i(1-\rho_i)+\rho_i\,\pi^{*}_{i,t-1}+\sigma_i\varepsilon_{i,t},\qquad \pi_{i,t}=\min\big(\pi^{*}_{i,t},c_{i,t}\big)$$ {car}

with $\varepsilon_{i,t}\sim N(0,1)$. The model is a Tobit with two features that the application requires. The censoring point is observed and differs every day. And the lag is the latent premium, not the observed one: what buyers would have paid yesterday, had they been allowed to, carries over to today. Over a run of capped days the lagged state is unobserved, so the likelihood integrates it out. Because the state is one-dimensional the integral is computed directly. If $f_{t-1}$ is the density of $\pi^{*}_{t-1}$ given the data to $t-1$, then on a capped day

$$f_t(u)\propto \mathbf{1}\{u\ge c_t\}\int \phi\!\left(\frac{u-\mu(1-\rho)-\rho v}{\sigma}\right)f_{t-1}(v)\,dv$$ {filter}

and the normalising constant is that day's contribution to the likelihood; on an uncapped day the state is observed and the density collapses to a point. The recursion is run on a grid of 0.4 log points.

The filtered density on 4 September is the estimate of the hidden premium on the last day of the fence. Simulating {eq:car} forward from it gives the forecast distribution of the latent premium in each later session. Let $\hat\Pi^{*}_i$ be the forecast mean of the premium $e^{\pi^{*}}-1$ averaged over sessions 16 to 40 after the change, and $\Pi_i$ the realised average over the same sessions. The induced share is

$$\iota_i=1-\frac{\hat\Pi^{*}_i}{\Pi_i}$$ {iota}

The first 15 sessions are left out because the price needs time to reach a high latent premium at 20% a day and because they contain the run-up and crash of one fund; session 40 is 4 November 2026. The window was fixed before any series was computed. The test is two-sided.

Critical values come from a parametric bootstrap of the whole procedure under the estimated null of pure revelation. For each fund, 299 latent paths are drawn from the fitted model over the fund's own sequence of ceilings and continued for 40 sessions; each is censored at those ceilings, the model is re-estimated on the censored path, and the gap between the continued path and the re-estimated forecast is recorded. Draws are kept only if the last pre-change day has the same capped status as in the data. The realised gap is compared with this distribution. Variants use the earlier definition of a capped day, a sample from January 2025, covariates (the fund's five-day NAV return, the five-day return of the Nifty and the change in units), and a model that ignores censoring.

## Does the estimator work?

Before it was taken to data the estimator was tested on simulated data in which the truth is known ({app:mc}). {tab:mc} reports the main results from 240 replications of each of three designs, in which about 35%, 60% and 80% of days are capped. Ignoring censoring understates the hidden premium by 10 to 18 percentage points and finds induced demand where there is none in 26% to 89% of replications. The censored estimator with the day's ceiling is nearly unbiased. Its forecast test has power of 90% to 95% against 30 log points of induced demand, but it over-rejects when most days are capped, which is why the application takes its critical values from the bootstrap.

!TABLE[mc|w1|Monte Carlo: the estimator on simulated data|2.9,1,1,1|]
| Share of days capped | 35% | 60% | 80% |
|---|---|---|---|
| >> A. Hidden premium on the last capped day: mean error, percentage points |
| Censoring ignored | −9.8 | −12.2 | −17.8 |
| Fixed ceiling | 2.7 | 3.0 | 1.6 |
| Moving ceiling | 1.3 | 1.3 | −0.7 |
| >> B. Root mean squared error, percentage points |
| Censoring ignored | 12.7 | 15.3 | 22.2 |
| Moving ceiling | 7.6 | 11.1 | 13.9 |
| >> C. Realised above the 90% band, % of replications |
| No induced demand: ignored | 26.2 | 66.7 | 88.8 |
| No induced demand: moving | 5.8 | 10.4 | 15.4 |
| Induced, 30 log points: moving | 89.6 | 91.7 | 95.0 |
!NOTE 240 replications per design; 400 pre-change sessions; forecast window sessions 16 to 40; nominal upper-tail size 5%. Simulated data; design in {app:mc}.

## Supply and float (P3)

Each dated change in units is a supply shock of known size. For every creation batch the change in the premium from five sessions before to ten sessions after is computed, and its ratio to the percentage change in units gives an elasticity. Two cautions apply. Batches are published up to a week after the creation, so the window may begin after the shock. And creations are not random: a fund house creates when it has room and a market maker asks, which is when the premium is high. The estimate is therefore a description. Across funds the relation between premium and float is tested with a rank correlation on each session after the change.

## Limit-hit dynamics (S1)

For the sessions after the change,

$$\Delta\pi_{i,t}=a_i+b_1\,TO_{i,t}+b_2\,d_{i,t}+b_3\,r_{i,t-1}+b_4\,r_{i,t-5:t-1}+\sum_{j=1}^{3}g_j H_{i,t-j}+\varepsilon_{i,t}$$ {s1}

with turnover $TO$, delivered share $d$, the fund's own return over the previous one and five sessions, and indicators $H$ for a day at the maximum upper limit $j$ sessions earlier. Resale-option models predict that premium and turnover rise together; extrapolation predicts a positive $b_3$ that survives the inclusion of turnover [@pearson2021chinese]. Errors allow for correlation across funds within a day and over four lags [@driscoll1998consistent].

## What the rule cost buyers

For each fund-day after the change, the amount paid above the old ceiling is

$$E_{i,t}=q_{i,t}\,\max\big(0,\ \bar P_{i,t}-1.2\,NAV_{i,t-2}\big)$$ {excess}

where $q$ is units traded on NSE and $\bar P$ the day's average traded price. Summed by fund and week it measures the transfer from buyers to sellers that the old ceiling would have prevented, without any claim about who they were. The amount paid above NAV replaces the ceiling by the last known NAV and adds BSE.

## Inference

There are at most six treated funds from three fund houses and one rule date. Standard errors clustered by fund are unreliable at this number, as is the wild cluster bootstrap [@mackinnon2018wild]. The paper therefore leads with the holiday observation, the rank of the true date among placebo dates and break dates by fund, and uses panel estimates only in support. Tests are few and were stated in advance, in the spirit of pre-analysis plans [@olken2015promises; @harvey2017presidential]; departures from the stated design are listed in {app:protocol}.
# Results {#sec:results}

All figures in this section are computed from the panel. Premiums are over the last NAV known at the close. Session 1 is 7 September 2026 and session 47 is 13 November.

## Six years in one figure

{fig:history} plots the premium of the six funds from January 2021. Four phases are visible. Until January 2022 creation was open and premiums averaged between 0.3% and 3.9% ({tab:desc}). When overseas subscriptions stopped in February 2022 the two funds whose houses were out of room moved: the Nasdaq 100 ETF's premium rose from 0.9% in the 20 sessions before to 9.5% on average in May 2022, and the Hang Seng fund reached 21.5% in April, touching the band. After SEBI allowed funds to resume within their frozen room on 21 June 2022, the Nasdaq 100 ETF's premium fell from 6.6% in the 20 sessions before to 1.5% in the 20 after. The Mirae Asset funds, whose house still had room, stayed within 1% of NAV throughout. This first episode is the barrier at work without the band binding: premiums appear when creation stops and go when it resumes, fund house by fund house.

The third phase begins in 2024. Mirae Asset stopped creation in January; in the 20 sessions after 1 April 2024 its FANG+ and S&P 500 Top 50 funds averaged premiums of 16.6% and 11.3%, from 3.4% and zero before. By 2025 the premiums of five funds were moving between zero and the ceiling, and by mid-2026 they were on it. The fourth phase is the subject of this paper.

!FIG[fig_history.png|w2|history|Premium over the last known NAV, six funds, January 2021 to 13 November 2026 (five-session moving average, log scale). The dotted line marks the level of about 20% at which the NAV-anchored band capped the premium. The shaded area is the period of the previous-close band.]

!TABLE[desc|w2|Premiums, limit days and trading, by period|1.4,0.75,0.75,0.8,0.75,0.85,0.85,0.75,0.75,0.8,0.8,0.75,0.75,0.8,0.8|small group]
| | Mean premium (%) | | | | Closed at upper limit (% of sessions) | | Turnover (% of units a day) | | Trades a day (median) | | Delivered share (%) | | Rupees per trade (median) | |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Fund | 2021 to Jan 22 | Feb 22 to 2023 | 2024 to 4 Sep 26 | After | 2024 to 4 Sep 26 | Last 120 | Before | After | Before | After | Before | After | Before | After |
| MONQ50 | 3.9 | 1.7 | 8.5 | 95.2 | 21.0 | 88.3 | 0.41 | 8.94 | 184 | 14,480 | 96.4 | 37.1 | 15,183 | 10,411 |
| MASPTOP50 | 0.3 | 0.1 | 12.9 | 50.6 | 40.1 | 90.8 | 0.08 | 0.86 | 238 | 11,058 | 96.9 | 50.0 | 13,085 | 7,907 |
| MAFANG | 0.4 | 0.3 | 14.1 | 42.3 | 51.8 | 92.5 | 0.10 | 0.47 | 586 | 19,991 | 94.4 | 47.1 | 26,888 | 11,799 |
| MAHKTECH | 1.0 | 1.1 | 14.5 | 29.1 | 40.2 | 77.5 | 0.34 | 0.39 | 1,673 | 4,807 | 90.3 | 67.3 | 7,584 | 2,693 |
| MON100 | 1.1 | 1.1 | 6.0 | 14.4 | 14.4 | 75.0 | 0.10 | 0.21 | 1,442 | 21,300 | 94.0 | 53.5 | 37,024 | 12,529 |
| HNGSNGBEES | 1.9 | 3.5 | 9.9 | 3.5 | 9.9 | 1.7 | 0.54 | 0.35 | 4,338 | 3,683 | 65.0 | 66.5 | 11,256 | 8,364 |
| NIFTYBEES | 0.1 | 0.0 | 0.1 | 0.0 | 0.0 | 0.0 | 0.26 | 0.36 | 40,606 | 62,191 | 61.6 | 63.0 | 35,697 | 32,868 |
!NOTE Premium over the last known NAV. 'After' is 7 September to 13 November 2026 (47 sessions). 'Closed at upper limit' counts sessions on which the close equalled the official upper limit or the last trade was at it; 'Last 120' is the 120 sessions to 4 September 2026. For the trading columns 'Before' is the 60 sessions to 4 September and 'After' the 47 sessions from 7 September; turnover counts both exchanges. NIFTYBEES is a domestic ETF shown for comparison.

## The fence in the data {#sec:fence}

{fig:fence} shows how the old band bound. Between February 2024 and 4 September 2026 the five funds other than the Hang Seng fund closed at the upper limit on 35% of fund-days. The share rose over time: Mirae Asset's FANG+ fund closed at the limit on more than half its sessions over the whole period, and in the last 120 sessions the five funds closed there on 85% of fund-days ({tab:desc}). The premium was nevertheless free for long stretches, which is what identifies the model of {sec:design}: every fund has quarters in which it closed at the limit on fewer than one session in ten.

The ceiling moved as Proposition 1 says. On days when the base was the NAV of two days earlier, the ceiling on the quoted premium of the four US-linked funds had a 5th percentile of 16.4% and a 95th of 23.7%, the two values that a two-day move in NAV of three per cent either way implies. On 4 September, the last day of the regime, five funds closed at the limit to the paisa, at quoted premiums from 19.0% to 19.6%.

The band rationed quantity as well as price. Pooling the five funds over the same period, median turnover on days that closed at the limit was 0.14% of units, against 0.34% on other days; the median number of trades was 1,349 against 4,540; and 94% of traded units were delivered, against 67%. On 4 September the Nasdaq Q50 ETF traded 95 times, every trade at the limit price of Rs 141.93, and 0.12% of its units changed hands. Under the fence a new buyer could rarely buy at all. The quoted premium of 19% was the price of a unit for the few who obtained one, not the price at which the market cleared.

!FIG[fig_fence.png|w2|fence|The NAV-anchored band as a ceiling, February 2024 to 4 September 2026. (a) Share of sessions in each quarter on which a fund closed at the official upper limit. (b) The ceiling on the quoted premium, $U_{i,t}/NAV_{i,t}-1$, for the four US-linked funds on days when the base was the NAV of $t-2$.]

## The rule effect (P1) {#sec:p1}

{tab:p1} and {fig:p1} report the tests. On 7 September the Nasdaq Q50 ETF rose 15.6%, the largest daily rise among its 912 sessions since January 2023 and 10.6 standard deviations of its daily return. The S&P 500 Top 50 and FANG+ funds rose 7.6% and 5.7%, their third and sixth largest. The Nasdaq 100 ETF rose 3.6% and the Hang Seng TECH fund 4.3%; the latter's NAV fell 0.9% that day, since Hong Kong was open. The Hang Seng fund, which was not at the ceiling, and four domestic ETFs fell by less than one per cent.

A search for a single break in the level of the premium from January 2025 selects 7 September in four of the five funds that had been at the ceiling. The exception is the Nasdaq 100 ETF, whose break is in April 2026, when it rose to the ceiling. The breaks found in domestic ETFs are smaller than 0.3 log points. The event statistic, the five-session change in the mean premium of the five funds less that of three domestic ETFs, is 13.0 log points at 7 September. Among 950 dates since January 2023 only 8 September, whose window is the same event, is higher; the largest value before September 2026 is 4.7.

P1 is not refuted on any of its three conditions. But the rule did not raise every rationed fund. Comparing sessions 16 to 40 with the 60 sessions before the change, the premium rose by 45, 23, 19 and 9 log points in four funds and fell by 8 in the Nasdaq 100 ETF. A fund that had closed at the ceiling on three sessions in four traded lower once the ceiling was removed. {sec:p2} returns to this.

!TABLE[p1|w2|The rule effect: holiday return, break date and placebo rank|1.4,1.0,1.0,0.9,1.3,1.5,1.1,1.3|]
| Fund | Return on 7 Sep 2026 (%) | Rank among 912 sessions | In s.d. of daily return | Break date | Premium before and after the break (log points) | Placebo rank of 7 Sep, of 950 | Change, sessions 16–40 less the 60 before (log points) |
|---|---|---|---|---|---|---|---|
| MONQ50 | 15.6 | 1 | 10.6 | 7 Sep 2026 | 11.5, 65.7 | 7 | +44.9 |
| MASPTOP50 | 7.6 | 3 | 5.1 | 7 Sep 2026 | 16.6, 40.8 | 2 | +23.4 |
| MAFANG | 5.7 | 6 | 3.6 | 7 Sep 2026 | 17.4, 35.1 | 9 | +19.4 |
| MAHKTECH | 4.3 | 26 | 2.1 | 7 Sep 2026 | 16.5, 25.5 | 33 | +9.0 |
| MON100 | 3.6 | 15 | 2.8 | 7 Apr 2026 | 5.2, 16.3 | 51 | −8.2 |
| HNGSNGBEES | −0.4 | 569 | −0.2 | 14 Jul 2026 | 11.6, 4.0 | 474 | −3.7 |
| NIFTYBEES | −0.8 | 813 | −1.0 | 10 Jun 2026 | 0.1, 0.0 | 250 | 0.0 |
| JUNIORBEES | −0.6 | 733 | −0.6 | 21 Mar 2025 | 0.2, 0.1 | 56 | 0.0 |
| BANKBEES | −0.8 | 787 | −0.9 | 29 Jul 2026 | 0.0, −0.1 | 72 | 0.0 |
!NOTE Returns are log returns of the closing price, in per cent. Rank 1 is the largest rise since January 2023. The break date is the least-squares estimate of one break in the level of the log premium from January 2025 to 13 November 2026, with 10% trimming. The placebo rank is the rank of 7 September 2026 among all dates since January 2023 for the fund's five-session change in mean premium; for the five funds pooled, less domestic ETFs, the rank is 2.

!FIG[fig_p1.png|w1|p1|The rule effect. (a) Each fund's return on 7 September 2026, a US holiday (diamond), against its daily returns since January 2023 (dots). (b) The event statistic of {sec:design} computed at every date: the five-session change in the mean premium of the five funds that had been at the ceiling, less that of domestic ETFs.]

## The hidden premium: revealed and induced (P2) {#sec:p2}

### The first free close

Four of the five funds that had closed at the ceiling on 4 September did not touch even the initial 10% upper limit on 7 September. Their closes that day are market-clearing prices (Proposition 3): 23.2% above NAV for the Nasdaq 100 ETF, 25.4% for the Hang Seng TECH fund, 26.4% for the FANG+ fund and 28.7% for the S&P 500 Top 50 fund, against ceilings of 18.4% to 19.2% on the previous session. The fence had been hiding between four and ten percentage points in each. Among the three on US indices the amount is ordered inversely to float. The Nasdaq Q50 ETF is the exception. It closed at the maximum limit on the first day, touched it on the next two, and had its first free close on session 4, at 71.7%.

### The model

{tab:p2} reports the censored autoregression. Persistence is high, between 0.976 and 0.990, and ignoring the censoring lowers it in every fund, as the simulations predict. The model puts the hidden premium on 4 September at 22% to 36%, with 90% intervals that are narrow where the closing run at the ceiling was short and wide where it was long.

The first free closes test the model on data it did not see. In all four funds that traded freely on the first day, the realised premium lies inside the model's 90% interval for the hidden premium: 23.2% against an interval of 21.8% to 41.8%, 25.4% against 20.4% to 27.9%, 26.4% against 18.9% to 26.7%, and 28.7% against 23.2% to 55.3%. A model that took the ceiling for the premium would have put all four at 19%. For the Nasdaq Q50 ETF the first free close of 71.7% is above the interval of 22.8% to 47.6%: either that fund's hidden demand was beyond anything its pre-change path suggests, or demand arrived within the first three sessions.

!TABLE[p2|w2|Hidden premium, forecast and realised premium|1.35,0.75,0.7,0.95,1.5,1.0,1.55,0.9,0.75,0.8,1.15|small]
| Fund | Capped (% of days) | $\hat\rho$ | Premium, 4 Sep (%) | Hidden premium, 4 Sep (%) [90% interval] | First free close (%) (session) | Forecast, sessions 16–40 (%) [90% bootstrap band] | Realised, sessions 16–40 (%) | p-value, upper tail | Induced share | Verdict (and with capped days defined by the high) |
|---|---|---|---|---|---|---|---|---|---|---|
| MONQ50 | 21.8 | 0.988 | 19.4 | 32.9 [22.8, 47.6] | 71.7 (4) | 27.7 [9.5, 45.5] | 88.5 | <0.01 | 0.69 | Above (above) |
| MASPTOP50 | 41.7 | 0.983 | 19.2 | 36.4 [23.2, 55.3] | 28.7 (1) | 30.8 [12.2, 49.9] | 51.8 | 0.04 | 0.41 | Above (inside) |
| MAFANG | 53.8 | 0.981 | 18.4 | 22.4 [18.9, 26.7] | 26.4 (1) | 21.8 [7.5, 39.3] | 45.7 | 0.01 | 0.52 | Above (inside) |
| MAHKTECH | 41.8 | 0.976 | 19.2 | 24.0 [20.4, 27.9] | 25.4 (1) | 21.1 [8.7, 34.3] | 31.1 | 0.12 | 0.32 | Inside (inside) |
| MON100 | 15.0 | 0.990 | 19.2 | 30.3 [21.8, 41.8] | 23.2 (1) | 25.6 [7.6, 38.4] | 10.5 | 0.92 | — | Inside (below) |
| HNGSNGBEES | 10.3 | 0.966 | 2.5 | not capped | 3.1 (1) | 7.4 [0.3, 15.4] | 3.8 | 0.73 | — | Inside (inside) |
!NOTE Censored autoregression {eq:car} fitted to 641 sessions from 6 February 2024 to 4 September 2026, by fund. 'Capped' is the share of those sessions on which the close equalled the limit or the last trade was at it. The hidden premium is the mean and 5th to 95th percentiles of the filtered density on 4 September. The first free close is the first session after the change on which the high did not reach the initial upper limit. The forecast is the mean premium over sessions 16 to 40 from 20,000 simulated paths; the band and p-value are from 299 parametric bootstrap replications of the whole procedure under pure revelation. The induced share is {eq:iota}. The last column repeats the verdict with plug-in bands when a capped day is one whose high reached the limit.

!FIG[fig_p2.png|w1|p2|Revealed and induced. For each fund: the premium at the close of 4 September 2026 (bar), the model's 90% interval for the hidden premium that day (light band), the first free close after the change (circle), the bootstrap 90% band for the forecast of the latent premium over sessions 16 to 40 (dark band) and the realised average (diamond).]

### Sessions 16 to 40

Over sessions 16 to 40 the Nasdaq Q50, S&P 500 Top 50 and FANG+ funds averaged premiums of 88.5%, 51.8% and 45.7%. Each lies above the bootstrap band for the forecast of the latent premium, with upper-tail p-values below 0.01, of 0.04 and of 0.01, and induced shares of 0.69, 0.41 and 0.52. The Hang Seng TECH fund, at 31.1%, lies inside its band. The Nasdaq 100 ETF, at 10.5%, lies in the lower part of its band, with a lower-tail p-value of 0.08; the Hang Seng fund, never capped at the end, is inside as it should be. Under the null the plug-in band would reject in the upper tail in 2% to 10% of bootstrap draws against a nominal 5%, so the calibration matters for the marginal cases and not for the Nasdaq Q50 ETF.

The verdict for two funds is fragile. If a capped day is defined by the day's high, as in the second draft, the model assigns the FANG+ fund a hidden premium of 48% in place of 22%, because its last 192 sessions then form one unbroken capped run, and the FANG+ and S&P 500 Top 50 funds fall inside their bands. Starting the sample in January 2025 or adding covariates leaves the main verdicts unchanged except that the S&P 500 Top 50 fund is inside its band with covariates. The first free closes side with the main definition: 26.4% for the FANG+ fund is at the edge of the main model's interval and far below 48%.

Read together, the two measures give a consistent account. The fence hid a premium a few points above the ceiling in four funds, and it was revealed on the first day. What followed in the three small funds on US indices, a further 16 to 23 points in two and far more in the third, arrived after the fence was gone and cannot be attributed to demand that the pre-change data show waiting behind it. In the terms of {eq:split} it is induced demand or news. NAVs fell slightly over the window, exchanges issued cautions on sessions 14 and 21, and no quota was raised, so the news that arrived was, if anything, adverse. The fund with the largest float went the other way.

!FIG[fig_episode.png|w2|episode|The six funds through the change of anchor, 25 August to 13 November 2026: premium over the last known NAV by session (log scale). The vertical line marks the change; the shaded area is the window of sessions 16 to 40; dotted lines mark the exchanges' cautions of 25 September and 7 October. Triangles in (a) mark sessions on which the Nasdaq Q50 ETF reached the maximum upper or lower limit.]

## Anatomy of one fund

{fig:episode} and {fig:band} trace the Nasdaq Q50 ETF. Its close rose from Rs 141.93 to Rs 164.09 on the first day, with NAV unchanged, and reached Rs 396.30, 233% above NAV, on session 9. On 21 September it traded as high as Rs 471.99 and closed at Rs 317.04, exactly 20% below the previous close; 34.6% of its units changed hands on the two exchanges. Two more lower-limit days followed. On 24 September it traded between Rs 164.01 and Rs 241.52, in 89,662 trades, with 42.5% of units changing hands and 18% of traded units delivered. No news, exchange action or fund-house statement has been reported as a trigger for the fall. The premium then rose again and stood at 101% on 13 November. In 47 sessions the high reached the maximum upper limit on seven and the maximum lower limit on three; outside this fund the maximum upper limit was reached once, by the S&P 500 Top 50 fund on the day of the Nasdaq Q50 ETF's peak, and the lower never.

The other funds moved with it in part. Daily premium changes of the S&P 500 Top 50 and FANG+ funds have correlations of 0.77 and 0.60 with those of the Nasdaq Q50 ETF after the change, and the former's change has a correlation of 0.36 with the Nasdaq Q50 ETF's change of the day before. Across the five funds the mean pairwise correlation of premium changes is 0.37, about what it was before the change, when a common ceiling produced it mechanically.

!FIG[fig_band.png|w1|band|Motilal Oswal Nasdaq Q50 ETF, 15 sessions before the change to 13 November 2026: close and daily range, the official band at its widest, the ceiling that the old rule would have set, and the last known NAV.]

## Supply and float (P3)

Units outstanding record 40 creation batches and 36 redemption batches in the six funds between February 2024 and March 2026 ({tab:funds}, {fig:units}). None has occurred since March 2026, and no unit has been created since November 2025, although one creation unit of the Nasdaq Q50 ETF bought at NAV and sold at the close of 18 September would have yielded about Rs 16.7 crore.

The record contradicts the common account that creation has been shut since 2022 or 2024, and it shows how the rationing worked. Motilal Oswal's two funds created units worth Rs 622 crore at NAV and redeemed units worth Rs 653 crore. The cumulative net position of the two funds together never moved more than Rs 184 crore from its starting point and ended Rs 30 crore below it: room freed by redemptions, mostly in the large fund, was reused for creations, some of them in the small one. Mirae Asset's three funds lost Rs 84 crore net and created almost nothing after April 2024, in line with its notice that direct creation had stopped. The frozen-room rule of 2022 is visible in the unit counts.

!FIG[fig_units.png|w1|units|Units outstanding (steps, left axis) and premium over the last known NAV (right axis) for Motilal Oswal's two funds, February 2024 to November 2026. The vertical line marks the change of anchor.]

{tab:p3} gives the event study. The premium fell by 0.5 log points on average from five sessions before a creation batch to ten after, with a standard error of 1.0. Among the 21 creations made when the premium was above 5% the fall is 2.3 points (standard error 1.4), and 71% of the changes are negative. For the Nasdaq Q50 ETF, whose creations raised units by 15% on average, the fall is 1.5 points, an elasticity of about −0.1. The largest creation in the sample, which raised that fund's units by 55% in the week to 29 July 2024, was followed by a fall in its premium from 17.6% to 3.5%. After redemptions the premium rose by 1.7 points (standard error 0.6). The signs are those of P3 and the creation estimate is not distinguishable from zero. It should not be read as a demand elasticity: creations are batch-dated up to a week late, they respond to the premium, and when the price sits at the ceiling a fall in latent demand does not show.

One pattern was not expected. On five occasions units were redeemed at NAV while the fund traded 5% to 18% above it: four batches in the Nasdaq 100 ETF between December 2024 and April 2025, worth Rs 195 crore, and one in the FANG+ fund. A holder gave up at NAV units for which the exchange was quoting more. A large holder that needed cash, such as a feeder fund meeting redemptions, could not have sold that many units on an exchange where a few hundred trades a day took place at the limit; the files do not say who it was.

Across the four funds on US indices the ordering of premium and float is exact. On 46 of the 47 sessions after the change the rank correlation of premium and float is −1: the Nasdaq Q50 ETF, with Rs 167 crore of float, has the highest premium and the Nasdaq 100 ETF, with Rs 13,104 crore, the lowest ({fig:float}). With four funds that is a description, and the two Hong Kong funds, with small floats and modest premiums, show that float is not sufficient. Demand is also needed.

!TABLE[p3|w1|Premium around changes in units|2.6,0.6,1.0,1.1,0.8,0.9|]
| Event | n | Premium before (%) | Mean change (log points) | s.e. | Share negative (%) |
|---|---|---|---|---|---|
| All creations | 40 | 6.1 | −0.5 | 1.0 | 65 |
| Creations at a premium above 5% | 21 | 10.2 | −2.3 | 1.4 | 71 |
| Creations, MONQ50 | 12 | 10.0 | −1.5 | 2.3 | 75 |
| All redemptions | 36 | 1.1 | +1.7 | 0.6 | 31 |
!NOTE Batches of changes in units outstanding in the six funds, February 2024 to March 2026. The change is in the log premium from five sessions before the batch date to ten sessions after. Batch dates are publication dates and may follow the creation by up to a week.

!FIG[fig_float.png|w1|float|Premium and float. Premium over the last known NAV on 13 November 2026 (filled) and at the first free close after the change (open), against units outstanding valued at NAV, gross of feeder holdings.]

## Trading after the change (S1)

Trading changed character ({tab:desc}, {fig:trading}). The median number of trades a day rose 78-fold in the Nasdaq Q50 ETF, 46-fold in the S&P 500 Top 50 fund, 34-fold in the FANG+ fund and 15-fold in the Nasdaq 100 ETF, and did not rise in the Hang Seng fund. The delivered share fell from above 90% to between 37% and 67%, and the median trade became smaller in every fund. The Nasdaq Q50 ETF's units turned over 4.2 times in 47 sessions, against 0.15 times in the 47 sessions before. Heavy trading in the fund with the smallest float and the highest premium is what models of speculative trading under short-sale constraints describe [@scheinkman2003overconfidence; @hong2006asset].

{tab:s1} reports regression {eq:s1}. After the change the coefficient on the fund's own previous-day return is 0.39 (t = 2.9); it is unchanged by turnover and the delivered share, neither of which is significant, and is 0.26 (t = 2.4) without the Nasdaq Q50 ETF. Before the change the same coefficient is −0.10 (t = −3.0): premium changes reverted. The change of sign is what extrapolation predicts and what a pure revelation of fixed demand does not. The day-after-limit indicators add little, with one exception: the day after a close at the maximum lower limit the premium falls a further 11 log points. There were only eight days at the maximum upper limit, seven of them in one fund; the next-day return averaged 2.3% and was positive on four. The supporting hypothesis about upper-limit days cannot be judged on that number.

Whether these funds could be sold short is still not established. Lending files sampled for 224 dates show all five funds on the eligible list in November 2026 and a single day of lending in the six funds in 2026, 5,000 units of the FANG+ fund on 13 November.

!TABLE[s1|w2|Changes in the premium after the change of anchor|2.2,1.1,1.1,1.1,1.2,1.2|]
| | (1) | (2) | (3) | (4) Without MONQ50 | (5) Before the change |
|---|---|---|---|---|---|
| Own return, previous session | 0.389 (0.133) | 0.390 (0.134) | 0.223 (0.129) | 0.258 (0.106) | −0.097 (0.032) |
| Own return, previous five sessions | −0.064 (0.081) | −0.059 (0.073) | −0.058 (0.074) | −0.054 (0.054) | 0.021 (0.013) |
| Turnover | | −0.137 (0.137) | −0.021 (0.195) | −1.399 (0.857) | −0.056 (0.066) |
| Delivered share | | −0.017 (0.053) | −0.020 (0.036) | 0.000 (0.047) | −0.014 (0.003) |
| At maximum upper limit, 1 session earlier | | | 0.004 (0.031) | | |
| At maximum upper limit, 2 sessions earlier | | | 0.030 (0.029) | | |
| At maximum upper limit, 3 sessions earlier | | | −0.003 (0.012) | | |
| At maximum lower limit, 1 session earlier | | | −0.110 (0.048) | | |
| Fund-days | 235 | 235 | 235 | 188 | 3,205 |
| Within R-squared | 0.13 | 0.14 | 0.19 | 0.09 | 0.02 |
!NOTE Dependent variable: daily change in the log premium over the last known NAV. Columns (1) to (4): the five funds that had been at the ceiling, 7 September to 13 November 2026. Column (5): the same funds, 6 February 2024 to 4 September 2026. Fund fixed effects. Driscoll–Kraay standard errors with four lags in parentheses.

!FIG[fig_trading.png|w1|trading|Trading before and after the change. (a) Nasdaq Q50 ETF: units traded on both exchanges as a share of units outstanding, and the share of traded units delivered. (b) Median number of trades a day in the 60 sessions before and the 47 after.]

## What it cost

{tab:money} adds up the rupees. In the 47 sessions from 7 September the six funds traded Rs 5,391 crore on the two exchanges in 4.1 million trades. Buyers paid Rs 1,668 crore more than the NAV of what they bought. Of that, Rs 929 crore was paid above the price that the old band would have allowed on the day, Rs 558 crore of it in the Nasdaq Q50 ETF, a fund that holds Rs 167 crore of shares. These are transfers from buyers to sellers; the files do not say who either was, and a buyer who sold later at a higher price lost nothing.

The stock of value above NAV tells a different story from the flow. The six funds were already priced Rs 3,784 crore above NAV on 4 September, under the fence. The figure peaked at Rs 5,968 crore on 18 September, fell to Rs 2,808 crore on 30 October and was Rs 4,941 crore on 13 November ({fig:money}). Most of it sits in the two largest funds at moderate premiums: the Nasdaq 100 ETF and the FANG+ fund account for Rs 4,056 crore of the last figure and the Nasdaq Q50 ETF for Rs 169 crore.

!TABLE[money|w1|Rupees traded and paid above NAV, 7 September to 13 November 2026|1.5,0.9,0.9,0.9,0.9,0.9|]
| Fund | Traded | Paid above NAV | Paid above old ceiling | Units turned over (times) | Value above NAV, 13 Nov |
|---|---|---|---|---|---|
| MONQ50 | 1,510 | 771 | 558 | 4.20 | 169 |
| MASPTOP50 | 722 | 262 | 155 | 0.40 | 545 |
| MAFANG | 1,358 | 415 | 202 | 0.22 | 1,918 |
| MAHKTECH | 78 | 17 | 4 | 0.18 | 120 |
| MON100 | 1,545 | 197 | 9 | 0.10 | 2,138 |
| HNGSNGBEES | 177 | 6 | 0 | 0.17 | 51 |
| Six funds | 5,391 | 1,668 | 929 | | 4,941 |
!NOTE Rs crore, except turnover. 'Traded' is value on NSE and BSE. 'Paid above NAV' is units traded times the excess of the day's average price over the last known NAV, when positive. 'Paid above old ceiling' is {eq:excess}, NSE only. 'Value above NAV' is units outstanding times the excess of the close over the last known NAV.

!FIG[fig_money.png|w1|money|The rupees involved. (a) Market value of the six funds above NAV, by session. (b) Amount paid above the ceiling that the old rule would have set, by week.]

# April 2027: Registered Tests and the Transition {#sec:april}

Paragraph 4.4 of the circular makes the previous day's NAV the base from 1 April 2027, subject to exchanges and fund houses resolving operational difficulties. If any NAV anchor is applied, Proposition 4 holds: a fund trading more than 20% above the anchoring NAV must jump into the band on the first day. {tab:april} applies it to the premiums of 13 November. Four funds are exposed. At those premiums the switch would take at least Rs 1,643 crore off their market value in a day, Rs 1,120 crore of it in the FANG+ fund.

!TABLE[april|w1|Exposure to a NAV anchor at the premiums of 13 November 2026|1.5,0.9,0.8,0.9,0.9,0.8,1.0|]
| Fund | Premium (%) | Excess $x$ | Forced fall, band widened (%) | Forced fall, first trade (%) | Sessions $K$ | Value lost, at least (Rs crore) |
|---|---|---|---|---|---|---|
| MONQ50 | 101.2 | 0.52 | 40.4 | 45.3 | 3 | 136 |
| MASPTOP50 | 50.0 | 0.22 | 20.0 | 26.7 | 2 | 327 |
| MAFANG | 48.1 | 0.21 | 19.0 | 25.7 | 1 | 1,120 |
| MAHKTECH | 40.1 | 0.16 | 14.3 | 21.5 | 1 | 60 |
| MON100 | 16.3 | −0.03 | none | 5.4 | 0 | 0 |
| HNGSNGBEES | 5.1 | −0.13 | none | none | 0 | 0 |
!NOTE The excess is {eq:x}. Forced falls are {eq:cliff} with $b=0.2$ and, for the first trade of the day, $b=0.1$. $K$ is {eq:sessions}: the number of lower-limit sessions needed before the switch to avoid a jump. Value lost is units outstanding times the close times the forced fall with the band widened.

A holder who is certain of the switch should not hold through it. The switch is a target, however, and may be delayed, so a premium above the future ceiling is not by itself evidence of irrationality. The tests concern how the excess over that ceiling moves, and they are fixed here before any observation from 2027 exists. The text is that of the second draft, unchanged, and will be filed on a public registry before 31 December 2026.

### Definitions

The excess of a fund on a day is

$$x_{i,t}=\ln\!\left(\frac{P_{i,t}}{1.2\,NAV^{pub}_{i,t}}\right)$$ {x}

where $NAV^{pub}_{i,t}$ is the last NAV published before that close. An exposed fund is one of the six with $x>0$ at the close of the last session of December 2026. The switch date $T$ is the first session on which NSE's official band for a fund is centred on a NAV of any lag, read from the security master. An event is a dated publication by SEBI, the Reserve Bank of India, NSE, BSE, the Association of Mutual Funds in India or the three fund houses about the band's base, the overseas limits or creation in these funds. Each event is logged with its time and classed, before prices are examined, as premium-reducing (anchor confirmed or advanced, limits raised, creation resumed) or premium-preserving (delay, exemption).

### Branches

A: a NAV anchor is applied to these funds on or before 30 April 2027. B: a delay or exemption is announced before $T$. C: the limits are raised, or an exposed fund's units rise by at least one creation unit, before $T$.

### Predictions

1) Run, not cliff (A). For every exposed fund $x\le 0$ at the close of $T-1$. The prediction is refuted for a fund if $x>0$ then, in which case the fall arrives as a jump on $T$.
2) Path (A). Between 1 January and $T-1$ each exposed fund has at least one session whose low equals the official lower limit, and its median $x$ over the last 10 sessions before $T$ is below its median over sessions 11 to 40 before $T$.
3) News (all branches). Across events and exposed funds, $x$ falls from the close before an event's first tradable session to the close two sessions later for premium-reducing events and rises for premium-preserving ones. An exact sign test is applied to the pooled changes.
4) Trading (A, B). Units traded over units outstanding in the 20 sessions before $T$, or before the announcement in branch B, exceed the ratio in sessions 21 to 60 before.
5) Supply (C). After units rise, $x$ falls, and by more where the percentage rise in units is larger.

All five predictions will be reported whatever the result.

### Known when written

The registration text was written on 11 November 2026 with press premiums to 4 November and exchange files for two dates. On 15 November, when this draft was completed, the panel to 13 November had been examined. No price after 13 November 2026 has been seen. This paragraph will be updated on the day of filing.

### Why a run is the prediction

Before $T$ a falling price can lose at most 20% a day, and on lower-limit days sellers may find no buyers, as on 21 and 22 September 2026. Holders who intend to leave must therefore leave early, and each has reason to leave before the others. The alternative is that the premium survives to $T-1$ because holders doubt the date, cannot coordinate [@abreu2003bubbles] or do not know the rule. The first prediction separates the two.

### The transition

The results bear on how the switch is made. {sec:fence} shows that a binding NAV band closes the market to most trade, and Proposition 4 that its return is a cliff. Three designs avoid both. A declining ceiling (Proposition 5) phased in over 11 to 26 sessions removes the jump from a premium of 100% while forcing no more than 2% to 5% a session. A trigger on the premium, of the kind China and Korea use, halts or moves to a call auction any fund whose price strays from the last known NAV beyond a threshold and leaves ordinary days untouched. And room to create units, for these funds alone, removes the premium at its source: in the sample the largest creation, of 55% of one fund's units, took its premium from 18% to 4%. The units outstanding of the four exposed funds are worth Rs 5,545 crore at NAV, which gives the scale of the room at issue. Which of these is preferable is outside the scope of this paper. That the choice is between them, and not between two bases for a band, is its main implication for policy.

# Threats to Validity and Limitations {#sec:limits}

### Fair value

The dependent variable is the premium over the last known NAV. It omits the move in the underlying market and the rupee between the previous foreign close and the Indian close. The omission does not affect limit days or the first free closes by more than about a point, but it adds noise to the censored model near the ceiling and to daily premium changes in {tab:s1}. The next version uses index futures and exchange rates at 15:30, with unit and estimated betas.

### Few funds

Six funds from three fund houses cannot support claims about ETFs in general. The contribution is a well-identified case, and inference is chosen accordingly. Four domestic controls are too few for the interval methods the design names; more are being added to the panel.

### A shared demand shock

Chinese ETFs on US technology indices also traded at high premiums in the autumn of 2026 [@caixin2026nov]. A global rush into US technology shares could raise premiums in both countries. It cannot explain a break on a US holiday or a fall in the largest fund, but it could contribute to sessions 16 to 40. The Chinese comparison is not yet in the panel.

### The latent premium is a model

The split in {eq:split} is relative to a law of motion estimated before the change. If that law would have changed in September 2026 for reasons unrelated to the rule, the change is counted as induced. The model is a first-order autoregression close to a unit root, fitted to a period in which the regime of creation itself changed, and the verdict for two funds depends on how a capped day is defined. The first free closes are the check, and they support the main definition; they do not make the forecast band for later sessions any narrower.

### Revelation may take time

A buyer who had stopped bidding at a ceiling that never cleared might have returned only after seeing prices move. Such demand existed before the change in a sense that neither the model nor the first free close can detect, and both would class it as induced. The distinction the paper can draw is between demand that was bidding at the ceiling and demand that arrived afterwards.

### Endogenous supply

Creations respond to the premium and are dated late. The event study describes what followed them and does not identify the slope of demand. Redemptions from sister schemes of the same fund house, which free room without reference to the ETF's premium, are a candidate instrument once the houses' monthly disclosures are in the panel.

### Who buys

Daily data show delivered share, trade count and trade size, not investor identity. The paper makes no statement about the rationality of any group. Feeder holdings, and therefore net float and the supporting hypothesis S2, await monthly portfolios.

### Short selling

Whether units could be borrowed in 2026, and whether brokers allowed short sales within the day, remains to be established from a full run of the lending files.

### The rule may change

SEBI may alter the April target or the limits. The branches in {sec:april} treat each as an event; they do not remove the uncertainty.

### Researcher knowledge

P1 to P3, S1 and S2 were written after the September and October price path was public and before the panel existed. This draft changed one definition after seeing the data and says so. R1 is a prediction in the full sense.

# Conclusion {#sec:concl}

India's international ETFs offer an unusual setting: an asset with a published value, a regulatory barrier to arbitrage that can be dated from exchange records, and a price limit whose anchor moved once and is due to move again. The argument of this paper is that the anchor is not a technical detail. Anchored to value, a limit bounds mispricing and rations trade; anchored to price, it bounds only the speed at which mispricing can grow; and a return from the second to the first forces a jump unless it is phased in.

The first results from the panel support the argument and qualify the story usually told. The old band was a binding ceiling under which little traded. The break in premiums is on the day the anchor changed, a day on which value could not move. But the ceiling had been hiding a premium of only a few points in four of five funds, and that premium was revealed in a single session. The far larger premiums that followed in the small funds on US indices came after the fence was gone, with heavy trading and with price changes that followed their own past, while the largest fund traded below the level at which it had been pinned. The change of anchor did not uncover a 100% premium. It removed the rule under which one could not form.

For regulators the case carries a general point. A rule written for assets whose price can outrun a stale value was applied to assets whose price is not tied to value at all, and the property that had kept them near value was a side effect of the rule that was replaced. Where creation can be blocked, a limit on price changes is not a limit on mispricing, and some instrument anchored to value, whether a phased ceiling, a halt or a call auction, has to do that job, or the block has to be lifted. The next version of the paper will add fair value from futures, feeder holdings and the Chinese comparison. The version after April 2027 will report the outcome of the registered tests, whichever way they fall.

# Appendix A: Protocol Timeline and Departures from the Design {#app:protocol}

{tab:protocol} lists, for each element of the design, the date it was fixed and what had been seen at that date. Four departures from the design of the second draft were made in this draft. (i) A capped day is defined by the close or the last trade being at the limit, not by the day's high; both are reported. (ii) Fair value is the last known NAV; futures are not yet available. (iii) The model of {eq:car} has no covariates in its main form, because the rupee and search-interest series are not yet in the panel; a variant with the covariates that are available is reported. (iv) Intervals for few treated groups were not computed. The window of sessions 16 to 40, the refutation conditions and the registered text are unchanged.

!TABLE[protocol|w1|Elements of the design and outcomes already seen|2.2,1.0,3.1|left]
| Element | Date fixed | Outcomes seen at that date |
|---|---|---|
| Fair-value measure | 10 Nov 2026 | NAV timing check for one fund |
| P1 to P3, S1, S2; window of sessions 16 to 40 | 11 Nov 2026 | Press premiums, 4 Sep to 4 Nov; units outstanding on five dates |
| Estimator, Monte Carlo check, bootstrap calibration | 12 Nov 2026 | As above, plus exchange files for 4 Sep and 9 Nov; simulated data |
| R1 and the tests of {sec:april} | 11 Nov 2026; registration pending | As above; no price after 9 Nov |
| Capped-day definition; first free close; tests run | 15 Nov 2026 | Panel to 13 Nov 2026 |

# Appendix B: Monte Carlo Design {#app:mc}

The latent log premium follows $\pi^{*}_t=\mu(1-\rho)+\rho\pi^{*}_{t-1}+\sigma\varepsilon_t$ with $\rho=0.985$, $\sigma=0.02$ and $\mu\in\{0.10,0.20,0.30\}$, started from its stationary distribution. Daily log changes in value are $n_t\sim N(0,0.012^2)$ and the ceiling is $c_t=\ln 1.2-(n_t+n_{t-1})$. For $t\le 400$ the observed premium is $\min(\pi^{*}_t,c_t)$. A replication is kept if the last pre-change day is capped and between 10% and 95% of pre-change days are capped. For $t>400$ the observed premium is $\pi^{*}_t+\delta$ with $\delta\in\{0,0.3,0.6\}$. Three estimators are compared: least squares on the observed premium; maximum likelihood for {eq:car} on the premium over the band's base with a fixed ceiling of $\ln 1.2$; and maximum likelihood with the moving ceiling. The likelihood is computed by the recursion {eq:filter} on a grid and maximised by the Nelder–Mead method from the least-squares estimates. The forecast band is the 5th and 95th percentiles of the average log premium over sessions 16 to 40 across simulated paths, with parameters drawn from their estimated asymptotic distribution. Seeds are fixed and the code is in the project repository.

# Appendix C: Variables {#app:variables}

!TABLE[vars|w1|Variables used in this draft|1.6,3.3,1.4|left]
| Variable | Definition | Source |
|---|---|---|
| Close, high, low, average price | NSE daily prices; the close is the average of the last half hour | NSE bhavcopy |
| Last known NAV | Last NAV whose underlying market had closed before 15:30 on $t$ | AMFI, calendars |
| Premium $\pi$ | ln(close / last known NAV) | Derived |
| Quoted premium | Close over same-date NAV, less one | Derived |
| Band low, band high | Official band for session $t$, initial and widest | NSE security master |
| Ceiling $c$ | ln(band high / last known NAV) | Derived |
| Limit indicators | High at the initial limit; at the maximum limit; close equal to the limit; last trade at the limit; for each side | Derived |
| Units | Units outstanding, daily from 5 Feb 2024 | NSE security master |
| Unit event | Signed change in units over the creation-unit size, by batch | Derived |
| Float | Units times last known NAV | Derived |
| Turnover | Units traded on NSE and BSE over units outstanding | NSE, BSE |
| Delivered share, trades, trade size | Share of traded quantity delivered; number of trades; value per trade | NSE bhavcopy |
| Excess $x$ | ln(close / (1.2 × last published NAV)) | Derived |
| Paid above old ceiling | Units traded × max(0, average price − 1.2 × NAV of $t-2$) | Derived |

# Appendix D: Dated Events {#app:events}

!TABLE[events|w1|Chronology|1.2,5.1|left]
| Date | Event |
|---|---|
| Apr 2008 | Industry limit on overseas investment by mutual funds set at USD 7 billion [@lawasia2026] |
| 3 Jun 2021 | Limit per fund house raised to USD 1 billion [@bs2021limit] |
| 18 Jan 2022 | Motilal Oswal suspends issuance in its two Nasdaq ETFs [@bs2022motilal] |
| 1 Feb 2022 | Fresh overseas subscriptions stop across the industry [@pti2022resume] |
| 21 Jun 2022 | Funds may resume up to each house's room as of 1 February 2022 [@pti2022resume] |
| 8 Feb 2023 | Reserve Bank of India: no proposal to raise the limit [@bs2023rbi] |
| 5 Jan 2024 | Mirae Asset stops direct creation in its three overseas ETFs [@mirae2024addendum] |
| 1 Apr 2024 | Pool for foreign-listed ETFs full [@zerodha2024etf] |
| 5 Jan 2025 | Motilal Oswal pauses instalment plans in its Nasdaq 100 feeder [@et2025motilal] |
| Nov 2025 | Last creation of units in any of the six funds (panel) |
| 13 Feb 2026 | SEBI consultation paper on base price and bands [@sebi2026cp] |
| 15 Jun 2026 | SEBI circular: base is the previous close; NAV base targeted for 1 April 2027 [@sebi2026circular] |
| 4 Sep 2026 | Last session of the NAV-anchored band; five of six funds close at the upper limit (panel) |
| 7 Sep 2026 | Previous-close anchor in force; US markets closed [@nse2026circular] |
| 18 Sep 2026 | Nasdaq Q50 ETF closes 233% above the last known NAV (panel) |
| 21–23 Sep 2026 | Nasdaq Q50 ETF: three sessions at the maximum lower limit (panel) |
| 25 Sep 2026 | NSE and BSE press releases cautioning investors [@nse2026press; @bse2026press] |
| 7 Oct 2026 | NSE repeats its caution [@reuters2026nse] |
| 13 Nov 2026 | Six funds priced Rs 4,941 crore above NAV (panel) |
| 1 Apr 2027 | Target date for a base of the previous day's NAV [@sebi2026circular] |

# Acknowledgment and Disclosure {#nonum}

!SMALL Literature search, analysis code and drafting were assisted by an AI system (Claude, Anthropic); the author is responsible for the content. The author holds no position in the funds studied and will not trade them during the study. No funding was received. Nothing in this paper is investment advice.

# References {#nonum}

!REFERENCES
