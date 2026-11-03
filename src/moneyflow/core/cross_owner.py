"""A6/A7/A8 cross-owner transfers.

Money moving between two owners of one household is not spending. Detection runs on the expense leg
only and reads the description only (never the comment). Coverage is a union over the counterparty's
included accounts, never an intersection. A leg is paired with income on the counterparty's included
accounts that names the payer, by cardinality (n legs, n partners), never greedily.

A7: a leg already excluded by a ruling is decided: it never triggers the unpaired refusal, but it still
counts in its key's cardinality match, as in the old audit. A match also excludes its income partner(s);
a leg keeps its ruling's reason (a hand ruling beats every signal).
A8: a leg with a `white_card` ruling is neither paired nor excluded; it stays kept as spending.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from moneyflow.core.model import Account, BuildRefused, Inputs, LedgerEntry, Transaction

Coverage = Mapping[str, tuple[date, date]]
Key = tuple[date, object, str, str, str]  # date, amount, currency, payer owner, recipient owner

REASON = "cross-owner-transfer"
_CROSS_OWNER_REASONS = frozenset({REASON, "cross-owner-unpaired"})


def coverage(transactions: Iterable[Transaction]) -> dict[str, tuple[date, date]]:
    """Per account, the first and last observed date. An account with no rows is absent."""
    spans: dict[str, tuple[date, date]] = {}
    for tx in transactions:
        first, last = spans.get(tx.account_id, (tx.date, tx.date))
        spans[tx.account_id] = (min(first, tx.date), max(last, tx.date))
    return spans


@dataclass(frozen=True)
class _Context:
    accounts: Mapping[str, Account]
    strings: Mapping[str, tuple[str, ...]]
    pool: Mapping[str, frozenset[str]]  # owner -> included account ids
    coverage: Coverage
    open: tuple[Transaction, ...]  # transactions whose entry is still unresolved
    scan: tuple[Transaction, ...]  # open plus transactions already excluded by a ruling (A7 legs)
    ruled: frozenset[str]
    white: frozenset[str]


def _pool(inputs: Inputs) -> dict[str, frozenset[str]]:
    grouped: dict[str, set[str]] = {}
    for account in inputs.accounts:
        if account.include:
            grouped.setdefault(account.owner, set()).add(account.id)
    return {owner: frozenset(ids) for owner, ids in grouped.items()}


def _cross_owner_ruling(entry: LedgerEntry) -> bool:
    """An excluded entry decided by a ruling whose reason is a cross-owner one (A7)."""
    return (entry.decided_by or "").startswith("ruling:") and entry.reason in _CROSS_OWNER_REASONS


def _context(entries: list[LedgerEntry], inputs: Inputs, spans: Coverage) -> _Context:
    unresolved = {e.transaction_id for e in entries if e.status == "unresolved"}
    ruled = frozenset(e.transaction_id for e in entries if e.status == "excluded" and _cross_owner_ruling(e))
    return _Context(
        accounts={a.id: a for a in inputs.accounts},
        strings={o.id: o.counterparty_strings for o in inputs.owners},
        pool=_pool(inputs),
        coverage=spans,
        open=tuple(t for t in inputs.transactions if t.id in unresolved),
        scan=tuple(t for t in inputs.transactions if t.id in unresolved or t.id in ruled),
        ruled=ruled,
        white=frozenset(r.transaction_id for r in inputs.rulings if r.kind == "white_card"),
    )


def _names(tx: Transaction, needles: tuple[str, ...]) -> bool:
    haystack = tx.description.lower()
    return any(needle.lower() in haystack for needle in needles)


def _covered(spans: Coverage, account_ids: frozenset[str], day: date) -> bool:
    return any(span[0] <= day <= span[1] for a in account_ids if (span := spans.get(a)) is not None)


def _payer(tx: Transaction, ctx: _Context) -> str:
    return ctx.accounts[tx.account_id].owner


def _recipient(tx: Transaction, ctx: _Context) -> str | None:
    """The owner this expense names, when that owner was being observed on the day."""
    mine = _payer(tx, ctx)
    for other, needles in ctx.strings.items():
        if other != mine and _names(tx, needles):
            # stop at the first owner named, covered or not: two names must not make two transfers
            return other if _covered(ctx.coverage, ctx.pool.get(other, frozenset()), tx.date) else None
    return None


def _detect(ctx: _Context) -> dict[str, str]:
    detected: dict[str, str] = {}
    for tx in ctx.scan:
        recipient = _recipient(tx, ctx) if tx.direction == "expense" else None
        if recipient is not None:
            detected[tx.id] = recipient
    return detected


def _group(detected: Mapping[str, str], ctx: _Context) -> dict[Key, list[Transaction]]:
    keys: dict[Key, list[Transaction]] = {}
    for tx in ctx.scan:
        if tx.id in detected:
            key = (tx.date, tx.amount, tx.currency, _payer(tx, ctx), detected[tx.id])
            keys.setdefault(key, []).append(tx)
    return keys


def _first_named(tx: Transaction, recipient: str, ctx: _Context) -> str | None:
    """The first other owner a row names: one row has one payer, however many names it holds."""
    for other, needles in ctx.strings.items():
        if other != recipient and _names(tx, needles):
            return other
    return None


def _candidates(key: Key, ctx: _Context) -> list[Transaction]:
    day, amount, currency, payer, recipient = key
    pool = ctx.pool.get(recipient, frozenset())
    return [
        t
        for t in ctx.open
        if t.direction == "income"
        and t.account_id in pool
        and (t.date, t.amount, t.currency) == (day, amount, currency)
        and _first_named(t, recipient, ctx) == payer
    ]


def _detail(txs: Iterable[Transaction]) -> str:
    rows = sorted(txs, key=lambda t: (t.direction, t.id))
    return "; ".join(f"{t.id} {t.date} {t.amount} {t.currency} {t.direction}" for t in rows)


def _refuse_listing(ruled: set[str], detected: Mapping[str, str], ctx: _Context) -> None:
    for tx_id in sorted(ctx.white & ruled):
        raise BuildRefused("white-card leg that is both a white-card leg and excluded by a ruling", tx_id)
    for tx_id in sorted(ctx.white - detected.keys()):
        raise BuildRefused(
            "white-card leg that is not a detected cross-owner expense leg; "
            "a leg outside the counterparty's coverage is never detected: "
            "drop its white_card ruling and decide its category with a category ruling instead",
            tx_id,
        )


def _refuse_unless_blessed(where: str, legs: list[Transaction], partners: list[Transaction], ctx: _Context) -> None:
    if all(t.id in ctx.ruled for t in legs):
        return
    raise BuildRefused(
        f"{where}: {len(legs)} detected expense leg(s) but {len(partners)} income partner(s) "
        f"naming the payer ({_detail(legs + partners)}); "
        "write an exclude ruling for the leg if it really is one-sided",
        legs[0].id,
    )


def _decide(key: Key, all_legs: list[Transaction], ctx: _Context) -> list[str]:
    day, amount, currency, payer, recipient = key
    legs = [t for t in all_legs if t.id not in ctx.white]
    partners = _candidates(key, ctx)
    where = f"cross-owner transfer {payer} -> {recipient} on {day} {amount} {currency}"
    if not legs and partners:
        listed = [t for t in all_legs if t.id in ctx.white]
        raise BuildRefused(
            f"{where}: white-card leg(s) listed but {len(partners)} income partner(s) name the payer "
            f"({_detail(listed + partners)}); if the money reached the pulled card, drop the white_card ruling",
            listed[0].id,
        )
    if legs and len(legs) != len(partners):
        _refuse_unless_blessed(where, legs, partners, ctx)
        return []  # every leg is blessed by a ruling: already excluded, the partners stay kept
    return [t.id for t in legs + partners if t.id not in ctx.ruled]  # a ruled leg keeps its ruling's reason


def cross_owner(entries: list[LedgerEntry], inputs: Inputs, coverage: Coverage) -> Mapping[str, str]:
    """Transaction id -> reason, for every unresolved entry that is a cross-owner transfer leg."""
    ctx = _context(entries, inputs, coverage)
    ruled = {e.transaction_id for e in entries if (e.decided_by or "").startswith("ruling:")}
    detected = _detect(ctx)
    _refuse_listing(ruled, detected, ctx)
    out: dict[str, str] = {}
    for key, legs in _group(detected, ctx).items():
        out.update(dict.fromkeys(_decide(key, legs, ctx), REASON))
    return out
