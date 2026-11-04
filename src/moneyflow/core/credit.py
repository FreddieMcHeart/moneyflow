"""A16: a credit repayment is the drawdown it pays back (principal, excluded) plus the interest (kept)."""

from dataclasses import replace

from moneyflow.core.match import matches
from moneyflow.core.model import BuildRefused, Inputs, LedgerEntry, Rule, Transaction
from moneyflow.core.owners import owner_of

DRAWDOWN = "credit-drawdown"
CATEGORY = "Credit"


def _rules(inputs: Inputs) -> list[Rule]:
    return sorted((r for r in inputs.rules if r.active and r.kind == "credit_repayment"), key=lambda r: (r.priority, r.ordinal))


def _rule_for(rules: list[Rule], tx: Transaction) -> Rule | None:
    for rule in rules:
        try:
            if matches(rule.match, tx):
                return rule
        except (ValueError, ArithmeticError) as broken:
            raise BuildRefused(f"rule {rule.id}: {broken}", tx.id) from broken
    return None


def _drawn_for(repayment: LedgerEntry, tx: Transaction, merchant: str, drawdowns: list[tuple[LedgerEntry, Transaction]], inputs: Inputs) -> LedgerEntry | None:
    """The earliest drawdown that is the same owner and currency, named alike, strictly earlier and no larger."""
    owner = owner_of(tx.account_id, inputs)
    for entry, dtx in drawdowns:
        same = dtx.currency == tx.currency and dtx.description.strip() == merchant and owner_of(dtx.account_id, inputs) == owner
        if same and (dtx.date, dtx.time) < (tx.date, tx.time) and entry.amount <= repayment.amount:
            return entry
    return None


def _parts(entry: LedgerEntry, drawn: LedgerEntry, rule: Rule, inputs: Inputs) -> list[LedgerEntry]:
    via = f"rule:{rule.id}"
    principal = replace(entry, amount=drawn.amount, status="excluded", reason="credit-principal", decided_by=via, via=via)
    interest = entry.amount - drawn.amount
    if interest == 0:
        return [principal]
    if CATEGORY not in inputs.categories:
        raise BuildRefused(f"unknown category {CATEGORY}", entry.transaction_id)
    kept = replace(entry, part=1, amount=interest, category=CATEGORY, status="kept", decided_by="core:credit-interest", via=via)
    return [principal, kept]


def _is_repayment(entry: LedgerEntry) -> bool:
    return entry.status == "unresolved" and entry.via is None and entry.direction == "expense" and entry.part == 0


def pair_credit(entries: list[LedgerEntry], inputs: Inputs) -> list[LedgerEntry]:
    rules = _rules(inputs)
    txs = {t.id: t for t in inputs.transactions}
    drawdowns = sorted(
        ((e, txs[e.transaction_id]) for e in entries if e.status == "excluded" and e.reason == DRAWDOWN and e.part == 0),
        key=lambda pair: (pair[1].date, pair[1].time),
    )
    replaced: dict[str, list[LedgerEntry]] = {}
    for entry in sorted((e for e in entries if _is_repayment(e)), key=lambda e: (txs[e.transaction_id].date, txs[e.transaction_id].time, e.transaction_id)):
        tx = txs[entry.transaction_id]
        rule = _rule_for(rules, tx)
        if rule is None:
            continue
        merchant = tx.description.lstrip()[len(str(rule.match.get("description_prefix", ""))) :].strip()
        drawn = _drawn_for(entry, tx, merchant, drawdowns, inputs)
        if drawn is not None:
            drawdowns = [pair for pair in drawdowns if pair[0] is not drawn]
            replaced[entry.transaction_id] = _parts(entry, drawn, rule, inputs)
    return [part for e in entries for part in replaced.get(e.transaction_id, [e])]
