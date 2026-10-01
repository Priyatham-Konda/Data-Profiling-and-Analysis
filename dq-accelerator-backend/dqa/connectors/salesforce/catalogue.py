"""Which Salesforce objects to offer in the picker.

An org exposes several hundred objects, most of them plumbing: field
history, sharing rows, feeds, change events, setup and metadata types.
Offering those alongside Account and Contact buries the ones worth
assessing, so the default list keeps business objects only. `include=all`
shows the rest.
"""
from __future__ import annotations

from ... import config

# Name endings of Salesforce's system and infrastructure objects. Careful
# with this list: "Event" alone must never appear, because Event is the
# calendar object and is business data.
_SYSTEM_ENDINGS = (
    "History",       # AccountHistory, Invoice__History
    "Share",         # AccountShare, Invoice__Share
    "Feed",          # AccountFeed, Invoice__Feed
    "ChangeEvent",   # AccountChangeEvent
    "__Tag",
    "__mdt",         # custom metadata types
    "__e",           # platform events
    "__b",           # big objects
    "__x",           # external objects -- data living outside Salesforce
    "__hd",          # historical trending
)


def assessable(sobject: dict) -> bool:
    """Can this object be queried and downloaded at all?"""
    return bool(sobject.get("queryable")) and bool(sobject.get("retrieveable", True))


def visible_by_default(sobject: dict) -> bool:
    """A business object a user would recognise and want assessed."""
    if not assessable(sobject):
        return False
    if sobject.get("deprecatedAndHidden") or sobject.get("customSetting"):
        return False
    # Objects without page layouts are almost always system objects.
    if not sobject.get("layoutable"):
        return False
    return not sobject.get("name", "").endswith(_SYSTEM_ENDINGS)


def build(sobjects: list[dict], counts: dict[str, int], include_all: bool) -> dict:
    """The body of GET /salesforce/connections/{id}/objects."""
    candidates = [s for s in sobjects if assessable(s)]
    shown = candidates if include_all else [s for s in candidates if visible_by_default(s)]
    suggested = set(config.SF_SUGGESTED_OBJECTS)

    objects = [
        {
            "name": s["name"],
            "label": s.get("label") or s["name"],
            "custom": bool(s.get("custom")),
            "recordCount": counts.get(s["name"]),
            "suggested": s["name"] in suggested,
        }
        for s in shown
    ]
    objects.sort(key=lambda o: (o["label"].casefold(), o["name"]))
    return {"objects": objects, "hiddenCount": len(candidates) - len(shown)}


def labels(sobjects: list[dict]) -> dict[str, str]:
    """name -> label for every object that could be assessed."""
    return {s["name"]: s.get("label") or s["name"] for s in sobjects if assessable(s)}
