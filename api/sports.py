"""
api/sports.py — sports keyword filtering and category classification.
Isolated here so it can be imported by routes and tested independently.
"""
import re

SPORTS_KEYWORDS = [
    r"\bnfl\b", r"\bnba\b", r"\bmlb\b", r"\bnhl\b", r"\bmls\b", r"\bufc\b", r"\bpga\b",
    r"\bfootball\b", r"\bbasketball\b", r"\bbaseball\b", r"\bhockey\b", r"\bsoccer\b",
    r"\btennis\b", r"\bgolf\b", r"\boxing\b", r"\bmma\b",
    r"\bsuper bowl\b", r"\bworld series\b", r"\bnba finals\b", r"\bstanley cup\b",
    r"\bchampions league\b", r"\bpremier league\b", r"\bla liga\b", r"\bbundesliga\b",
    r"\bserie a\b", r"\bligue 1\b", r"\bworld cup\b", r"\bcopa america\b",
    r"\bquarterback\b", r"\btouchdown\b", r"\bhomerun\b", r"\bslam dunk\b",
    r"\blakers\b", r"\bceltics\b", r"\bpatriots\b", r"\bchiefs\b", r"\byankees\b",
    r"\bdodgers\b", r"\bbarcelona\b", r"\breal madrid\b", r"\bjuventus\b", r"\bpsg\b",
    r"\barsenal\b", r"\bliverpool\b", r"\bman city\b", r"\bchelsea\b",
    r"\bwimbledon\b", r"\bus open\b", r"\bfrench open\b", r"\baustralia open\b",
    r"\bformula 1\b", r"\bf1\b", r"\bnascar\b", r"\bindycar\b",
    r"\bboxing\b", r"\bwrestling\b", r"\bolympics\b",
    r"\bplayoffs?\b", r"\bchampionship\b", r"\bleague\b", r"\btournament\b",
]

SPORTS_TAG_KEYWORDS = [
    "sport", "soccer", "nba", "nfl", "nhl", "mlb", "tennis", "golf", "ufc",
    "football", "basketball", "baseball", "hockey", "mma", "boxing", "racing",
    "f1", "olympics", "rugby", "cricket",
]


def is_sports_market(title: str, tags: list) -> bool:
    t          = title.lower()
    tag_labels = " ".join(tg.get("label", "").lower() for tg in (tags or []))
    if any(re.search(kw, t) for kw in SPORTS_KEYWORDS):
        return True
    if any(kw in tag_labels for kw in SPORTS_TAG_KEYWORDS):
        return True
    return False


def get_sport_category(title: str, tags: list) -> str:
    t       = title.lower()
    tag_str = " ".join(tg.get("label", "").lower() for tg in (tags or []))

    if re.search(r"\bnfl\b|super bowl|touchdown|quarterback", t) or "nfl" in tag_str:
        return "NFL"
    if re.search(r"\bnba\b|basketball|nba finals", t) or "nba" in tag_str or "basketball" in tag_str:
        return "NBA"
    if re.search(r"\bmlb\b|baseball|world series", t) or "mlb" in tag_str or "baseball" in tag_str:
        return "MLB"
    if re.search(r"\bnhl\b|hockey|stanley cup", t) or "nhl" in tag_str or "hockey" in tag_str:
        return "NHL"
    if re.search(
        r"\bpremier league\b|\bla liga\b|\bbundesliga\b|\bserie a\b|"
        r"\bligue 1\b|\bchampions league\b|\bmls\b|\bsoccer\b", t
    ) or "soccer" in tag_str or "football" in tag_str:
        return "Soccer"
    if re.search(r"\btennis\b|\bwimbledon\b|\bus open\b|\bfrench open\b", t) or "tennis" in tag_str:
        return "Tennis"
    if re.search(r"\bufc\b|\bmma\b|\boxing\b", t) or "ufc" in tag_str or "mma" in tag_str:
        return "UFC / MMA"
    if re.search(r"\bgolf\b|\bpga\b|\bmasters\b", t) or "golf" in tag_str:
        return "Golf"
    if re.search(r"\bf1\b|\bformula 1\b|\bnascar\b|\bracing\b", t) or "f1" in tag_str or "racing" in tag_str:
        return "Racing"
    if re.search(r"\bolympics\b|\bolympic\b", t) or "olympics" in tag_str:
        return "Olympics"
    if re.search(r"\brugby\b|\bcricket\b", t) or "rugby" in tag_str or "cricket" in tag_str:
        return "Rugby / Cricket"
    if re.search(r"\bcollege\b|\bncaa\b|\bmarch madness\b", t) or "ncaa" in tag_str:
        return "College Sports"
    return "Other Sports"
