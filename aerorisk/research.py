"""Published studies on cabin section and accident outcomes, and the colour bands derived from them.

The bands are a summary of two small, old US studies. They are shown only as a labelled,
opt-in view with the basis stated next to the colours; seat-level risk is never estimated.
"""
from __future__ import annotations

STUDIES = [
    {
        "id": "pm2007",
        "name": "Popular Mechanics (2007)",
        "sample": "20 US commercial jet crashes that had both survivors and fatalities, 1971–2007",
        "short": "20 US crashes, 1971–2007",
        "data": "NTSB investigation reports and seating charts",
        "definition": "Front = first/business class (or the front 15% of all-economy cabins); over-wing; "
                      "rear = behind the wing's trailing edge",
        "died_pct": {"front": 51, "middle": 44, "rear": 31},
        "notes": "Rear passengers did better in 11 crashes, front passengers in 5; 3 showed no pattern and 1 "
                 "could not be determined. Published as a survival rate (49% / 56% / 69%).",
        "url": "https://nakedcapitalism.com/2007/07/air-safety-which-seats-have-highest.html",
        "url_label": "Summary of the Popular Mechanics study",
    },
    {
        "id": "time2015",
        "name": "TIME (2015)",
        "sample": "17 US accidents that had both survivors and fatalities, 1985–2000",
        "short": "17 US crashes, 1985–2000",
        "data": "FAA CSRTG Aircraft Accident Database seating charts",
        "definition": "Cabin split into thirds",
        "died_pct": {"front": 38, "middle": 39, "rear": 32},
        "notes": "Middle seats in the rear third had the lowest figure (28%); aisle seats in the middle third the "
                 "highest (44%). The article quotes the FAA: there is no safest seat.",
        "url": "https://time.com/safest-seat-airplane/",
        "url_label": "TIME: The safest seat on an airplane",
    },
]

EXIT_STUDY = {
    "id": "greenwich",
    "name": "University of Greenwich (Prof. Ed Galea)",
    "sample": "105 survivable accidents, 1977–1999, about 1,900 passengers",
    "finding": "Passengers seated within about five rows of a usable exit were most likely to get out. "
               "Aisle seats did better than window seats.",
    "caveat": "Galea: \"There is no magic safest seat… it depends on the nature of the accident.\"",
    "url": "https://ktvz.com/entertainment/cnn-style/2025/01/04/the-survivors-of-recent-crashes-were-sitting-at-the-back-of-the-plane-what-does-that-tell-us-about-airplane-safety/",
    "url_label": "CNN (2025): what recent crashes tell us",
}

# Rear had the lowest fatality figure in BOTH studies. Front vs middle flips between them,
# so they share one band rather than pretending to a three-level ranking.
BANDS = {"front": "higher", "middle": "higher", "rear": "lower"}

BAND_LABEL = {"lower": "Lower in both studies", "higher": "Higher (front and middle don't differ consistently)"}

BASIS = "2 small US studies · 37 crashes · 1971–2007 · low confidence"

CAVEATS = [
    "Small samples: 20 and 17 crashes, all in the US, all before 2007.",
    "Only crashes where some people died and some survived; most accidents are either fully survivable or not survivable.",
    "The type of accident decides the pattern: a tail strike affects the rear, a nose-first impact the front.",
    "Aircraft, seats and cabin materials have improved since these crashes.",
    "The FAA and the researchers quoted say there is no safest seat.",
]


def section_summary() -> dict[str, dict]:
    out = {}
    for sec in ("front", "middle", "rear"):
        out[sec] = {"band": BANDS[sec],
                    "died_pct": {st["id"]: st["died_pct"][sec] for st in STUDIES}}
    return out
