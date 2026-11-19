# The lower-bar charter (sixth charter)

Status: **owner-signed 2026-11-19, in session ("Sign as proposed"). The trial has not been
built or scored yet.** Next step: the builder's check in §9. Numbers marked *(proposed)* were
the drafter's suggestions; the owner signed them unchanged, and §8 lists the signed values.

Asked for by the owner on 2026-11-19: "take the findings from this test and move forward with
the next steps, just modify the rules to fit the 6%."

## 1. What this does, in one paragraph

The 90-day study ended at a 6.17% median yearly return on paper against a signed 15% line. The
owner judges 6% good enough to keep going. This charter writes that down as **new rules for a
new test**, and names the next step: a paper trial that holds every basket until both markets
pay out, so the 6% is checked against real losses for the first time. It stays paper-only.

## 2. What it does not do

- **It does not turn the 90-day study into a pass.** That study was scored on lines signed on
  2026-10-14, before the result, and it reads NO-GO. Its record (pull request #126) is not
  edited. Rules changed after a result apply to the next test, not the last one.
- **It adds no way to place orders.** `ATLAS_TRADING_ENABLED` stays off and `/health` keeps
  reporting `trading_enabled=false` (`AGENTS.md`, hard invariant).
- **It trusts no unproven pair.** `REVIEW_REQUIRED` and wording-blocked pairs are followed on
  paper only; none becomes an approved label.
- **It reverses one earlier owner ruling, and says so:** on 2026-08-20 the owner dropped the
  execution track and made paper-only "indefinite". Signing this reopens the *question* of
  execution. It does not reopen execution.

## 3. The rule changes (signed, see §8)

The old study needed five things. Fitting the 6% means touching four of them, not one.

| # | Old rule | Day-90 reading | New rule *(proposed)* | Note |
| --- | --- | --- | --- | --- |
| 1 | At least 10 verified opportunities per 30 days | 226.5 | unchanged | passes |
| 2 | Median yearly return on locked money at least 15% | 6.17% | **at least 6%** | In the eight full reports it ranged 3.0% to 6.9%, so a 6% line would have failed most weeks |
| 3 | Median basket at least $500 | $52.80 | **at least $50** | Without this change the answer stays no. Ranged $48 to $144 |
| 4 | "Few" one-leg-only fills (no number ever signed) | 0 of 2,951 | **at most 2%** | Measured on 31% of the data |
| 5 | Three people who would pay for alerts | never measured | **dropped** | Only fits if this is for the owner's own use, not a product |

With all four signed, the old data would read "continue". That reading is **not claimed**: the
lines would have been drawn around numbers already seen. §4 is the honest version.

## 4. The next step: a hold-to-settlement paper trial, on new data

**The gap in the old study.** Of 740 verified opportunities, 28 fully cleared the checker. The
other 712 were pairs whose wording differs between venues. The 6.17% assumes all of them pay
out the same way on both sides. Nobody measured how often they don't, and one that doesn't can
lose the whole basket.

**The trial.**

- Starts 2026-11-19, the signing date. Only opportunities first seen on or after it count.
- Same frozen families, verifier and opportunity definition as the 90-day study.
- Only pairs the owner could use from the US (the study's "tradeable venue" subset, Polymarket
  US, where the day-90 reading was 11.85% on 440 opportunities).
- A paper bankroll of **$2,000** *(proposed)*. Each opportunity takes a paper basket at the
  size the books actually showed, at most 5% of the bankroll. Money in a basket is locked and
  cannot be reused until both sides settle.
- Each basket is closed at what the two venues actually paid, after fees.
- **60 days** of new baskets *(proposed)*, to 2027-01-18, then up to 30 more days waiting for
  payouts, to 2027-02-17. A basket still open then counts as locked money with zero profit.
- Baskets are worked out by fixed rule from what the monitor recorded, so the scorer can be
  built after signing without choosing anything by hand. It is built and tested before any
  trial result is read.

## 5. Pass lines, fixed before any basket is opened (all must hold)

1. **Enough to judge:** at least 30 baskets settled on both sides *(proposed)*. Fewer reads
   INCONCLUSIVE, which counts as a fail, as in the market-making charter.
2. **Real return:** yearly return on the average locked money, after fees and after every
   losing basket, is at least **6%**.
3. **Beats doing nothing:** that return is above the 3-month US Treasury bill yield read on
   the signing date and written in §8 (4.26%).
4. **Losses are survivable:** the worst single basket loses no more than 5% of the bankroll,
   and all losing baskets together lose less than half of the total winnings *(proposed)*.
5. **Worth the effort:** at least **$10 a month** of paper profit (owner's number, §8): total
   profit after fees and losses on all baskets opened in the 60 days is at least **$20**.

## 6. What will not count

- Any number from before the signing date, including the 6.17%.
- Changing a line, the bankroll or the window after the first basket opens.
- Leaving out a basket for any reason after it opens. Losers and stuck ones stay in.
- Gaps on venues the owner cannot use.
- A good percentage on a handful of dollars: line 5 exists for that.

## 7. What a result means

- **PASS:** the owner may ask for a *separate* execution charter. Per `AGENTS.md` that needs
  its own module, an explicit promotion gate, a default-off flag and human authorization.
  Nothing switches on by itself, and this charter builds none of it.
- **FAIL or INCONCLUSIVE:** the cross-venue question is closed and the monitor can stop.
- **Either way** the monitor, keep-awake and watchdog must stay on until the trial ends. That
  settles "what stays on" for now.

Drafter's honest odds, written before any data: **a pass is less likely than not.** Reasons:
the return sat under 6% in most weekly reports; baskets near $50 make line 5 hard at any
sensible dollar figure; and settlement mismatches have never been counted. This is what
Atlas's own tests suggest, not investment advice.

## 8. Signed values

Signed by the owner on 2026-11-19, in session. The first four were "Sign as proposed"; the
fifth had no proposal and was the owner's own choice the same day.

1. Return line: **6%** a year
2. Basket line: **$50**
3. Paper bankroll: **$2,000**
4. Three-paying-users rule: **dropped**
5. Dollars a month that make it worth it: **$10** (about what 6% a year earns on $2,000 fully
   in use)

Treasury bill yield: **4.26%** (13-week, coupon equivalent, for 2026-11-18, the newest row in
the Treasury's daily bill-rate table when read on 2026-11-19; that day's row was not yet
published).

Still fixed by this charter and not by the owner's five numbers: 30 settled baskets, the 60 and
30 day windows, the 5% stake cap and the two loss limits in §5 line 4. They were in the draft
the owner signed.

## 9. Left to the builder, after signing

- Check whether `atlas/paper.py` and `atlas/settlement*.py` already hold a paper basket until
  both venues settle and lock its money, or whether that needs building (tests first).
- A weekly trial report that shows counts and locked money but **not** the running return, so
  nobody is tempted to stop early on a good week.
- An amendment note in `docs/NINETY_DAY_STUDY.md` pointing here, cut only after #126 merges.
