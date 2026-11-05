from __future__ import annotations

from decimal import Decimal

from ..events import Lifecycle, Trade
from ..strategy import Cancel, CancelAll, Place, Strategy
from ..tape_sim import Quote

TICK = Decimal("0.01")


class BandMaker(Strategy):
    """Rest a passive order on the FAVOURITE (or longshot) side when its price is in a band.

    side_mode:
      'fav'      buy the side priced in [lo, hi]: YES via bid at best bid, or NO via ask at best ask
      'longshot_fade' sell the cheap side: if YES ask <= hi, rest an ask (sell YES) at best ask
    price_rule: 'join' (best price) or 'improve' (one tick better, if spread allows)
    window_ms:  only act within this many ms before close (None = whole life); needs meta close_ms
    """

    def __init__(self, lo, hi, size="1", side_mode="fav", price_rule="join", window_ms=None,
                 taker=False, tag=None, max_per_market="1"):
        self.lo, self.hi = Decimal(str(lo)), Decimal(str(hi))
        self.size, self.mode, self.rule = Decimal(size), side_mode, price_rule
        self.window, self.taker = window_ms, taker
        self.max_pos = Decimal(max_per_market)
        self.name = tag or f"band_{side_mode}_{lo}_{hi}_{price_rule}_{window_ms}_{'T' if taker else 'M'}"

    def _in_window(self, ctx, ticker):
        if self.window is None:
            return True
        cm = ctx.meta.get(ticker, {}).get("close_ms")
        return cm is not None and 0 <= cm - ctx.now <= self.window

    def on_event(self, e, ctx):
        if not isinstance(e, Quote):
            return []
        t = e.ticker
        mine = [o for o in ctx.open_orders.values() if o.ticker == t and o.tag == self.name]
        held = abs(ctx.positions.get(t, Decimal(0)))
        want = None
        if self._in_window(ctx, t) and held < self.max_pos and e.bid is not None and e.ask is not None:
            if self.mode == "fav":
                if self.lo <= e.bid <= self.hi:                 # YES is the favourite
                    px = e.ask if self.taker else e.bid
                    if self.rule == "improve" and not self.taker and e.ask - e.bid > TICK:
                        px = e.bid + TICK
                    want = ("bid", px)
                elif self.lo <= 1 - e.ask <= self.hi:           # NO is the favourite
                    px = e.bid if self.taker else e.ask
                    if self.rule == "improve" and not self.taker and e.ask - e.bid > TICK:
                        px = e.ask - TICK
                    want = ("ask", px)
            elif self.mode == "longshot_fade":
                if e.ask <= self.hi and e.ask >= self.lo:      # YES is the longshot: sell it
                    want = ("ask", e.bid if self.taker else e.ask)
                elif 1 - e.bid <= self.hi and 1 - e.bid >= self.lo:   # NO is the longshot
                    want = ("bid", e.ask if self.taker else e.bid)
        cmds = []
        keep = False
        for o in mine:
            if want and o.side == want[0] and o.price == want[1]:
                keep = True
            else:
                cmds.append(Cancel(o.oid))
        if want and not keep:
            cmds.append(Place(t, want[0], want[1], self.size, post_only=not self.taker, tag=self.name))
        return cmds


def _flatten(e, ctx, tag):
    """Exit inventory as a taker at the current touch (IOC). Used at the pull time so the book holds
    no position through the live event."""
    inv = ctx.positions.get(e.ticker, Decimal(0))
    if inv > 0 and e.bid is not None:
        return [Place(e.ticker, "ask", e.bid, inv, post_only=False, tag=tag)]
    if inv < 0 and e.ask is not None:
        return [Place(e.ticker, "bid", e.ask, -inv, post_only=False, tag=tag)]
    return []


class TwoSidedMaker(Strategy):
    """Quote both sides at the touch (or improved) while spread >= min_spread. Pull at close-window."""

    def __init__(self, min_spread="0.03", size="1", improve=True, pull_ms=900_000, max_inv="3",
                 skew_to=None, tag=None, flatten=False):
        self.flatten = flatten
        self.min_spread, self.size, self.improve = Decimal(min_spread), Decimal(size), improve
        self.pull_ms, self.max_inv = pull_ms, Decimal(max_inv)
        self.skew_to = skew_to       # 'sell_yes' to quote only the ask (Yes-fade), 'buy_yes' bid only
        self.name = tag or f"mm_{min_spread}_{'imp' if improve else 'join'}_{pull_ms}_{skew_to}{'_flat' if flatten else ''}"

    def on_event(self, e, ctx):
        if not isinstance(e, Quote):
            return []
        t = e.ticker
        mine = [o for o in ctx.open_orders.values() if o.ticker == t and o.tag == self.name]
        cm = ctx.meta.get(t, {}).get("close_ms")
        live = ctx.meta.get(t, {}).get("event_start_ms")
        stop_at = (live if live else cm)
        if stop_at is not None and ctx.now >= stop_at - self.pull_ms:
            out = [Cancel(o.oid) for o in mine]
            if self.flatten and ctx.now < stop_at:
                out += _flatten(e, ctx, self.name)
            return out
        want = {}
        if e.bid is not None and e.ask is not None and e.ask - e.bid >= self.min_spread:
            inv = ctx.positions.get(t, Decimal(0))
            b = e.bid + TICK if self.improve else e.bid
            a = e.ask - TICK if self.improve else e.ask
            if self.skew_to != "sell_yes" and inv < self.max_inv:
                want["bid"] = b
            if self.skew_to != "buy_yes" and inv > -self.max_inv:
                want["ask"] = a
        cmds, have = [], set()
        for o in mine:
            if want.get(o.side) == o.price:
                have.add(o.side)
            else:
                cmds.append(Cancel(o.oid))
        for side, px in want.items():
            if side not in have:
                cmds.append(Place(t, side, px, self.size, post_only=True, tag=self.name))
        return cmds


class RandomEntry(Strategy):
    """No-information control (acceptance check 19): deterministic pseudo-random taker entries."""

    def __init__(self, every=4, size="1", tag="random_taker"):
        self.every, self.size, self.name, self.n = every, Decimal(size), tag, 0

    def on_event(self, e, ctx):
        if not isinstance(e, Quote) or e.bid is None or e.ask is None:
            return []
        self.n += 1
        if self.n % self.every:
            return []
        if ctx.positions.get(e.ticker, 0) != 0:
            return []
        side = "bid" if (self.n // self.every) % 2 else "ask"
        px = e.ask if side == "bid" else e.bid
        return [Place(e.ticker, side, px, self.size, post_only=False, tag=self.name)]


class Combo(Strategy):
    """Run several strategies side by side (each keeps its own tag for attribution)."""

    def __init__(self, parts, name=None):
        self.parts = parts
        self.name = name or "combo[" + "+".join(p.name for p in parts) + "]"

    def on_event(self, e, ctx):
        out = []
        for p in self.parts:
            out.extend(p.on_event(e, ctx) or [])
        return out


class PreEventMaker(Strategy):
    """Mention / scheduled-event maker. Quote passively only while ctx.now < event_start - pull_ms.
    side: 'sell_yes' (the Yes-fade: rest asks), 'buy_yes', or 'both'.
    price_rule: 'join' best price, 'improve' one tick inside, 'offset' k ticks behind best (safer).
    band: only quote when the YES mid is inside [lo, hi].
    min_spread: require at least this spread to quote (wide books only).
    max_inv: max contracts held per market (per side)."""

    def __init__(self, side="sell_yes", pull_ms=900_000, price_rule="join", lo="0.05", hi="0.95",
                 min_spread="0.02", size="1", max_inv="1", offset_ticks=0, tag=None, flatten=False):
        self.flatten = flatten
        self.side, self.pull, self.rule = side, pull_ms, price_rule
        self.lo, self.hi, self.min_spread = Decimal(lo), Decimal(hi), Decimal(min_spread)
        self.size, self.max_inv, self.off = Decimal(size), Decimal(max_inv), offset_ticks
        self.name = tag or f"pre_{side}_{pull_ms//60000}m_{price_rule}{offset_ticks or ''}_{lo}-{hi}_s{min_spread}_inv{max_inv}{'_flat' if flatten else ''}"

    def on_event(self, e, ctx):
        if not isinstance(e, Quote):
            return []
        t = e.ticker
        meta = ctx.meta.get(t, {})
        start = meta.get("event_start_ms") or meta.get("close_ms")
        mine = [o for o in ctx.open_orders.values() if o.ticker == t and o.tag == self.name]
        if start is None or ctx.now >= start - self.pull:
            out = [Cancel(o.oid) for o in mine]
            if self.flatten and start is not None and ctx.now < start:
                out += _flatten(e, ctx, self.name)
            return out
        want = {}
        if e.bid is not None and e.ask is not None and e.ask - e.bid >= self.min_spread:
            mid = (e.bid + e.ask) / 2
            if self.lo <= mid <= self.hi:
                inv = ctx.positions.get(t, Decimal(0))
                step = TICK * self.off
                if self.side in ("sell_yes", "both") and inv > -self.max_inv:
                    a = e.ask - TICK if (self.rule == "improve" and e.ask - e.bid > TICK) else e.ask + step
                    if a < 1:
                        want["ask"] = a
                if self.side in ("buy_yes", "both") and inv < self.max_inv:
                    b = e.bid + TICK if (self.rule == "improve" and e.ask - e.bid > TICK) else e.bid - step
                    if b > 0:
                        want["bid"] = b
        cmds, have = [], set()
        for o in mine:
            if want.get(o.side) == o.price:
                have.add(o.side)
            else:
                cmds.append(Cancel(o.oid))
        for sd, px in want.items():
            if sd not in have:
                cmds.append(Place(t, sd, px, self.size, post_only=True, tag=self.name))
        return cmds


class PreEventTaker(Strategy):
    """Control: take the NO side (sell YES at the bid) once, at the first quote inside the
    window [event_start - enter_ms, event_start - pull_ms], when the YES mid is in [lo, hi]."""

    def __init__(self, side="sell_yes", enter_ms=3 * 3600_000, pull_ms=900_000, lo="0.05", hi="0.50", tag=None):
        self.side, self.enter, self.pull = side, enter_ms, pull_ms
        self.lo, self.hi = Decimal(lo), Decimal(hi)
        self.name = tag or f"pretaker_{side}_{enter_ms//60000}m_{lo}-{hi}"
        self.done = set()

    def on_event(self, e, ctx):
        if not isinstance(e, Quote) or e.ticker in self.done or e.bid is None or e.ask is None:
            return []
        meta = ctx.meta.get(e.ticker, {})
        start = meta.get("event_start_ms") or meta.get("close_ms")
        if start is None or not (start - self.enter <= ctx.now < start - self.pull):
            return []
        mid = (e.bid + e.ask) / 2
        if not (self.lo <= mid <= self.hi):
            return []
        self.done.add(e.ticker)
        if self.side == "sell_yes":
            return [Place(e.ticker, "ask", e.bid, Decimal(1), post_only=False, tag=self.name)]
        return [Place(e.ticker, "bid", e.ask, Decimal(1), post_only=False, tag=self.name)]
