"""
Country-localised packs for discovery and contact heuristics.

Discovery stays separate from extraction/filtering behaviour, but both are now
loaded from the same localisation package so country-specific logic lives next
to the rest of that country's vocabulary.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from gc_contacts.localisation.common import DEFAULT_CONTACT_PACK, DEFAULT_DISCOVERY_PACK
from gc_contacts.localisation.france import FRANCE_CONTACT_PACK, FRANCE_DISCOVERY_PACK
from gc_contacts.localisation.italy import ITALY_CONTACT_PACK, ITALY_DISCOVERY_PACK


COUNTRY_LOCALISATION_PACKS: dict[str, dict[str, dict[str, Any]]] = {
    "FR": {
        "contact": FRANCE_CONTACT_PACK,
        "discovery": FRANCE_DISCOVERY_PACK,
    },
    "IT": {
        "contact": ITALY_CONTACT_PACK,
        "discovery": ITALY_DISCOVERY_PACK,
    },
}

COUNTRY_DISCOVERY_PACKS: dict[str, dict[str, Any]] = {
    code: dict(pack.get("discovery", {}))
    for code, pack in COUNTRY_LOCALISATION_PACKS.items()
}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _merge_unique(base: list[Any], extra: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for item in list(base) + list(extra):
        marker = (type(item).__name__, repr(item))
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(deepcopy(item))
    return merged


def _merge_pack(base: Any, extra: Any) -> Any:
    if isinstance(base, dict) and isinstance(extra, dict):
        merged = {key: deepcopy(value) for key, value in base.items()}
        for key, value in extra.items():
            if key in merged:
                merged[key] = _merge_pack(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    if isinstance(base, (list, tuple, set)) or isinstance(extra, (list, tuple, set)):
        return _merge_unique(_as_list(base), _as_list(extra))
    return deepcopy(extra)


def _country_pack(country: str | None) -> dict[str, dict[str, Any]]:
    return COUNTRY_LOCALISATION_PACKS.get(str(country or "").strip().upper(), {})


def get_country_discovery_pack(country: str | None) -> dict[str, Any]:
    return _merge_pack(DEFAULT_DISCOVERY_PACK, _country_pack(country).get("discovery", {}))


def get_country_contact_pack(country: str | None) -> dict[str, Any]:
    return _merge_pack(DEFAULT_CONTACT_PACK, _country_pack(country).get("contact", {}))
