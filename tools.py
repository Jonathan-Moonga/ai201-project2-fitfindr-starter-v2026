"""
The three FitFindr tools.

Each one is a standalone function you can call and test on its own, before any
of them are wired into the loop.

    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)             → str
    create_fit_card(outfit, new_item)              → str

The full spec for each — inputs, return shape, and what it returns when it has
nothing — is in the README under Tool Inventory. The docstrings below repeat
the parts the code depends on.
"""

import math
import re

import config
from generate import generate
from utils.data_loader import load_listings


# ── Tool 1: search_listings ───────────────────────────────────────────────────

# Words that say nothing about the item. Dropped before scoring so that
# "looking for a tee" scores the same as "tee".
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "for", "with", "to", "my",
    "me", "i", "im", "is", "it", "that", "this", "some", "any", "something",
    "looking", "look", "want", "need", "find", "show", "get", "please", "size",
    "under", "below", "max", "than", "less", "up",
}

# Spelled-out sizes people type, mapped to the letters the listings use.
_SIZE_WORDS = {
    "extra small": "xs", "x-small": "xs", "xsmall": "xs",
    "small": "s", "medium": "m", "large": "l",
    "extra large": "xl", "x-large": "xl", "xlarge": "xl",
}

# How much a keyword hit is worth, by where it was found. A word in the title
# says more about what the item *is* than a word buried in the description.
_FIELD_WEIGHTS = (("title", 3), ("style_tags", 2), ("other", 1))


def _stem(word: str) -> str:
    """Fold simple plurals so 'sneaker' matches 'sneakers'. Leaves '90s' alone."""
    if len(word) > 3 and word.endswith("s") and not word[-2].isdigit():
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    """Lowercase word set of a string, plurals folded."""
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower())}


def _keywords(description: str) -> list[str]:
    """The words in a description worth matching on, in order, no repeats."""
    seen: list[str] = []
    for word in re.findall(r"[a-z0-9]+", description.lower()):
        if word in _STOPWORDS:
            continue
        word = _stem(word)
        if word not in seen:
            seen.append(word)
    return seen


def _size_tokens(listing_size: str) -> set[str]:
    """
    Break a listing's size string into whole tokens.

    "S/M" → {"s", "m"}        "XL (oversized)" → {"xl", "oversized"}
    "US 8.5" → {"us", "8.5"}  "W30 L30" → {"w30", "l30"}

    Whole tokens are the point: a substring test would say "s" is in "us 9"
    and "l" is in "xl".
    """
    return {t for t in re.split(r"[\s/(),]+", listing_size.lower()) if t}


def _size_matches(wanted: str, listing_size: str) -> bool:
    """
    True when `wanted` is one of the listing's size tokens.

    - Case-insensitive. "M" matches "S/M" and "M/L", not "XL".
    - "medium" is read as "m", "large" as "l", and so on.
    - A leading "US" is ignored: "US 8" and "8" both match "US 8".
    - A bare waist number matches a W-size: "30" matches "W30 L30".
    - "One Size" listings match every requested size.
    """
    wanted = wanted.strip().lower()
    wanted = _SIZE_WORDS.get(wanted, wanted)
    wanted = re.sub(r"^us\s*", "", wanted)
    if not wanted:
        return True

    tokens = _size_tokens(listing_size)
    if {"one", "size"} <= tokens:
        return True
    if wanted in tokens:
        return True
    if wanted.isdigit() and f"w{wanted}" in tokens:
        return True
    return False


def _score(listing: dict, keywords: list[str]) -> tuple[int, int]:
    """
    Score one listing against the keywords.

    Returns (score, how many keywords matched at all). Each keyword counts
    once, at the weight of the best field it appeared in.
    """
    fields = {
        "title": _words(listing["title"]),
        "style_tags": _words(" ".join(listing["style_tags"])),
        "other": _words(" ".join([
            listing["description"],
            listing["category"],
            " ".join(listing["colors"]),
            listing["brand"] or "",      # brand is None for most listings
        ])),
    }
    score = 0
    matched = 0
    for keyword in keywords:
        for field, weight in _FIELD_WEIGHTS:
            if keyword in fields[field]:
                score += weight
                matched += 1
                break
    return score, matched


def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the listings data for items matching a description, and optionally a
    size and a price ceiling. Does not call the model.

    Args:
        description: keywords describing what the user wants
                     (e.g. "vintage graphic tee").
        size:        a size to filter by, or None to skip size filtering.
                     Matched against whole size tokens — see _size_matches.
        max_price:   maximum price, inclusive, or None to skip price filtering.

    Returns:
        A list of listing dicts, best match first, at most
        config.SEARCH_RESULT_LIMIT of them. Each dict has: id, title,
        description, category, style_tags, size, condition, price, colors,
        brand, platform.

        A listing is a match when it passes the size and price filters and
        contains at least half of the description's keywords (rounded up).
        Ties in score go to the cheaper listing.

        Returns [] when nothing matches, and when the description has no
        usable keywords. Never None, never an exception.
    """
    keywords = _keywords(description or "")
    if not keywords:
        return []

    needed = math.ceil(len(keywords) / 2)
    scored = []
    for listing in load_listings():
        if max_price is not None and listing["price"] > max_price:
            continue
        if size and not _size_matches(size, listing["size"]):
            continue
        score, matched = _score(listing, keywords)
        if score == 0 or matched < needed:
            continue
        scored.append((score, listing))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["price"]))
    return [listing for _, listing in scored[: config.SEARCH_RESULT_LIMIT]]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

_STYLIST = (
    "You are a thrift stylist. Be concrete and brief. Plain text only — no "
    "markdown, no headings, no bullet symbols."
)


def _describe_item(item: dict) -> str:
    """One listing as a few plain lines for a prompt. Skips a missing brand."""
    lines = [
        f"Item: {item.get('title')}",
        f"Category: {item.get('category')}",
        f"Colors: {', '.join(item.get('colors') or [])}",
        f"Style: {', '.join(item.get('style_tags') or [])}",
        f"Details: {item.get('description')}",
    ]
    if item.get("brand"):
        lines.append(f"Brand: {item['brand']}")
    return "\n".join(lines)


def _describe_wardrobe(items: list[dict]) -> str:
    """The wardrobe as one line per piece, with the user's notes when present."""
    lines = []
    for piece in items:
        line = f"- {piece.get('name')} ({piece.get('category')}; {', '.join(piece.get('colors') or [])})"
        if piece.get("notes"):
            line += f" — {piece['notes']}"
        lines.append(line)
    return "\n".join(lines)


def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest one or two outfits.
    Calls the model through generate().

    Args:
        new_item: a listing dict — the item the user is considering.
        wardrobe: a wardrobe dict with an 'items' key holding a list of
                  wardrobe items. The list may be empty.

    Returns:
        A non-empty string. With wardrobe items, it describes one or two
        outfits that name pieces from the wardrobe. With an empty wardrobe, it
        is general styling advice for the item instead.

        If the model comes back with nothing, returns a fixed one-line
        fallback naming the item, so the string is never "".
    """
    items = (wardrobe or {}).get("items") or []
    item_text = _describe_item(new_item)

    if not items:
        prompt = (
            f"{item_text}\n\n"
            "The buyer hasn't told us what they own. Suggest two ways to style "
            "this item using common pieces most people have. For each, say "
            "what to wear on the other half of the body, shoes, and one "
            "finishing touch. Two short paragraphs."
        )
    else:
        prompt = (
            f"{item_text}\n\n"
            f"The buyer already owns:\n{_describe_wardrobe(items)}\n\n"
            "Suggest one or two outfits built around the new item, using only "
            "pieces from the list above. Name each piece exactly as it is "
            "written in the list. One or two sentences per outfit on why it "
            "works."
        )

    response = generate(prompt, system=_STYLIST).strip()
    if not response:
        return (
            f"No outfit suggestion came back for {new_item.get('title')}. "
            "Pair it with neutral basics and try again."
        )
    return response


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

_CAPTION_WRITER = (
    "You write captions for thrift-haul posts. They sound like a real person "
    "showing off a find, not a product description. Plain text only."
)


def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Write a short caption someone would actually post about the find.
    Calls the model through generate().

    Args:
        outfit:   the outfit suggestion string from suggest_outfit().
        new_item: the listing dict for the item.

    Returns:
        A two-to-four sentence caption that mentions the item, its price, and
        its platform.

        If `outfit` is empty or whitespace, returns a message saying there is
        no outfit to write about, without calling the model. Never raises for
        that case and never returns "".
    """
    if not outfit or not outfit.strip():
        return (
            "No fit card: there's no outfit suggestion to write a caption "
            "from. Run suggest_outfit first."
        )

    price = new_item.get("price")
    price_text = f"${price:.0f}" if isinstance(price, (int, float)) else str(price)

    prompt = (
        f"The find: {new_item.get('title')}\n"
        f"Price: {price_text}\n"
        f"Platform: {new_item.get('platform')}\n"
        f"Vibe: {', '.join(new_item.get('style_tags') or [])}\n\n"
        f"How it's being styled:\n{outfit.strip()}\n\n"
        "Write the caption. Two to four sentences. Mention the item, the "
        "price, and the platform once each. Be specific about the vibe and "
        "name at least one piece it's being worn with."
    )

    response = generate(prompt, system=_CAPTION_WRITER).strip()
    if not response:
        return (
            f"Thrifted: {new_item.get('title')} for {price_text} on "
            f"{new_item.get('platform')}."
        )
    return response
