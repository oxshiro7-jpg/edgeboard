"""Market definitions and settlement probabilities derived from score models.

Market key grammar (colon separated):
    1X2:H | 1X2:D | 1X2:A
    DC:1X | DC:X2 | DC:12
    DNB:H | DNB:A
    OU:<line>:over|under                total match goals
    TT:H|A:<line>:over|under            team total goals
    BTTS:yes | BTTS:no
    AH:H|A:<line>                       Asian handicap, line from that team's view
    EH:H|D|A:<hcap>                     European (3-way) handicap applied to home
    FH1X2:H|D|A, FHDC:1X|X2|12, FHOU:<line>:over|under
    SHOU:<line>:over|under              second-half goals
    CARDS:<line>:over|under             total bookings (cards counted 1 each)
    CORNERS:<line>:over|under           total corners
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .goals import ScoreMatrix, poisson_pmf
from .odds import SettlementDistribution

FAMILY = {
    "1X2": "result", "DC": "result", "DNB": "result", "AH": "handicap", "EH": "handicap",
    "OU": "goals", "TT": "goals", "BTTS": "btts",
    "FH1X2": "first_half", "FHDC": "first_half", "FHOU": "first_half", "SHOU": "second_half",
    "CARDS": "cards", "CORNERS": "corners",
}


def market_type(key: str) -> str:
    return key.split(":")[0]


def family(key: str) -> str:
    return FAMILY.get(market_type(key), "other")


def label(key: str, home: str, away: str) -> str:
    p = key.split(":")
    t = p[0]
    side = {"H": home, "A": away, "D": "Draw"}
    if t in ("1X2", "FH1X2"):
        base = "Draw" if p[1] == "D" else f"{side[p[1]]} to win"
        return ("1st Half: " if t == "FH1X2" else "") + base
    if t in ("DC", "FHDC"):
        txt = {"1X": f"{home} or Draw", "X2": f"{away} or Draw", "12": f"{home} or {away}"}[p[1]]
        return ("1st Half: " if t == "FHDC" else "") + f"Double Chance: {txt}"
    if t == "DNB":
        return f"{side[p[1]]} Draw No Bet"
    if t in ("OU", "FHOU", "SHOU"):
        pre = {"OU": "", "FHOU": "1st Half ", "SHOU": "2nd Half "}[t]
        return f"{pre}{p[2].capitalize()} {p[1]} Goals"
    if t == "TT":
        return f"{side[p[1]]} {p[3].capitalize()} {p[2]} Team Goals"
    if t == "BTTS":
        return f"Both Teams To Score: {p[1].capitalize()}"
    if t == "AH":
        line = float(p[2])
        return f"{side[p[1]]} {line:+g} Asian Handicap"
    if t == "EH":
        h = int(float(p[2]))
        return f"European Handicap ({home} {h:+d}): {side[p[1]]}"
    if t == "CARDS":
        return f"{p[2].capitalize()} {p[1]} Cards"
    if t == "CORNERS":
        return f"{p[2].capitalize()} {p[1]} Corners"
    return key


def complete_market(key: str) -> Optional[list[str]]:
    """Keys forming the complete book this selection belongs to (for no-vig
    probabilities). None when the market is not a closed set on its own."""
    p = key.split(":")
    t = p[0]
    if t in ("1X2", "FH1X2"):
        return [f"{t}:H", f"{t}:D", f"{t}:A"]
    if t in ("OU", "FHOU", "SHOU", "CARDS", "CORNERS"):
        return [f"{t}:{p[1]}:over", f"{t}:{p[1]}:under"]
    if t == "TT":
        return [f"TT:{p[1]}:{p[2]}:over", f"TT:{p[1]}:{p[2]}:under"]
    if t == "BTTS":
        return ["BTTS:yes", "BTTS:no"]
    if t == "DNB":
        return ["DNB:H", "DNB:A"]
    if t == "AH":
        line = float(p[2])
        other = "A" if p[1] == "H" else "H"
        mine = f"AH:{p[1]}:{_fmt(line)}"
        opp = f"AH:{other}:{_fmt(-line)}"
        return [mine, opp] if p[1] == "H" else [opp, mine]
    if t == "EH":
        return [f"EH:H:{p[2]}", f"EH:D:{p[2]}", f"EH:A:{p[2]}"]
    return None


def _fmt(x: float) -> str:
    return f"{x:+g}" if x != 0 else "0"


def goal_diff_distribution(m: ScoreMatrix) -> dict[int, float]:
    out: dict[int, float] = {}
    for i, row in enumerate(m):
        for j, p in enumerate(row):
            out[i - j] = out.get(i - j, 0.0) + p
    return out


def _binary(p: float) -> SettlementDistribution:
    return SettlementDistribution(win=p, loss=1 - p)


def _ah_single(gd: dict[int, float], line: float) -> tuple[float, float, float]:
    w = pu = lo = 0.0
    for d, p in gd.items():
        adj = d + line
        if adj > 1e-9:
            w += p
        elif adj < -1e-9:
            lo += p
        else:
            pu += p
    return w, pu, lo


def asian_handicap(gd_team: dict[int, float], line: float) -> SettlementDistribution:
    """gd_team: distribution of (team goals - opponent goals)."""
    frac = abs(line * 4) % 2
    if frac == 1:   # quarter line: split stake across line-0.25 and line+0.25
        win = half_win = push = half_loss = loss = 0.0
        for d, p in gd_team.items():
            r1 = _settle(d + line - 0.25)
            r2 = _settle(d + line + 0.25)
            combo = tuple(sorted((r1, r2)))
            if combo == ("W", "W"):
                win += p
            elif combo == ("P", "W"):
                half_win += p
            elif combo == ("L", "P"):
                half_loss += p
            elif combo == ("L", "L"):
                loss += p
            elif combo == ("P", "P"):
                push += p
            else:  # ("L","W") impossible for adjacent quarter lines
                push += p
        return SettlementDistribution(win, half_win, push, half_loss, loss)
    w, pu, lo = _ah_single(gd_team, line)
    return SettlementDistribution(win=w, push=pu, loss=lo)


def _settle(adj: float) -> str:
    return "W" if adj > 1e-9 else "L" if adj < -1e-9 else "P"


def _over_prob_matrix(m: ScoreMatrix, line: float, which: str = "total") -> float:
    p = 0.0
    for i, row in enumerate(m):
        for j, x in enumerate(row):
            g = i + j if which == "total" else i if which == "H" else j
            if g > line:
                p += x
    return p


def _poisson_over(mean: float, line: float, max_k: int = 40) -> float:
    return sum(poisson_pmf(k, mean) for k in range(max_k) if k > line)


@dataclass
class MarketInputs:
    ft: ScoreMatrix
    fh: ScoreMatrix
    sh: ScoreMatrix
    cards_mean: Optional[float] = None
    corners_mean: Optional[float] = None


def settlement(key: str, mi: MarketInputs) -> Optional[SettlementDistribution]:
    """Settlement distribution for a market key, or None if the model cannot
    price it (e.g. cards without referee data)."""
    p = key.split(":")
    t = p[0]
    if t in ("1X2", "FH1X2", "DC", "FHDC"):
        m = mi.ft if t in ("1X2", "DC") else mi.fh
        h = d = a = 0.0
        for i, row in enumerate(m):
            for j, x in enumerate(row):
                if i > j:
                    h += x
                elif i == j:
                    d += x
                else:
                    a += x
        if t in ("1X2", "FH1X2"):
            return _binary({"H": h, "D": d, "A": a}[p[1]])
        return _binary({"1X": h + d, "X2": d + a, "12": h + a}[p[1]])
    if t == "DNB":
        gd = goal_diff_distribution(mi.ft)
        if p[1] == "A":
            gd = {-k: v for k, v in gd.items()}
        return asian_handicap(gd, 0.0)
    if t == "AH":
        gd = goal_diff_distribution(mi.ft)
        if p[1] == "A":
            gd = {-k: v for k, v in gd.items()}
        return asian_handicap(gd, float(p[2]))
    if t == "EH":
        hcap = int(float(p[2]))
        gd = goal_diff_distribution(mi.ft)
        h = sum(v for d, v in gd.items() if d + hcap > 0)
        dr = sum(v for d, v in gd.items() if d + hcap == 0)
        a = 1 - h - dr
        return _binary({"H": h, "D": dr, "A": a}[p[1]])
    if t in ("OU", "FHOU", "SHOU"):
        m = {"OU": mi.ft, "FHOU": mi.fh, "SHOU": mi.sh}[t]
        over = _over_prob_matrix(m, float(p[1]))
        return _binary(over if p[2] == "over" else 1 - over)
    if t == "TT":
        over = _over_prob_matrix(mi.ft, float(p[2]), p[1])
        return _binary(over if p[3] == "over" else 1 - over)
    if t == "BTTS":
        yes = sum(x for i, row in enumerate(mi.ft) for j, x in enumerate(row) if i > 0 and j > 0)
        return _binary(yes if p[1] == "yes" else 1 - yes)
    if t == "CARDS":
        if mi.cards_mean is None:
            return None
        over = _poisson_over(mi.cards_mean, float(p[1]))
        return _binary(over if p[2] == "over" else 1 - over)
    if t == "CORNERS":
        if mi.corners_mean is None:
            return None
        over = _poisson_over(mi.corners_mean, float(p[1]))
        return _binary(over if p[2] == "over" else 1 - over)
    return None


def standard_keys() -> list[str]:
    """Markets priced by the model for every fixture (whether or not a
    bookmaker price exists)."""
    keys = ["1X2:H", "1X2:D", "1X2:A", "DC:1X", "DC:X2", "DC:12", "DNB:H", "DNB:A",
            "BTTS:yes", "BTTS:no"]
    for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
        keys += [f"OU:{line}:over", f"OU:{line}:under"]
    for side in ("H", "A"):
        for line in ("0.5", "1.5", "2.5"):
            keys += [f"TT:{side}:{line}:over", f"TT:{side}:{line}:under"]
    for line in (-1, -0.75, -0.5, -0.25, 0.25, 0.5, 0.75, 1):
        keys += [f"AH:H:{_fmt(line)}", f"AH:A:{_fmt(-line)}"]
    keys += ["EH:H:-1", "EH:D:-1", "EH:A:-1"]
    keys += ["FH1X2:H", "FH1X2:D", "FH1X2:A", "FHDC:1X", "FHDC:X2", "FHDC:12",
             "FHOU:0.5:over", "FHOU:0.5:under", "FHOU:1.5:over", "FHOU:1.5:under",
             "SHOU:0.5:over", "SHOU:0.5:under", "SHOU:1.5:over", "SHOU:1.5:under"]
    return list(dict.fromkeys(keys))
