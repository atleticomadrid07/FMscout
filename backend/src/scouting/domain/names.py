"""Name normalization shared by ingestion (building the index) and queries (searching it)."""

from __future__ import annotations

from unidecode import unidecode


def search_name(name: str) -> str:
    """Accent-free, lowercased form for the search index: "Miloš Kerkez" → "milos kerkez"."""
    return " ".join(unidecode(name).lower().split())
