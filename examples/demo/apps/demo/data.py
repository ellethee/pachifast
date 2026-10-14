"""Static data of the demo: lookups (countries, cities, categories) and the seed of the sample records."""

import random

COUNTRIES: list[dict] = [
    {"value": 1, "label": "Italia"},
    {"value": 2, "label": "Francia"},
    {"value": 3, "label": "Germania"},
    {"value": 4, "label": "Spagna"},
]

CITIES: list[dict] = [
    {"value": 1, "label": "Roma", "countryId": 1},
    {"value": 2, "label": "Milano", "countryId": 1},
    {"value": 3, "label": "Napoli", "countryId": 1},
    {"value": 4, "label": "Parigi", "countryId": 2},
    {"value": 5, "label": "Lione", "countryId": 2},
    {"value": 6, "label": "Berlino", "countryId": 3},
    {"value": 7, "label": "Monaco", "countryId": 3},
    {"value": 8, "label": "Amburgo", "countryId": 3},
    {"value": 9, "label": "Madrid", "countryId": 4},
    {"value": 10, "label": "Barcellona", "countryId": 4},
]

CATEGORIES: list[dict] = [
    {"value": "tools", "label": "Strumenti"},
    {"value": "books", "label": "Libri"},
    {"value": "toys", "label": "Giochi"},
    {"value": "food", "label": "Alimentari"},
    {"value": "clothes", "label": "Abbigliamento"},
]

STATUSES: list[dict] = [
    {"value": "draft", "label": "Bozza"},
    {"value": "active", "label": "Attivo"},
    {"value": "archived", "label": "Archiviato"},
]

_ADJECTIVES = ["Piccolo", "Grande", "Rapido", "Antico", "Moderno", "Verde", "Rosso", "Leggero", "Robusto", "Elegante"]
_NOUNS = ["martello", "libro", "robot", "pane", "giacca", "puzzle", "cacciavite", "romanzo", "casco", "formaggio"]


def sample_items(count: int = 120) -> list[dict]:
    """``count`` reproducible sample records (fixed seed): same data on every database."""
    rng = random.Random(42)
    items = []
    for number in range(1, count + 1):
        city = rng.choice(CITIES)
        items.append(
            {
                "name": f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} {number}",
                "category": rng.choice(CATEGORIES)["value"],
                "status": rng.choice(STATUSES)["value"],
                "price": round(rng.uniform(1, 500), 2),
                "quantity": rng.randint(0, 200),
                "active": rng.random() > 0.2,
                "country_id": city["countryId"],
                "city_id": city["value"],
                "notes": None if rng.random() > 0.3 else f"Nota di prova {number}",
            }
        )
    return items
