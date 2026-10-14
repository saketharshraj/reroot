"""Native species shortlist for Aravalli sites, chosen from site conditions.

Rule-based on purpose: species advice must come from a vetted list, never be invented.
Never recommend Vilayati kikar (Prosopis juliflora), the invasive that already dominates
much of the Aravalli scrub.
"""

ROCKY = [
    ("Anogeissus pendula", "Dhau", "Climax tree of Aravalli rocky ridges; tolerates thin soil"),
    ("Boswellia serrata", "Salai", "Grows in rock crevices on dry slopes"),
    ("Senegalia senegal", "Kumat", "Drought-hardy acacia for degraded rocky land"),
    ("Capparis decidua", "Kair", "Leafless desert shrub, survives extreme dry spells"),
]
PLAINS = [
    ("Azadirachta indica", "Neem", "Fast shade, deep roots, liked by communities"),
    ("Holoptelea integrifolia", "Papri", "Native of Aravalli foothill plains"),
    ("Prosopis cineraria", "Khejri", "Nitrogen-fixing, fodder for livestock"),
    ("Ziziphus mauritiana", "Ber", "Hardy fruit tree, income for caretakers"),
]
MOIST = [
    ("Terminalia arjuna", "Arjun", "Needs moisture; good near water bodies"),
    ("Vachellia nilotica", "Desi babool", "Native acacia for seasonally wet ground"),
]
ANYWHERE = [
    ("Balanites aegyptiaca", "Hingot", "Very drought-tolerant native"),
    ("Vachellia leucophloea", "Ronjh", "Common native acacia of Aravalli scrub"),
]


def shortlist(slope_deg: float, frac_bare: float, dist_water_m: float, soil_moisture: float) -> list[dict]:
    if slope_deg > 6 or frac_bare > 0.4:
        picks = ROCKY[:3] + ANYWHERE[:1]
    elif dist_water_m < 500 or soil_moisture > 0.25:
        picks = MOIST + PLAINS[:2]
    else:
        picks = PLAINS[:3] + ANYWHERE[:1]
    return [{"name": n, "local": loc, "why": why} for n, loc, why in picks]
