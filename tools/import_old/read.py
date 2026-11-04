"""Readers for the old repo's data files. Every reader is read-only and builds core model objects."""

import ast
import csv
import itertools
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import yaml

from moneyflow.core.model import Account, Owner, Person, Rule, Ruling, Transaction

_DIRECTIONS = ("income", "expense")
_AMOUNT_KEYS = ("amount_lt", "amount_gte")


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_accounts(path: Path) -> tuple[Account, ...]:
    return tuple(
        Account(
            id=str(a["id"]), name=str(a["name"]), currency=str(a["currency"]),
            owner=str(a["owner"]), role=str(a["role"]), include=bool(a["include"]),
            cash_from=None,
        )
        for a in _load(path)["accounts"]
    )


def read_categories(path: Path) -> frozenset[str]:
    return frozenset(str(c["name"]) for c in _load(path)["categories"])


def _match(raw: dict) -> dict[str, object]:
    """Amount thresholds are stored as strings so no float ever reaches the database."""
    return {k: (str(v) if k in _AMOUNT_KEYS else v) for k, v in raw.items()}


def read_rules(path: Path) -> tuple[Rule, ...]:
    return tuple(
        Rule(
            id=str(r["name"]), kind="categorize", priority=int(r["priority"]), ordinal=i,
            match=_match(r["match"]),
            result={"category": r["category"], "subcategory": r.get("subcategory")},
            active=True, created_by="import",
        )
        for i, r in enumerate(_load(path)["rules"])
    )


def read_overrides(path: Path) -> tuple[Ruling, ...]:
    return tuple(
        Ruling(
            id=f"override:{tx_id}", transaction_id=str(tx_id), kind="category",
            payload={"category": o["category"], "subcategory": o.get("subcategory"), "note": o.get("note")},
            created_by="import",
        )
        for tx_id, o in _load(path)["overrides"].items()
    )


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _transaction(row: dict, account: Account) -> Transaction:
    tid = row["id"]
    amount = Decimal(row["amount_uah"] if "amount_uah" in row else row["amount"])
    if amount < 0:
        raise ValueError(f"negative amount (transaction {tid})")
    if row["direction"] not in _DIRECTIONS:
        raise ValueError(f"direction {row['direction']!r} is not income or expense (transaction {tid})")
    declared = (row.get("currency") or "").strip()
    if declared and declared != account.currency:
        raise ValueError(f"currency {declared!r} differs from account {account.id} ({account.currency}) (transaction {tid})")
    return Transaction(
        id=tid, account_id=account.id, date=date.fromisoformat(row["date"]), time=_utc(row["time"]),
        amount=amount, currency=account.currency, direction=row["direction"],
        mcc=int(row["mcc"]) if row["mcc"] else None,
        description=row["description"], comment=row["comment"], source="bank",
    )


def _file_rows(path: Path, accounts: dict[str, Account]) -> list[Transaction]:
    account_id = path.name.rsplit("_", 2)[0]
    if account_id not in accounts:
        raise ValueError(f"raw file {path.name} belongs to unknown account {account_id!r}")
    with open(path, encoding="utf-8", newline="") as handle:
        return [_transaction(row, accounts[account_id]) for row in csv.DictReader(handle)]


def read_raw(directory: Path, accounts: tuple[Account, ...]) -> tuple[Transaction, ...]:
    """`directory/*.csv`, no recursion. Last copy of an id wins; ids whose copies differ are printed."""
    by_id = {a.id: a for a in accounts}
    kept: dict[str, Transaction] = {}
    differing: list[str] = []
    for path in sorted(Path(directory).glob("*.csv")):
        for t in _file_rows(path, by_id):
            if t.id in kept and kept[t.id] != t and t.id not in differing:
                differing.append(t.id)
            kept[t.id] = t
    print(f"differing duplicates: {len(differing)}")
    for tid in differing:
        print(f"  {tid}")
    return tuple(kept.values())


def read_owners(path: Path) -> tuple[Owner, ...]:
    """`owners:` and `budget_owner:` of the old accounts.yaml; exactly one owner gets `budget=True`."""
    doc = _load(path)
    owners = doc.get("owners")
    if not owners:
        raise ValueError("owners: missing in accounts.yaml")
    budget = doc.get("budget_owner")
    if budget not in owners:
        raise ValueError(f"budget_owner {budget!r} is not a key of owners: in the old accounts.yaml")
    return tuple(
        Owner(
            id=str(key), display=str(o["display"]),
            counterparty_strings=tuple(str(s) for s in (o.get("counterparty_strings") or ())),
            budget=key == budget,
        )
        for key, o in owners.items()
    )


def _constants(path: Path) -> dict[str, ast.expr]:
    """Module-level `NAME = <expr>` assignments of an old source file, parsed and never executed."""
    out: dict[str, ast.expr] = {}
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out[node.target.id] = node.value
    return out


def _constant(constants: dict[str, ast.expr], name: str, path: Path) -> ast.expr:
    if name not in constants:
        raise ValueError(f"{name} not found in {Path(path).name} of the old repo")
    return constants[name]


def read_cash(cash_source: Path) -> tuple[str, date]:
    """The cash account id and its cutover date, read from the old `finance/cash.py` source."""
    constants = _constants(cash_source)
    account = ast.literal_eval(_constant(constants, "CASH_ACCOUNT", cash_source))
    call = _constant(constants, "CASH_CUTOVER", cash_source)
    if not (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "date"):
        raise ValueError("CASH_CUTOVER is not a date(...) call in the old cash.py")
    return str(account), date(*(ast.literal_eval(a) for a in call.args))


_REGEX_META = frozenset("\\^$.|?*+()[]{}")


def _regex_source(constants: dict[str, ast.expr], name: str, path: Path) -> str:
    call = _constant(constants, name, path)
    if not (isinstance(call, ast.Call) and call.args):
        raise ValueError(f"{name} is not a re.compile(...) call in the old {Path(path).name}")
    return str(ast.literal_eval(call.args[0]))


def _expand_optional(text: str) -> list[str]:
    """`н?` becomes two literals: the letter present and absent."""
    out = [""]
    i = 0
    while i < len(text):
        if i + 1 < len(text) and text[i + 1] == "?":
            out = [prefix + tail for prefix in out for tail in (text[i], "")]
            i += 2
        else:
            out = [prefix + text[i] for prefix in out]
            i += 1
    return out


def _alternatives(pattern: str) -> list[str]:
    """One literal per regex alternative; refuses an alternative that still holds a metacharacter."""
    if pattern.startswith("(") and pattern.endswith(")"):
        pattern = pattern[1:-1]
    literals: list[str] = []
    for alternative in pattern.split("|"):
        for literal in _expand_optional(alternative.replace("\\s+", " ")):
            if _REGEX_META & set(literal):
                raise ValueError(f"regex alternative {alternative!r} holds a regex metacharacter after the swap")
            literals.append(literal)
    return literals


def read_exclude_rules(exclusions_source: Path) -> tuple[Rule, ...]:
    """A10, A11 and A14 regexes of the old `finance/exclusions.py`, one `exclude` rule per alternative.

    Note on A10: the old regex read the description only; `description_contains` reads description and
    comment. 0 live rows differ. No new match key was added for it. The "розстрочка на картку"
    alternative of A14 never becomes a rule: an instalment drawdown is excluded only through its id (A15).
    """
    constants = _constants(exclusions_source)
    instalment = set(_alternatives(_regex_source(constants, "_INSTALMENT_DRAWDOWN", exclusions_source)))
    # (constant, reason, income only, needs the expense partner)
    plan = (
        ("_MILES", "miles-to-charity", True, True),
        ("_CASHBACK", "cashback-roundup", False, False),
        ("_CREDIT_DRAWDOWN", "credit-drawdown", True, False),
    )
    rules: list[Rule] = []
    ordinal = itertools.count()
    for name, reason, income_only, paired in plan:
        literals = [lit for lit in _alternatives(_regex_source(constants, name, exclusions_source)) if lit not in instalment]
        for n, literal in enumerate(literals, start=1):
            match: dict[str, object] = {"description_contains": literal}
            if income_only:
                match["direction"] = "income"
            result: dict[str, str | bool | None] = {"reason": reason}
            if paired:
                result["paired"] = True
            rules.append(Rule(
                id=f"import-exclude-{reason}-{n}", kind="exclude", priority=0, ordinal=next(ordinal),
                match=match, result=result, active=True, created_by="import",
            ))
    return tuple(rules)


def _notes(path: Path, block: str) -> dict[str, str]:
    return {str(k): str(v) for k, v in (_load(path).get(block) or {}).items()}


def read_exclude_rulings(manual: Path, exclusions_source: Path) -> tuple[Ruling, ...]:
    """`unpaired_transfers:`, `unpaired_internal:` and the old excepted instalment ids as `exclude` rulings."""
    constants = _constants(exclusions_source)
    excepted = ast.literal_eval(_constant(constants, "_EXCEPTED_INSTALMENT_IDS", exclusions_source).args[0])  # type: ignore[attr-defined]
    sources = (
        ("cross-owner-unpaired", _notes(manual, "unpaired_transfers")),
        ("internal-transfer", _notes(manual, "unpaired_internal")),
        ("credit-drawdown", {str(i): "excepted instalment drawdown (old exclusions.py)" for i in sorted(excepted)}),
    )
    return tuple(
        Ruling(id=f"exclude:{reason}:{tx}", transaction_id=tx, kind="exclude",
               payload={"reason": reason, "note": note}, created_by="import")
        for reason, notes in sources for tx, note in notes.items()
    )


def read_white_card_rulings(manual: Path) -> tuple[Ruling, ...]:
    """`white_card` rulings. A leg that also has a split needs nothing else: A4 accepts the split ruling."""
    return tuple(
        Ruling(id=f"white_card:{tx}", transaction_id=str(tx), kind="white_card", payload={}, created_by="import")
        for tx in (_load(manual).get("white_card_spending") or {})
    )


def read_split_rulings(manual: Path) -> tuple[Ruling, ...]:
    """One `split:<tx>:<n>` ruling per part. A part named by `description` keeps it as `label`, with no subcategory."""
    rulings: list[Ruling] = []
    for tx, entry in (_load(manual).get("splits") or {}).items():
        for n, part in enumerate(entry["parts"], start=1):
            day = part.get("date")
            payload = {
                "amount": str(part["amount"]), "category": part["category"], "subcategory": part.get("subcategory"),
                "label": part.get("description"), "date": None if day is None else str(day),
            }
            rulings.append(Ruling(id=f"split:{tx}:{n}", transaction_id=str(tx), kind="split", payload=payload, created_by="import"))
    return tuple(rulings)


def read_reimbursement_rulings(manual: Path) -> tuple[Ruling, ...]:
    return tuple(
        Ruling(id=f"reimbursement:{tx}", transaction_id=str(tx), kind="reimbursement",
               payload={"category": str(category)}, created_by="import")
        for tx, category in (_load(manual).get("reimbursements") or {}).items()
    )


def read_debt_rulings(manual: Path) -> tuple[Ruling, ...]:
    return tuple(
        Ruling(id=f"debt:{tx}", transaction_id=str(tx), kind="debt",
               payload={"person": str(d["person"]), "kind": str(d["kind"]), "note": d.get("note")}, created_by="import")
        for tx, d in (_load(manual).get("debts") or {}).items()
    )


def read_people(manual: Path) -> tuple[Person, ...]:
    """`debt_people:`; the person's name is both id and display, as the old pipeline wrote it into the subcategory."""
    return tuple(
        Person(id=str(name), display=str(name), opening_balance=Decimal(str(p["opening_balance"])), note=p.get("note"))
        for name, p in (_load(manual).get("debt_people") or {}).items()
    )


def _manual_transaction(key: str, entry: dict, accounts: dict[str, Account]) -> Transaction:
    account = accounts.get(entry["account"])
    if account is None:
        raise ValueError(f"manual_expenses {key}: account {entry['account']!r} is not among the imported accounts")
    day = date.fromisoformat(str(entry["date"]))
    return Transaction(
        id=str(key), account_id=account.id, date=day, time=datetime.combine(day, time(12, 0)),
        amount=Decimal(str(entry["amount"])), currency=account.currency, direction=entry.get("direction", "expense"),
        mcc=None, description=entry["description"], comment=entry["note"], source="manual",
    )


def read_manual_transactions(manual: Path, accounts: tuple[Account, ...]) -> tuple[tuple[Transaction, ...], tuple[Ruling, ...]]:
    """One manual transaction and one `manual:<tx>` category ruling per `manual_expenses:` entry.

    Not ported: the old loader's `new_subcategory` flag only validates that a (category, subcategory) pair is
    produced somewhere; it changes no ledger value, so it is not read.
    """
    by_id = {a.id: a for a in accounts}
    entries = (_load(manual).get("manual_expenses") or {}).items()
    transactions = tuple(_manual_transaction(str(k), e, by_id) for k, e in entries)
    rulings = tuple(
        Ruling(id=f"manual:{k}", transaction_id=str(k), kind="category",
               payload={"category": e["category"], "subcategory": e.get("subcategory") or None, "note": e["note"]}, created_by="import")
        for k, e in entries
    )
    return transactions, rulings


def _literal(source: Path, name: str) -> object:
    return ast.literal_eval(_constant(_constants(source), name, source))


def _investment_literal(source: Path) -> str:
    literals = _alternatives(_regex_source(_constants(source), "INVESTMENT_WITHDRAWAL", source))
    if len(literals) != 1:
        raise ValueError(f"INVESTMENT_WITHDRAWAL has {len(literals)} alternatives in the old {Path(source).name}, expected 1")
    return literals[0]


def _person_return_rule(description: str, target: tuple[str, str], ordinal: int) -> Rule:
    category, name = target
    if not name.endswith("-return"):
        raise ValueError(f"person return rule name {name!r} does not end in -return")
    return Rule(
        id=name, kind="person_return", priority=0, ordinal=ordinal, match={"description_equals": description},
        result={"person": name.removesuffix("-return"), "category": category}, active=True, created_by="import",
    )


def read_reclassify_rules(source: Path) -> tuple[Rule, ...]:
    """A16, A20, A21 and A22 as rules, read from the constants of the old `finance/` modules, never executed."""
    source = Path(source)
    single = (
        ("import-credit-repayment", "credit_repayment", {"description_prefix": _literal(source / "exclusions.py", "_REPAYMENT_PREFIX")}),
        ("import-cancellation", "cancellation", {"description_prefix": _literal(source / "refunds.py", "REFUND_PREFIX")}),
        ("import-investment-withdrawal", "investment_withdrawal",
         {"description_contains": _investment_literal(source / "investment_withdrawals.py"), "direction": "income"}),
    )
    rules = [
        Rule(id=rid, kind=kind, priority=0, ordinal=0, match=match, result={}, active=True, created_by="import")  # type: ignore[arg-type]
        for rid, kind, match in single
    ]
    returns = _literal(source / "person_returns.py", "PERSON_RETURNS")
    rules += [_person_return_rule(d, t, i) for i, (d, t) in enumerate(returns.items())]  # type: ignore[attr-defined]
    return tuple(rules)
