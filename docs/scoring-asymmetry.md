# scoring.py: the asymmetry, and what the collected data says so far

Written 2026-07-29 night, rewritten 2026-07-30 after running the reasoning
against the 10,361 fills already in `data/rig.db`. The rewrite matters: the
first version proposed an arbitrary 4-to-1 penalty weight, and the Frechet
bounds already in the design spec are a sharper tool that needs no fudge factor.

## 1. The asymmetry, stated correctly

Start with the thing that is true regardless of any model. When you sell a
parlay at price `q`, a hit costs you the same $1 per contract no matter what you
quoted. The quote only sets how much premium you keep when it misses. Your
downside per contract is fixed and your upside is the quote. The price moves one
side of the ledger.

What does **not** hold on its own is the idea that overpricing earns a cushion
while underpricing gives money away. In expected value those are symmetric:

    EV per $1 of notional (selling) = q - p_true

Two cents rich earns two cents, two cents cheap loses two. Same magnitude,
opposite sign. So the asymmetry has to come from somewhere else.

It comes from three places.

**Adverse selection on fills.** You only trade when the quote is attractive
enough for the taker to hit it. Price too rich and you mostly do not get filled,
so the cost is a trade that never happened, bounded at zero. Price too cheap and
you get filled every time, including by takers who know something you do not.
Underpricing correlates with being wrong. Overpricing costs optionality;
underpricing costs cash, selectively, in the cases where the model was most
mistaken.

**Directional model bias.** Computing fair value as the product of leg
probabilities assumes independence. Real parlay legs are usually positively
correlated, and for positively correlated events the joint probability exceeds
the product:

    P(A and B) = P(A) * P(B) + Cov(A, B),   Cov > 0 under positive correlation

So the independence price systematically understates the true probability, and
selling at it means selling too cheap. The error has a direction; it is not
symmetric noise.

**Payout ratio.** You collect a few cents and owe 100 on a hit. Linear EV treats
a two cent error as a two cent problem. Risk of ruin does not, because loss per
contract dwarfs gain per contract.

## 2. Why the Frechet bounds beat a weighted absolute deviation

The design spec already calls for three reference prices per fill. They are not
three competing estimates. They are a bound and an interior point:

    frechet_floor(p)   = max(0, sum(p) - (n - 1))     lowest joint probability possible
    independence(p)    = prod(p)                       joint probability if independent
    frechet_ceiling(p) = min(p)                        highest joint probability possible

The true joint probability must lie in `[floor, ceiling]` for **every** possible
dependence structure. That turns the pricing question into a position question,
and it makes the asymmetry mathematical rather than a matter of taste:

| where the fill sits | what it means for the maker selling it |
|---|---|
| above the ceiling | positive EV under every dependence structure, since `q > ceiling >= p_true` |
| between independence and ceiling | positive correlation is being priced in, plausible, sign of EV depends on the real correlation |
| at independence | independence is being assumed, which is usually a directional error |
| between floor and independence | negative correlation is being priced, needs a reason |
| below the floor | negative EV under every dependence structure, a guaranteed loser |

So the loss function does not need an invented weight ratio. Distance below the
floor is a hard error. Distance above the ceiling is a hard edge. Position inside
the interval is the interesting middle, and the natural normalization is where
the fill sits between the bounds:

    position = (fill - floor) / (ceiling - floor)      when ceiling > floor

That number is unitless, comparable across parlays with wildly different leg
counts, and it does not require choosing how much worse under is than over. It
falls out of the geometry.

The `score_fill` contract in the plan already returns the pieces this needs. The
one thing worth adding when you write it is the interval width, `ceiling - floor`,
because a fill inside a two cent interval and a fill inside a forty cent interval
are not comparable facts.

## 3. What the collected data says, and why it is not a finding yet

Run against `data/rig.db` on 2026-07-30 (5,865 markets, 10,361 trades, 1,750 leg
quote snapshots, 765 settled markets), scoring every fill on the mid of its legs:

    max leg spread   scored  skipped   above ceiling   below floor
              1.00    10361        0    2586  25.0%    1159  11.2%
              0.20     3632     6729     688  18.9%     877  24.1%
              0.10     3257     7104     605  18.6%     811  24.9%
              0.05     3084     7277     586  19.0%     776  25.2%
              0.02     2277     8084     511  22.4%     690  30.3%

Roughly a fifth of fills price above the Frechet ceiling, which would mean free
money for the seller, and that does not go away when you filter for tight leg
books. **Do not put that number in front of Bill.** Three measurement problems
sit in front of it, and each one could produce the effect on its own:

1. **Leg quote quality.** 30.5% of the leg snapshots have an empty book, quoted
   `0.00 / 1.00`. Taking the mid of an empty book manufactures a 0.5
   "probability" out of no information. 38.2% have spreads wider than ten cents,
   where the mid is barely more meaningful.
2. **Timing.** Each leg has exactly one snapshot in this database, taken a median
   of seven minutes and up to fifteen minutes away from the fill it is being
   scored against. These are live MLB markets whose legs resolve during that
   window. A leg quoted 0.99 now may have been 0.40 at fill time, which inflates
   the computed ceiling in one direction systematically.
3. **Leg counts.** Parlays in the tape run from 2 to 74 legs. At 74 legs the
   independence price is effectively zero and the floor is exactly zero, so the
   whole interval collapses onto the ceiling. Those rows need their own treatment
   rather than being averaged in.

Worked example of problem 1, ticker
`KXMVESPORTSMULTIGAMEEXTENDED-S20265100B457692-D51E1F0765B`, a six leg fill at
0.804 whose computed ceiling was 0.225. Three of its legs were quoted
`0.99 / 1.00`, meaning already effectively resolved, one was an empty
`0.00 / 1.00` book scored as 0.5, and the remaining two carried 29 and 28 cent
spreads. Nothing about that fill is measurable with those inputs.

## 4. What that implies for the build order

- Scoring stays pure math, as designed, but `score_fill` should carry the leg
  quote quality alongside its answer: worst leg spread, count of empty books, and
  the interval width. A fill scored on garbage legs must be labeled, not dropped
  silently and not averaged in.
- The report must publish coverage next to any rate: how many fills were
  scorable at a given spread gate, out of how many. A headline rate without a
  denominator is not a measurement.
- Timing is a collector problem, not a scoring problem. Either capture leg
  quotes at fill time, or let the phase 2 candlestick backfill supply leg prices
  at the fill's timestamp. The second is already planned and is the honest fix.
- Only once fills are scored against leg prices from the right moment does the
  under-versus-over question become empirical. Then the fill curve as a function
  of distance from the bounds can be measured instead of assumed.

## 5. Open questions to answer with data

- What fraction of parlay legs have a real two sided market at fill time, by
  series and by leg count? That number caps everything else the rig can say.
- Once timing is fixed, how often do fills genuinely sit outside the Frechet
  interval? That is Bill's question and it is currently unanswered.
- What is the realized correlation between legs of the parlays Kalshi actually
  lists? The 765 settled markets in the database are the start of that answer.
- Where does the fill probability curve sit as a function of distance above the
  ceiling? That curve is the entire cost of overpricing.
