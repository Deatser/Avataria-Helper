# app/core/energy_bar.py
"""What to buy at the cafe for a wanted amount of energy.

The cafe sells three sweets (prices read off the in-game menu, 2026-08-12):

    Пирожок    +15  энергии   150 серебра
    Чизкейк    +30  энергии     1 золота
    Брауни    +100  энергии     3 золота

Silver buys only the first of them, so a silver run is one sweet repeated.
Gold has two sizes, and the run should be as few purchases as possible: take
the big one as many whole times as it fits, then top up with the small one,
rounding *up* — a run that stops one press early has not bought what was
asked for, so the last press is taken even when it overshoots.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import ceil

SILVER = "silver"
GOLD   = "gold"

CURRENCY_NAME = {SILVER: "серебра", GOLD: "золота"}

# Each sweet is written in its own colour, read off the cafe's menu art: the
# baked crust of the пирожок, the cream and berries of the чизкейк, the
# chocolate of the брауни. The two prices get the metal they are paid in.
PIE_CRUST    = "#e8b25e"
CHEESE_CREAM = "#f0dda8"
BROWNIE_COCO = "#b6764c"
SILVER_COIN  = "#c6d2e0"
GOLD_COIN    = "#f0c23a"

CURRENCY_COLOR = {SILVER: SILVER_COIN, GOLD: GOLD_COIN}


@dataclass(frozen=True)
class Treat:
    name: str
    energy: int
    price: int
    currency: str
    color: str
    template: str   # the buy button as it looks in the cafe's menu


PIE        = Treat("Пирожок",  15, 150, SILVER, PIE_CRUST,    "Button_pie.png")
CHEESECAKE = Treat("Чизкейк",  30,   1, GOLD,   CHEESE_CREAM, "button_cheesecake.png")
BROWNIE    = Treat("Брауни",  100,   3, GOLD,   BROWNIE_COCO, "button_brownie.png")

MENU = (PIE, CHEESECAKE, BROWNIE)


@dataclass(frozen=True)
class Plan:
    """Which sweets, how many, and what it comes to."""
    rounds: tuple           # ((Treat, count), …), biggest first
    cost: int               # in `currency`
    energy: int             # what actually lands, ≥ the amount asked for
    currency: str

    @property
    def purchases(self) -> int:
        return sum(count for _, count in self.rounds)

    def count_of(self, treat: Treat) -> int:
        """How many of one sweet this plan buys — 0 if it buys none."""
        return sum(count for bought, count in self.rounds if bought is treat)

    def sequence(self) -> list:
        """The sweets to buy, one entry per press, biggest first — the order
        the cafe run actually works through."""
        return [treat for treat, count in self.rounds for _ in range(count)]

    def describe(self) -> str:
        """«2 × Брауни + 3 × Чизкейк — 9 золота (290 энергии)»."""
        if not self.rounds:
            return "ничего — количество не выбрано"
        parts = " + ".join(f"{count} × {treat.name}"
                           for treat, count in self.rounds)
        return (f"{parts} — {self.cost} {CURRENCY_NAME[self.currency]} "
                f"({self.energy} энергии)")

    def describe_html(self, plain: str) -> str:
        """The same line for a QLabel, each sweet in its own colour and the
        price in its metal. `plain` is the colour for everything between."""
        if not self.rounds:
            return f'<span style="color:{plain};">ничего — количество ' \
                   f'не выбрано</span>'
        joiner = f'<span style="color:{plain};"> + </span>'
        parts = joiner.join(
            f'<span style="color:{treat.color};">{count} × {treat.name}</span>'
            for treat, count in self.rounds)
        money = (f'<span style="color:{CURRENCY_COLOR[self.currency]};">'
                 f'{self.cost} {CURRENCY_NAME[self.currency]}</span>')
        return (f'{parts}<span style="color:{plain};"> — </span>{money}'
                f'<span style="color:{plain};"> ({self.energy} энергии)</span>')

    def segments(self, plain: str, energy_color: str) -> list:
        """«Купили …» as (text, colour) pairs for a LogPanel line."""
        if not self.rounds:
            return [("Ничего не куплено — количество не выбрано", plain)]
        out = [("Купили ", plain)]
        for index, (treat, count) in enumerate(self.rounds):
            if index:
                out.append((" + ", plain))
            out.append((f"{count} × {treat.name}", treat.color))
        out += [(", потратили ", plain),
                (f"{self.cost} {CURRENCY_NAME[self.currency]}",
                 CURRENCY_COLOR[self.currency]),
                (", получили ", plain),
                (f"{self.energy} энергии", energy_color)]
        return out


def _totals(rounds: tuple, currency: str) -> Plan:
    cost   = sum(treat.price  * count for treat, count in rounds)
    energy = sum(treat.energy * count for treat, count in rounds)
    return Plan(rounds, cost, energy, currency)


def plan(amount: int, currency: str) -> Plan:
    """The cheapest short way to reach `amount` energy, never falling short."""
    amount = max(0, int(amount))
    if amount == 0:
        return _totals((), currency)

    if currency == SILVER:
        return _totals(((PIE, ceil(amount / PIE.energy)),), SILVER)

    # Gold: whole Брауни first, the rest topped up with чизкейками, rounded up.
    big   = amount // BROWNIE.energy
    rest  = amount - big * BROWNIE.energy
    small = ceil(rest / CHEESECAKE.energy) if rest else 0
    greedy = _totals(tuple(pair for pair in ((BROWNIE, big), (CHEESECAKE, small))
                           if pair[1]), GOLD)

    # …unless one more Брауни instead of those top-up чизкейков costs no more
    # and takes fewer trips to the counter — which it does as soon as the
    # remainder needs three of them (90 энергии: 1 × Брауни for 3 золота
    # beats 3 × Чизкейк for the same 3, in one press instead of three).
    if small:
        alternative = _totals(((BROWNIE, big + 1),), GOLD)
        if (alternative.cost <= greedy.cost
                and alternative.purchases < greedy.purchases):
            return alternative
    return greedy
