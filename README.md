# FitFindr

> ### 👋 Start here
>
> **New to this repo? Read [RUNNING.md](RUNNING.md) first** — setup, every
> command, and what to do when something breaks.
>
> Once `python test.py` passes:
>
> ```bash
> python app.py listings --full -n 6     # read the data (Milestone 1)
> python app.py fields                   # what you can filter on
> python app.py ask 'vintage graphic tee under $30'
> ```
>
> **The rest of this file is the submission.**

---

<!-- ═══════════════════════ UNIT 3 — THE BUILD ═══════════════════════ -->

## What This Does

<!-- Three or four sentences: what a user asks for, and what they get back. -->

---

## Tool Inventory

### `search_listings`

- **What it does:** Filters the 40 listings in `data/listings.json` by size and price, scores what is left by keyword overlap with the description, and returns the matches best-first. It does not call the model.
- **Inputs:**
  - `description` (str) — keywords for the item, e.g. `"vintage graphic tee"`. Filler words ("looking", "for", "a", …) are ignored and simple plurals are folded, so "sneaker" matches "sneakers".
  - `size` (str | None, default None) — a size to filter on, or None for no size filter. Matched case-insensitively against **whole size tokens**, not substrings: `"M"` matches `"S/M"` and `"M/L"` but not `"XL"`; `"S"` does not match `"US 9"`. `"medium"` is read as `"m"`; `"8"` and `"US 8"` both match `"US 8"`; a bare waist number matches a W-size (`"30"` matches `"W30 L30"`). Listings sized "One Size" match every requested size.
  - `max_price` (float | None, default None) — price ceiling in dollars, inclusive, or None for no ceiling.
- **Returns:** A `list[dict]` of at most `config.SEARCH_RESULT_LIMIT` (10) listings, best match first. Each dict is one unmodified listing with the keys `id` (str), `title` (str), `description` (str), `category` (str), `style_tags` (list[str]), `size` (str), `condition` (str), `price` (float), `colors` (list[str]), `brand` (str or None), `platform` (str). A listing is included only if it passes both filters **and** contains at least half of the description's keywords (rounded up). A keyword scores 3 if it is in the title, 2 if in `style_tags`, 1 if in the description, category, colors or brand. Ties go to the cheaper listing.
- **When it has nothing:** An empty list, `[]`. Never `None`, never an exception. This includes a description with no usable keywords (empty, or only filler words).

### `suggest_outfit`

- **What it does:** Asks the model for one or two outfits built around a listing, using pieces from the user's wardrobe.
- **Inputs:**
  - `new_item` (dict) — one listing dict, in the shape `search_listings` returns.
  - `wardrobe` (dict) — a dict with an `items` key holding a `list[dict]`; each wardrobe item has `id`, `name`, `category`, `colors`, `style_tags`, and `notes` (str or None). The list may be empty.
- **Returns:** A non-empty `str` of plain text: one or two outfits, each naming wardrobe pieces by their `name` with a sentence or two on why it works.
- **When it has nothing:** With an empty wardrobe (`items` is `[]`), it still calls the model and returns a `str` of general styling advice for the item — two short paragraphs using common pieces — instead of wardrobe-specific outfits. If the model itself returns an empty response, it returns a fixed one-line fallback string naming the item. It never returns `""`.

### `create_fit_card`

- **What it does:** Asks the model for a short caption someone would post about the find.
- **Inputs:**
  - `outfit` (str) — the outfit suggestion text from `suggest_outfit`.
  - `new_item` (dict) — the listing dict the caption is about.
- **Returns:** A `str` caption of two to four sentences. The prompt asks for the item, its price and its platform once each, plus at least one piece it is worn with. The wording differs between runs (`TEMPERATURE` is 0.9).
- **When it has nothing:** If `outfit` is empty or only whitespace, it does **not** call the model and returns the fixed string `"No fit card: there's no outfit suggestion to write a caption from. Run suggest_outfit first."` It does not raise. If the model returns an empty response, it returns a one-line fallback built from the title, price and platform.

---

## Planning Loop

**Branch rule:** If `search_listings` returns an empty list, put a message in `session["error"]` that names what the user could change, and stop — `suggest_outfit` and `create_fit_card` are not called and `session["fit_card"]` stays `None`. Otherwise take the first result, put it in `session["selected_item"]`, and go on to `suggest_outfit`.

**Where it lives:** `agent.py::run_agent`

**How the query is parsed:** Regex, no model call — `agent.py::parse_query`. One pattern takes the price ceiling: a number after "under", "below", "less than", "up to", "max" or "at most", or after a bare `$`. A second takes the size: the word after "size" (`size M`, `size 8`, `size US 8.5`). Whatever is left once those two are cut out is the description. A size written without the word "size" (`"medium tee"`) is not picked up as a size; it stays in the description.

**What moves through the session:** Each time round the loop, `run_agent` reads the session, finds the first step that hasn't happened, runs it, and writes the result back before going round again. `trace.check_iterations` runs on every pass.

1. `query` → `parse_query` → `parsed` (`description`, `size`, `max_price`)
2. `parsed` → `search_listings` → `search_results`, and `searched` is set to `True`
3. **Branch** on `search_results`: empty → `error` is set and the loop returns
4. `search_results[0]` → `selected_item`
5. `selected_item` + `wardrobe` → `suggest_outfit` → `outfit_suggestion`
6. `outfit_suggestion` + `selected_item` → `create_fit_card` → `fit_card`

Every tool call is also appended to `session["tool_calls"]` in order, with the `id` of the item that was passed in, so which tools ran and what they received can be read off the session after a run.

The empty-search message is built by `agent.py::_empty_search_message`. It re-runs the search with one filter removed at a time, so it can say which filter was in the way — for example "raise your price limit to at least $20" or "drop the size (it comes in: M)" — and falls back to suggesting different keywords when no filter is to blame.

---

## Sample Run

**One full query**

```
$ python app.py ask '...'

```

**The three tools, tested one at a time**

```
$ python -c "from tools import search_listings; print(search_listings('graphic tee', max_price=30))"

```

```
$ python -c "from tools import suggest_outfit; from utils.data_loader import get_example_wardrobe, load_listings; print(suggest_outfit(load_listings()[0], get_example_wardrobe()))"

```

```
$ python -c "from tools import create_fit_card; from utils.data_loader import load_listings; print(create_fit_card('jeans and white sneakers', load_listings()[0]))"

```

---

## How I Used AI

**Moment 1**

- *What I asked for:*
- *What came back:*
- *What I changed:*

**Moment 2**

- *What I asked for:*
- *What came back:*
- *What I changed:*

<!-- ═══════════════════════ UNIT 4 — THE TEST ═══════════════════════

     Don't fill these in during unit 3.
     ═══════════════════════════════════════════════════════════════════ -->

---

## Run Log — Before

<!-- Five criteria, five tries each, in this exact format.

     `python run_eval.py --label before` runs everything and writes the table
     into results/. Paste it here and fill in the verdicts. -->

| Criterion | Target | Try 1 | Try 2 | Try 3 | Try 4 | Try 5 | Verdict |
|---|---|---|---|---|---|---|---|
| 1. | | | | | | | |
| 2. | | | | | | | |
| 3. | | | | | | | |
| 4. | | | | | | | |
| 5. | | | | | | | |

**Real output from one try**, pasted as text, naming the file and function
that produced it:

```

```

---

## Verdicts and Diagnoses

<!-- MET or MISSED per criterion against LAST UNIT's target, plus a sentence on
     how you decided. Then, for every miss: which of the four places it
     happened — a tool, the loop's branch, the session, or the model's
     output — AND the mechanism. -->

| # | Criterion | Target | Verdict | How I decided |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |

**Diagnoses**

---

## Loop Trace

<!-- One full run, printed step by step, with the MCP call visible in it.
     `python app.py ask '...' --trace` once the trace.step() calls are in. -->

**Happy path**

```

```

**Empty search**

```

```

**On the MCP move:**

---

## The Improvement

<!-- `python run_eval.py --label after` -->

**What I changed:**

**Which failure it was meant to fix:**

### Run Log — After

| Criterion | Target | Try 1 | Try 2 | Try 3 | Try 4 | Try 5 | Verdict |
|---|---|---|---|---|---|---|---|
| 1. | | | | | | | |
| 2. | | | | | | | |
| 3. | | | | | | | |
| 4. | | | | | | | |
| 5. | | | | | | | |

**Did it help, and how do I know:**

---

## What's Still Broken

<!-- ═════════════════════════════════════════════════════════════════════

     SUBMISSION CHECKLIST — unit 3

     [ ] criteria.md has five numbered criteria, each with a target
     [ ] Each criterion has a reason underneath it
     [ ] All five unit 3 sections above have real content
     [ ] Tool Inventory: all three tools, inputs WITH TYPES, a specific
         return value, and the empty case
     [ ] Planning Loop names the branch rule and agent.py::run_agent
     [ ] Sample Run: one full query plus the three per-tool tests, as text
     [ ] At least four new commits
     [ ] Repository URL submitted — WRITE IT DOWN, you submit the same one
         next unit

     SUBMISSION CHECKLIST — unit 4

     [ ] mcp_server.py exists with one tool registered
         (or a written record of exactly where the rewire broke)
     [ ] Run Log — Before, five criteria, five tries each
     [ ] Real output pasted underneath, naming file and function
     [ ] A verdict on every criterion
     [ ] A diagnosis for every miss, naming a place AND a mechanism
     [ ] Loop Trace, with the MCP call visible in it
     [ ] All three failure modes triggered and handled
     [ ] One improvement, with Run Log — After in the same format
     [ ] What's Still Broken
     [ ] At least four new commits
     [ ] The SAME repository URL as last unit

     Do not delete and recreate this repository. Your commit history is what
     shows your criteria existed before your results did.
     ═════════════════════════════════════════════════════════════════════ -->

---

📖 **How to run this project: [RUNNING.md](RUNNING.md)**
