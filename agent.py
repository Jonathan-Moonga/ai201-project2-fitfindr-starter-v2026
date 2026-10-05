"""
The FitFindr planning loop.

This is the file that makes FitFindr an agent rather than a script. It decides
which tool to run next based on what the last one returned.

    python agent.py        runs both example paths below
"""

import re

import config
import trace
from tools import search_listings, suggest_outfit, create_fit_card
from generate import ModelUnavailable  # noqa: F401 — handled in unit 4


# ── session state ─────────────────────────────────────────────────────────────

def new_session(query: str, wardrobe: dict) -> dict:
    """
    A fresh session for one user interaction.

    The session is the single source of truth for a run. Every tool result goes
    in here, and the next tool reads it back out.
    """
    return {
        "query": query,                 # what the user typed
        "parsed": {},                   # description / size / max_price pulled out of it
        "search_results": [],           # everything search_listings returned
        "searched": False,              # True once search_listings has run
        "selected_item": None,          # the one chosen — goes into suggest_outfit
        "wardrobe": wardrobe,           # the user's wardrobe
        "outfit_suggestion": None,      # what suggest_outfit returned
        "fit_card": None,               # what create_fit_card returned
        "error": None,                  # set when the run ended early
        "tool_calls": [],               # one entry per tool call, in order
    }


# ── query parsing ─────────────────────────────────────────────────────────────

_PRICE = re.compile(
    r"(?:under|below|less than|up to|max(?:imum)?|at most|<=?)\s*\$?\s*(\d+(?:\.\d+)?)"
    r"|\$\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SIZE = re.compile(
    r"\bsize\s+(?:us\s+)?([a-z0-9]+(?:\.\d+)?(?:/[a-z0-9]+)?)",
    re.IGNORECASE,
)


def parse_query(query: str) -> dict:
    """
    Pull a description, a size, and a price ceiling out of a plain query.

    Regex, no model call. Two patterns:
      price — a number after "under", "below", "less than", "up to", "max",
              or after a bare "$".
      size  — the word after "size" ("size M", "size 8", "size US 8.5").

    Whatever is left once those are cut out is the description.

    Returns {"description": str, "size": str | None, "max_price": float | None}.
    """
    text = query or ""

    max_price = None
    price_match = _PRICE.search(text)
    if price_match:
        max_price = float(price_match.group(1) or price_match.group(2))
        text = text[: price_match.start()] + " " + text[price_match.end():]

    size = None
    size_match = _SIZE.search(text)
    if size_match:
        size = size_match.group(1).upper()
        text = text[: size_match.start()] + " " + text[size_match.end():]

    description = re.sub(r"[,;]+", " ", text)
    description = re.sub(r"\s+", " ", description).strip()
    # A trailing "in" is what's left of "... in size M".
    description = re.sub(r"\s+in$", "", description, flags=re.IGNORECASE)

    return {"description": description, "size": size, "max_price": max_price}


def _empty_search_message(parsed: dict) -> str:
    """
    Say what the user could change after a search that found nothing.

    Re-runs the search with one filter removed at a time, so the message can
    name the filter that is actually in the way rather than listing all of them.
    """
    description = parsed["description"]
    size = parsed["size"]
    max_price = parsed["max_price"]

    asked = f"'{description}'" if description else "that"
    if size:
        asked += f" in size {size}"
    if max_price is not None:
        asked += f" under ${max_price:g}"

    suggestions = []
    if max_price is not None:
        without_price = search_listings(description, size, None)
        if without_price:
            cheapest = min(item["price"] for item in without_price)
            suggestions.append(f"raise your price limit to at least ${cheapest:g}")
    if size:
        without_size = search_listings(description, None, max_price)
        if without_size:
            sizes = sorted({item["size"] for item in without_size})
            suggestions.append(
                f"drop the size (it comes in: {', '.join(sizes[:4])})"
            )

    if not suggestions:
        if (size or max_price is not None) and search_listings(description, None, None):
            suggestions.append("loosen both the size and the price limit")
        else:
            suggestions.append(
                "try different words for the item — the kind of piece "
                "('jacket', 'tee', 'jeans') or a style ('vintage', '90s', 'y2k')"
            )

    return f"No listings matched {asked}. To get results, {' or '.join(suggestions)}."


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Run the loop once and return the finished session.

    Each time round, the loop reads the session, works out which step is still
    missing, and runs that one step. Every result is written to the session
    before the next step reads it back out.

    The branch: after search_listings, if session["search_results"] is an
    empty list, a message goes in session["error"] and the loop stops.
    suggest_outfit and create_fit_card are never called with nothing.

    Returns:
        The session dict. Check session["error"] first — if it isn't None,
        the run ended early and the later fields are still None.
    """
    session = new_session(query, wardrobe)

    count = 0
    while True:
        count += 1
        trace.check_iterations(count)

        # Step 1 — parse. Nothing else can run without this.
        if not session["parsed"]:
            session["parsed"] = parse_query(session["query"])
            continue

        # Step 2 — search, with what parsing put in the session.
        if not session["searched"]:
            parsed = session["parsed"]
            session["search_results"] = search_listings(
                parsed["description"], parsed["size"], parsed["max_price"]
            )
            session["searched"] = True
            session["tool_calls"].append({
                "tool": "search_listings",
                "inputs": dict(parsed),
                "returned_ids": [item["id"] for item in session["search_results"]],
            })
            continue

        # ── THE BRANCH ──────────────────────────────────────────────────────
        # Empty search: say what to change and stop. No further tools run.
        if len(session["search_results"]) == 0:
            session["error"] = _empty_search_message(session["parsed"])
            return session

        # Step 3 — choose. Best match is first.
        if session["selected_item"] is None:
            session["selected_item"] = session["search_results"][0]
            continue

        # Step 4 — outfit. The item is read back out of the session.
        if session["outfit_suggestion"] is None:
            item = session["selected_item"]
            session["outfit_suggestion"] = suggest_outfit(item, session["wardrobe"])
            session["tool_calls"].append({
                "tool": "suggest_outfit",
                "item_id": item["id"],
                "wardrobe_size": len(session["wardrobe"].get("items", [])),
            })
            continue

        # Step 5 — fit card, from the outfit and item now in the session.
        if session["fit_card"] is None:
            item = session["selected_item"]
            session["fit_card"] = create_fit_card(session["outfit_suggestion"], item)
            session["tool_calls"].append({
                "tool": "create_fit_card",
                "item_id": item["id"],
            })
            continue

        # Nothing left to do.
        return session


# ── running it directly ───────────────────────────────────────────────────────

def _show(session: dict) -> None:
    calls = " → ".join(call["tool"] for call in session["tool_calls"])
    print(f"  parsed:   {session['parsed']}")
    print(f"  tools:    {calls}")

    if session["error"]:
        print(f"  stopped:  {session['error']}")
        print(f"  fit_card is {session['fit_card']!r} — it should still be None here")
        return

    item = session["selected_item"] or {}
    print(f"  found:    {item.get('title')} — ${item.get('price')} on {item.get('platform')}")
    print(f"  outfit:   {session['outfit_suggestion']}")
    print(f"  fit card: {session['fit_card']}")


if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe

    print("=== A query the data can match ===")
    _show(run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    ))

    print("\n=== A query it can't ===")
    _show(run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    ))

    print(
        "\nThe second one should stop before the fit card. If both paths look "
        "the same,\nthe branch isn't doing anything yet."
    )
