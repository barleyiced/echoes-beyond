"""Arcadia Coin: should you buy, sell, or leave the counter alone?

The Trader Mask adds a second currency and a counter that trades it against
Cosmic Fragments. Patch 4.5 added it; nothing about it is datamined, and what
*is* on the screen is easy to read backwards.

**The percentage on the counter is a delta, not a level.** It is the change from
the price you last saw, so a down arrow means cheaper than last time and never
cheap. Three readings settled this: 156 at +20.00% is exactly the 130 of the
reading before it, and 130 at -5.11% implies 137 before that. An earlier single
sample read 72 at -28.00%, which divides to exactly 100, and the obvious theory
was "baseline 100, negative is a buy". Under that theory 130 at -5.11% is a buy,
when in fact 130 sits near the top of everything seen and the price had been 72
earlier in the same run. The tidy arithmetic was a coincidence and the advice
would have been inverted.

So this module never guesses a fair price. It reads the prices the player has
actually logged and says where the current one sits among them, which is the
only honest source of "is this high". With one reading it says so and stops.

Everything numeric here is an estimate, in the sense NOTES.md uses: the shape is
reported from play, the thresholds are judgement.
"""

from __future__ import annotations

from engine.state import RunState

# Where in the run's own observed range a price stops being worth acting on.
# Deliberately wide: with a handful of readings the range is barely known, and a
# narrow band would turn sampling noise into a recommendation.
SELL_BAND = 0.65
BUY_BAND = 0.35

# Below this many logged prices the range means nothing and no verdict is given.
MIN_SAMPLES = 3


def position(prices: list[int]) -> float | None:
    """Where the newest price sits in the run's observed range, 0..1.

    None when the range has collapsed or there is nothing to compare against,
    which is a real state rather than a middling 0.5.
    """
    if len(prices) < 2:
        return None
    low, high = min(prices), max(prices)
    if high == low:
        return None
    return (prices[-1] - low) / (high - low)


def hold_yield(run: RunState, coins: int | None = None) -> int:
    """Fragments the coins you hold will pay out over the rest of the run.

    The Mask pays this on entering a Domain, so it is the Domains remaining that
    matter, not the Planes.

    **This is what waiting earns, not an alternative to selling.** The first
    version of this card compared it against the sale value and printed "selling
    all 5 now would bring 575, against 60 from holding them to the end" directly
    under a verdict reading Hold, which is a contradiction on its face. The
    comparison was wrong, not just badly worded: never selling is not a strategy,
    so the real choice is sell now against sell later, and the dividend is the
    small thing that makes waiting cheap rather than a rival to the sale.
    """
    coins = run.arcadia_coins if coins is None else coins
    return max(0, coins) * max(0, run.arcadia_dividend) * max(0, run.domains_left())


def advise(run: RunState) -> dict:
    """Buy, sell or hold, with the working shown.

    Returns the same shape the other economy surfaces use, so the UI renders it
    the way it renders a store verdict.
    """
    prices = [int(p) for p in run.arcadia_prices if p]
    coins = max(0, run.arcadia_coins)
    left = run.domains_left()
    yield_now = hold_yield(run)
    notes: list[str] = []

    # Always true, whatever the price is doing, and the largest number on the
    # screen for a player holding the opening grant untouched.
    if coins:
        notes.append(
            f"your {coins} coin(s) pay {run.arcadia_dividend} fragment(s) each when you enter "
            f"a Domain, so waiting for a better price earns about {yield_now} more fragments "
            f"over the {left} Domain(s) left rather than costing you anything")

    if not prices:
        return {
            "action": "log", "position": None, "price": 0, "coins": coins,
            "hold_yield": yield_now,
            "headline": "Type the rate on the counter. One reading is not enough to say "
                        "whether it is high.",
            "reasons": notes + [
                "the percentage on the counter is the change from the price you last saw, "
                "not a distance from any fixed value, so it cannot tell you whether the "
                "price is good",
            ],
        }

    price = prices[-1]
    pos = position(prices)
    sale = coins * price
    low, high = min(prices), max(prices)

    if len(prices) >= 2:
        notes.append(f"you have seen {low} to {high} this run, and it is {price} now")
    if coins:
        notes.append(f"selling all {coins} at {price} brings {sale} fragments")

    # Nothing survives a run, so a coin still held when it ends paid only its
    # dividend. That is the one verdict here that does not depend on the range.
    if coins and left <= 1:
        return {
            "action": "sell", "position": pos, "price": price, "coins": coins,
            "hold_yield": yield_now,
            "headline": f"Sell. {left} Domain(s) left, and coins do not survive the run.",
            "reasons": notes + [
                "whatever the price is doing, an unsold coin at the end of a run is worth "
                "nothing, the same way an unspent fragment is, so there is no later left "
                "to wait for",
            ],
        }

    if pos is None or len(prices) < MIN_SAMPLES:
        return {
            "action": "hold", "position": pos, "price": price, "coins": coins,
            "hold_yield": yield_now,
            "headline": f"{len(prices)} reading(s) logged. Not enough to say whether {price} "
                        f"is high or low.",
            "reasons": notes + [
                f"log the rate each time you pass the counter. {MIN_SAMPLES} readings is "
                f"the point where the range starts meaning something",
            ],
        }

    pct = int(round(pos * 100))
    if coins and pos >= SELL_BAND:
        return {
            "action": "sell", "position": pos, "price": price, "coins": coins,
            "hold_yield": yield_now,
            "headline": f"Sell. {price} is near the top of what you have seen this run.",
            "reasons": notes + [
                f"{pct}% of the way up your observed range, and you may only buy or sell "
                f"once in a Domain, so this is the trade to make here",
            ],
        }

    if pos <= BUY_BAND and run.fragments >= price:
        return {
            "action": "buy", "position": pos, "price": price, "coins": coins,
            "hold_yield": yield_now,
            "headline": f"Buy. {price} is near the bottom of what you have seen this run.",
            "reasons": notes + [
                f"{pct}% of the way up your observed range",
                "this is a bet that the price comes back up while you still have Domains "
                "left to sell in. The counter does not appear in every Domain",
            ],
        }

    return {
        "action": "hold", "position": pos, "price": price, "coins": coins,
        "hold_yield": yield_now,
        "headline": f"Hold. {price} is mid range for this run, so neither side is worth "
                    f"your one trade here.",
        "reasons": notes + [
            f"{pct}% of the way up your observed range, between the {int(BUY_BAND * 100)}% "
            f"and {int(SELL_BAND * 100)}% marks where a trade starts being worth making",
        ],
    }
