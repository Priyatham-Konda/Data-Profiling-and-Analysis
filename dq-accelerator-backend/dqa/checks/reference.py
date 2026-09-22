"""Bundled reference data.

Deliberately bundled rather than pulled from `pycountry`, which is LGPL 2.1.
Everything here is factual data (ISO codes, postal formats) and carries no
licence obligation.

This is a working subset, not an exhaustive dataset. Expand as engagements
require.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# ISO 4217 currency codes (active, common subset)
# --------------------------------------------------------------------------
CURRENCY_CODES: set[str] = {
    c.lower() for c in [
        "USD", "EUR", "GBP", "JPY", "CNY", "INR", "AUD", "CAD", "CHF", "SEK",
        "NOK", "DKK", "NZD", "SGD", "HKD", "KRW", "MXN", "BRL", "ZAR", "RUB",
        "TRY", "PLN", "THB", "IDR", "MYR", "PHP", "VND", "AED", "SAR", "ILS",
        "CZK", "HUF", "RON", "CLP", "COP", "ARS", "PEN", "EGP", "NGN", "KES",
        "PKR", "BDT", "LKR", "TWD", "UAH", "QAR", "KWD", "BHD", "OMR", "JOD",
    ]
}

# --------------------------------------------------------------------------
# Countries: ISO alpha-2, alpha-3 and common names, all lowercase
# --------------------------------------------------------------------------
_COUNTRIES: list[tuple[str, str, str]] = [
    ("US", "USA", "United States"), ("GB", "GBR", "United Kingdom"),
    ("IN", "IND", "India"), ("CA", "CAN", "Canada"), ("AU", "AUS", "Australia"),
    ("DE", "DEU", "Germany"), ("FR", "FRA", "France"), ("IT", "ITA", "Italy"),
    ("ES", "ESP", "Spain"), ("NL", "NLD", "Netherlands"), ("BE", "BEL", "Belgium"),
    ("CH", "CHE", "Switzerland"), ("AT", "AUT", "Austria"), ("SE", "SWE", "Sweden"),
    ("NO", "NOR", "Norway"), ("DK", "DNK", "Denmark"), ("FI", "FIN", "Finland"),
    ("IE", "IRL", "Ireland"), ("PT", "PRT", "Portugal"), ("PL", "POL", "Poland"),
    ("CZ", "CZE", "Czechia"), ("GR", "GRC", "Greece"), ("RO", "ROU", "Romania"),
    ("HU", "HUN", "Hungary"), ("JP", "JPN", "Japan"), ("CN", "CHN", "China"),
    ("KR", "KOR", "South Korea"), ("SG", "SGP", "Singapore"), ("MY", "MYS", "Malaysia"),
    ("TH", "THA", "Thailand"), ("ID", "IDN", "Indonesia"), ("PH", "PHL", "Philippines"),
    ("VN", "VNM", "Vietnam"), ("HK", "HKG", "Hong Kong"), ("TW", "TWN", "Taiwan"),
    ("NZ", "NZL", "New Zealand"), ("ZA", "ZAF", "South Africa"),
    ("NG", "NGA", "Nigeria"), ("KE", "KEN", "Kenya"), ("EG", "EGY", "Egypt"),
    ("AE", "ARE", "United Arab Emirates"), ("SA", "SAU", "Saudi Arabia"),
    ("IL", "ISR", "Israel"), ("TR", "TUR", "Turkey"), ("RU", "RUS", "Russia"),
    ("UA", "UKR", "Ukraine"), ("BR", "BRA", "Brazil"), ("MX", "MEX", "Mexico"),
    ("AR", "ARG", "Argentina"), ("CL", "CHL", "Chile"), ("CO", "COL", "Colombia"),
    ("PE", "PER", "Peru"), ("PK", "PAK", "Pakistan"), ("BD", "BGD", "Bangladesh"),
    ("LK", "LKA", "Sri Lanka"), ("NP", "NPL", "Nepal"),
]

COUNTRY_ALIASES: dict[str, str] = {}
for _a2, _a3, _name in _COUNTRIES:
    COUNTRY_ALIASES[_a2.lower()] = _a2
    COUNTRY_ALIASES[_a3.lower()] = _a2
    COUNTRY_ALIASES[_name.lower()] = _a2

# Common informal variants.
COUNTRY_ALIASES.update({
    "usa": "US", "u.s.a.": "US", "u.s.": "US", "america": "US",
    "united states of america": "US", "uk": "GB", "u.k.": "GB",
    "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "uae": "AE", "korea": "KR", "republic of korea": "KR",
    "russian federation": "RU", "viet nam": "VN", "holland": "NL",
    "czech republic": "CZ", "deutschland": "DE",
})

# --------------------------------------------------------------------------
# US states
# --------------------------------------------------------------------------
US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
}

# ZIP prefix -> state, for the most unambiguous ranges only. A partial map is
# fine: an absent prefix simply means no verdict, never a false positive.
US_ZIP_BY_STATE_PREFIX: dict[str, str] = {}
_ZIP_RANGES: list[tuple[int, int, str]] = [
    (100, 149, "NY"), (150, 196, "PA"), (200, 205, "DC"), (206, 219, "MD"),
    (220, 246, "VA"), (247, 268, "WV"), (270, 289, "NC"), (290, 299, "SC"),
    (300, 319, "GA"), (320, 349, "FL"), (350, 369, "AL"), (370, 385, "TN"),
    (386, 397, "MS"), (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"),
    (480, 499, "MI"), (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"),
    (570, 577, "SD"), (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"),
    (630, 658, "MO"), (660, 679, "KS"), (680, 693, "NE"), (700, 714, "LA"),
    (716, 729, "AR"), (730, 749, "OK"), (750, 799, "TX"), (800, 816, "CO"),
    (820, 831, "WY"), (832, 838, "ID"), (840, 847, "UT"), (850, 865, "AZ"),
    (870, 884, "NM"), (889, 898, "NV"), (900, 961, "CA"), (967, 968, "HI"),
    (970, 979, "OR"), (980, 994, "WA"), (995, 999, "AK"),
]
for _lo, _hi, _st in _ZIP_RANGES:
    for _p in range(_lo, _hi + 1):
        US_ZIP_BY_STATE_PREFIX[f"{_p:03d}"] = _st

# --------------------------------------------------------------------------
# Email domains used for typo detection
# --------------------------------------------------------------------------
COMMON_EMAIL_DOMAINS: set[str] = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "live.com", "msn.com", "comcast.net", "verizon.net",
    "me.com", "mac.com", "protonmail.com", "gmx.com", "yandex.com",
    "yahoo.co.uk", "hotmail.co.uk", "googlemail.com", "rediffmail.com",
    "zoho.com", "mail.com", "ymail.com", "btinternet.com",
}

# --------------------------------------------------------------------------
# Postal formats by country
# --------------------------------------------------------------------------
POSTCODE_PATTERNS: dict[str, re.Pattern] = {
    "US": re.compile(r"^\d{5}(-\d{4})?$"),
    "CA": re.compile(r"^[A-Z]\d[A-Z]\s?\d[A-Z]\d$", re.I),
    "GB": re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I),
    "IN": re.compile(r"^\d{6}$"),
    "DE": re.compile(r"^\d{5}$"),
    "FR": re.compile(r"^\d{5}$"),
    "IT": re.compile(r"^\d{5}$"),
    "ES": re.compile(r"^\d{5}$"),
    "NL": re.compile(r"^\d{4}\s?[A-Z]{2}$", re.I),
    "AU": re.compile(r"^\d{4}$"),
    "NZ": re.compile(r"^\d{4}$"),
    "JP": re.compile(r"^\d{3}-?\d{4}$"),
    "BR": re.compile(r"^\d{5}-?\d{3}$"),
    "SG": re.compile(r"^\d{6}$"),
    "ZA": re.compile(r"^\d{4}$"),
    "PL": re.compile(r"^\d{2}-\d{3}$"),
    "SE": re.compile(r"^\d{3}\s?\d{2}$"),
    "CH": re.compile(r"^\d{4}$"),
    "MX": re.compile(r"^\d{5}$"),
}


def normalise_country(value: str) -> str | None:
    """Return the ISO alpha-2 code for a country value, or None."""
    if not value:
        return None
    return COUNTRY_ALIASES.get(str(value).strip().lower())


def postcode_matches_country(postcode: str, country: str) -> bool:
    """True when the postcode is plausible for the country.

    Returns True whenever no verdict is possible -- an unknown country or a
    country with no pattern on file must never produce a false positive.
    """
    code = normalise_country(country)
    if code is None:
        return True
    pattern = POSTCODE_PATTERNS.get(code)
    if pattern is None:
        return True
    value = str(postcode).strip()
    if not value:
        return True
    return bool(pattern.match(value))
