# Reconciliation approach

This note explains how the demo turns hand-kept Excel books into a normalized
database, for someone reviewing the approach rather than the code. All data in
the repository is synthetic; measured results are in the generated report
(`docs/index.html`) and the README table, both produced by `make demo`.

## Principles

1. **Account for every row.** Each row below a table header ends up in exactly
   one place: a core table, `etl.merged_rows` (a duplicate of a loaded row), or
   `etl.rejects` with a reason code. "Loaded" is verified against the core
   tables, which record the book, sheet and row each record came from.
2. **Never guess silently.** When the data does not settle a question (which
   way round a date is, which of two disagreeing copies is right, whether two
   similar names are one supplier), the row is rejected or queued for review
   with the evidence, not resolved by a default.
3. **Prefer a missed merge to a wrong one.** Two suppliers merged by mistake mix
   their payables history and are hard to separate later; a supplier left
   duplicated can be merged in the ERP by someone who knows the business.
4. **Reloads are safe.** Loading the same books twice changes nothing, so a
   fixed book can be reloaded without cleaning the database first.
5. **The database enforces the rules too.** Python and SQL validation reject bad
   rows, and the schema's CHECK, foreign-key and unique constraints refuse
   anything that slips through.

## The four reconciliation problems

### Supplier names

The same supplier appears as "Walker Textiles Co. Ltd", "WALKER TEXTILES CO.
LTD", "Walker Textiles Company Limited", "Wlaker Textiles" and
"WalkerTextiles". Matching runs in two stages:

- **Rules** reduce each spelling to a key: case, accents and punctuation
  removed, "&" read as "and", dotted initials joined ("S.A.R.L." to "sarl"),
  common abbreviations expanded ("Intl", "Svcs", "Tex"), a trade word glued to
  the name split off, legal forms dropped ("Ltd", "Limited", "Co.", "SARL",
  including misspellings such as "Lmiited"). Spellings with the same key are
  one supplier.
- **Fuzzy matching** (rapidfuzz) compares the remaining keys, most frequent
  first, each against the anchor spelling of every supplier found so far. The
  score is the lower of two similarities: the whole name, and the identifying
  part (the name minus generic trade words such as "textiles" or "cloth
  merchants"). Taking the lower score stops two failure modes seen in early
  versions: different suppliers matching because they share long trade words
  ("Rose Cloth Merchants" and "Rhodes Cloth Merchants"), and different
  suppliers matching because they share a surname ("Fowler & Sons Textiles"
  and "Fowler & Sons Haberdashery").

Scores at or above the auto-merge threshold merge automatically. Scores between
the review and auto-merge thresholds stay separate and go to a review list with
the nearest supplier and the score.

**Choosing the thresholds.** `make calibrate` generates supplier spellings
from five seeds that are not the demo seed and sweeps the auto-merge threshold
from 70 to 100, measuring pairwise precision and recall against the generated
truth. The auto-merge threshold is the one with the highest recall among those
with pooled precision of at least 0.99. The review threshold is the lowest
5-point score band in which at least half of the suggestions are right: below
it, a reviewer would see more wrong suggestions than right ones. Tuning on the
seed being scored would have inflated the reported numbers, so the scored seed
is never used for calibration, and a unit test fails if the configured
thresholds stop matching what calibration picks.

The limit of name-only matching is planted in the data on purpose: two
different suppliers whose surnames differ by one letter. No string similarity
can tell that apart from a typo; a real migration would add a second signal
(tax ID, phone, bank account, or two spellings used on the same PO number).

### Dates

Values arrive as real Excel dates, serial numbers (a date column formatted as a
number), ISO text, text months ("5 Mar 2019", "March 5, 2019") and slashed dates
typed day-first or month-first. Slashed dates are the problem: "05/03/2019" is
5 March to one clerk and 3 May to another.

The rule, per date column:

1. A value is unambiguous if it is a real date, a serial, ISO, a text month, or
   a slashed date with one part above 12 (or both parts equal). Unambiguous
   slashed dates are evidence: "25/03/2019" says the sheet is day-first.
2. An ambiguous value takes its sheet's convention when the sheet's evidence
   points one way only.
3. A sheet with no evidence uses the whole workbook's evidence, again only if
   one-sided (the convention belongs to whoever kept the book).
4. A sheet with evidence both ways was kept by two people. Books are kept in
   date order, so the ambiguous value takes whichever reading falls between the
   nearest unambiguous dates above and below it, with a week of slack, if
   exactly one reading does.
5. Anything still undecided is rejected as `AMBIGUOUS_DATE`.

Every date must also fall within the book's year, plus 62 days either side for
rows carried over from the previous December; a year typo such as 2091 is
rejected as `DATE_OUT_OF_RANGE`.

### Currencies and amounts

Amounts are typed as numbers or as text: "125,000", "125 000", "125,000/=" (an
East African way of writing a whole amount), "RWF 125,000", "125,000 Frw",
"$1,250.50", "(1,250)". Separator rule: with both "," and ".", the right-most is
the decimal point; a lone "," is a thousands separator when followed by groups
of three digits and a decimal comma when followed by one or two.

The currency of a row comes from, in order: a currency column; a marker inside
the amount text; the column header ("Amount (Frw)"). A currency column that
contradicts the amount text is rejected as `CURRENCY_CONFLICT`, and a row with
no currency anywhere as `UNKNOWN_CURRENCY`. Amounts are stored in their original
currency and converted to RWF with a monthly rate table. In this demo the rate
table is synthetic, generated by a formula and labelled as such in the database,
because the point is the mechanism, not the rates.

### Duplicates

Rows are repeated within a sheet (keyed twice), across sheets (a December
payroll sheet copied for corrections) and across books (late-December rows
repeated at the top of next year's book; a quarter re-keyed by someone else in a
"revised" book, with different spellings and date formats). Because the
duplicates are written differently, de-duplication runs after parsing, on
normalized values.

Each domain has a natural key: PO number and product for purchase lines,
receipt and product for sales, voucher number for stock movements, month and
employee for payroll. Within each key, rows are ranked by book, sheet and row
with a window function; the first is kept. Later rows with the same values are
merged into it and recorded in `etl.merged_rows`; later rows with different
values are rejected as `CONFLICTING_DUPLICATE`, with a pointer to the row they
disagree with.

One detail matters here: "first" is decided by sorting book names, and the
database's default `en_US` collation ignores punctuation, so it sorts
`purchases_2019_revised.xlsx` before `purchases_2019.xlsx`. The book columns use
the `C` collation so the original book wins.

## Checks after the load

The report runs the reconciliations a month-end close depends on, as SQL over
the loaded tables:

- goods received against purchase-order lines (received in full, short, over,
  ordered but not received, received with no loaded order line);
- running stock balance per product (a window function); the generated true
  books never go negative, so every negative balance points at a rejected or
  missing movement;
- units sold per product per month in the sales book against the stock card.

These are the questions an accountant asks before closing a month, answered by a
query instead of by cross-checking spreadsheets.

## What is not covered

The target is a normalized schema shaped like an ERP's, not Odoo's own models or
import API. Payroll has no tax rules. Reloads update and insert but never delete.
The data is synthetic and was written by the same person who wrote the parsers,
so the scores show the pipeline handles the anticipated mess, not that it would
score the same on real books. See the README's Limitations section.
