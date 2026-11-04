"""Field access shared by the kinds: a malformed rule or ruling refuses, never asserts."""

from collections.abc import Mapping

from moneyflow.core.model import BuildRefused


def _text(mapping: Mapping[str, object], key: str, where: str, transaction_id: str) -> str:
    """The string at `mapping[key]`, or a refusal naming `where` (e.g. "exclude rule r1")."""
    value = mapping.get(key)
    if not isinstance(value, str):
        raise BuildRefused(f"{where}: {key} missing or not a string", transaction_id)
    return value
