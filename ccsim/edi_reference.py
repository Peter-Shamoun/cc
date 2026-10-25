"""Reference tables behind the mock EDI: who the patients are, where they live, what their tests are for.

  names       ccsim/data/census2010_surnames.csv: the 10,000 most common surnames of the 2010 Census with their
              counts and Hispanic share (weighted by the tenant state's Hispanic share);
              ccsim/data/ssa_first_names.csv: the 400 most common SSA first names per sex and birth decade, with
              counts. Draws follow the real frequencies, so a month of claims shows thousands of distinct names.
  places      city / ZIP pairs inside each lab's referral area (plus a few border towns), weighted by population,
              with the area codes in use there; streets are named after common stems and surnames, each stem with
              the suffixes it really takes (MAIN ST, COUNTRY CLUB RD, CREEKSIDE DR)
  diagnoses   ccsim/data/icd10cm_lab_dx_2026.csv: CMS FY2026 billable ICD-10-CM codes grouped into clinical domains
              (built from icd10cm_codes_2026.txt; every code was checked against that file), each with sex and age
              limits; tests map to the domains that justify them, so a line pointer always names a fitting diagnosis
  NCCI        lab panel components (CMP, BMP, renal, hepatic, lipid, CBC, obstetric, hepatitis, urinalysis): a lab's
              claim scrubber strips a component billed with its panel before the claim goes out
All people are fictional; names and places are common ones, combined at random.
"""
from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from itertools import accumulate
from pathlib import Path

DATA = Path(__file__).with_name("data")


def _rows(name: str):
    with open(DATA / name) as fh:
        yield from csv.DictReader(line for line in fh if not line.startswith("#"))


# --- surnames ---------------------------------------------------------------------------------------
HISPANIC_SHARE_BY_STATE = {"NC": .11, "TX": .40}          # Census 2020 share of residents
US_HISPANIC_SHARE = .19
# real surnames that read as test data in a claim file: left out
_NOT_USED = {"MOCK", "TEST", "SAMPLE", "FAKE", "DUMMY", "BOGUS", "NULL", "BLANK", "SPAM"}
_SURNAME_ROWS = [(r["name"], int(r["count"]), float(r["pct_hispanic"]) / 100) for r in _rows("census2010_surnames.csv")
                 if r["name"] not in _NOT_USED]
SURNAMES = [n for n, _, _ in _SURNAME_ROWS]
SURNAME_HISPANIC = {n: h for n, _, h in _SURNAME_ROWS}
assert len(set(SURNAMES)) == len(SURNAMES), "duplicate surname"


def _state_ratios(state: str) -> tuple[float, float]:
    share = HISPANIC_SHARE_BY_STATE.get(state, US_HISPANIC_SHARE)
    return share / US_HISPANIC_SHARE, (1 - share) / (1 - US_HISPANIC_SHARE)


def surname_table(state: str) -> list[float]:
    """Cumulative weights over SURNAMES for patients in this state (Hispanic bearers re-weighted to its share)."""
    bh, bo = _state_ratios(state)
    return list(accumulate(c * (h * bh + (1 - h) * bo) for _, c, h in _SURNAME_ROWS))


def is_hispanic(r, surname: str, state: str) -> bool:
    """Whether this bearer of the surname is of Hispanic origin, given the state."""
    h = SURNAME_HISPANIC.get(surname, 0.0)
    bh, bo = _state_ratios(state)
    return r.random() < h * bh / (h * bh + (1 - h) * bo) if h else False


# --- first names --------------------------------------------------------------------------------------
HISPANIC_FIRST_NAMES = {
    "F": """MARIA GUADALUPE ROSA ANA JUANA CARMEN MARTHA LETICIA VERONICA ALEJANDRA GABRIELA ELENA YOLANDA PATRICIA LAURA
        ADRIANA SILVIA BLANCA CLAUDIA ESPERANZA SOFIA CAMILA VALERIA XIMENA DANIELA LUCIA MARISOL IRMA OFELIA ALMA""",
    "M": """JOSE JUAN LUIS CARLOS JESUS MIGUEL ANTONIO FRANCISCO JORGE PEDRO ALEJANDRO RICARDO MANUEL RAMON JAVIER RAUL
        FERNANDO ROBERTO MARIO ARTURO EDUARDO SERGIO HECTOR OSCAR RAFAEL ARMANDO ENRIQUE ALFREDO SANTIAGO MATEO""",
}
P_SPANISH_FIRST_NAME = .4                  # Hispanic patients given a traditional Spanish name (else the SSA mix)
MIDDLE_INITIALS = "AJMLERDKSCBTGNWHPFVO"   # rough frequency order of middle initials
P_MIDDLE_INITIAL = .45                     # share of 837 patient names with NM105


@dataclass(frozen=True)
class WeightedList:
    items: tuple
    cum: tuple

    @classmethod
    def zipf(cls, items, s: float = .85) -> "WeightedList":
        items = tuple(dict.fromkeys(items))
        return cls(items, tuple(accumulate(1 / (i + 1) ** s for i in range(len(items)))))

    @classmethod
    def counts(cls, pairs) -> "WeightedList":
        pairs = list(pairs)
        return cls(tuple(n for n, _ in pairs), tuple(accumulate(c for _, c in pairs)))

    def pick(self, r) -> str:
        return self.items[bisect.bisect_left(self.cum, r.random() * self.cum[-1])]


def _first_names() -> dict:
    by = {}
    for row in _rows("ssa_first_names.csv"):
        by.setdefault((row["sex"], int(row["decade"])), []).append((row["name"], int(row["count"])))
    return {k: WeightedList.counts(v) for k, v in by.items()}


_FIRST = _first_names()
_SPANISH_FIRST = {k: WeightedList.zipf(v.split(), .7) for k, v in HISPANIC_FIRST_NAMES.items()}


def first_name(r, sex: str, birth_year: int, hispanic: bool) -> str:
    if hispanic and r.random() < P_SPANISH_FIRST_NAME:
        return _SPANISH_FIRST[sex].pick(r)
    return _FIRST[(sex, min(max(birth_year // 10 * 10, 1920), 2010))].pick(r)


# --- places -----------------------------------------------------------------------------------------
# (city, state, ZIPs, relative weight, area codes). The lab's own county is over-weighted (patient service centers).
PLACES = {
    "NC": [
        ("BURLINGTON", "NC", "27215 27217", 40, "336 743"), ("GRAHAM", "NC", "27253", 10, "336"),
        ("MEBANE", "NC", "27302", 8, "919"), ("ELON", "NC", "27244", 3, "336"), ("GIBSONVILLE", "NC", "27249", 3, "336"),
        ("HAW RIVER", "NC", "27258", 2, "336"), ("GREENSBORO", "NC", "27401 27403 27405 27406 27407 27408 27409 27410 27455",
                                                45, "336 743"),
        ("HIGH POINT", "NC", "27260 27262 27265", 18, "336"), ("JAMESTOWN", "NC", "27282", 3, "336"),
        ("WINSTON SALEM", "NC", "27101 27103 27104 27105 27106 27107 27127", 35, "336 743"),
        ("KERNERSVILLE", "NC", "27284", 5, "336"), ("CLEMMONS", "NC", "27012", 3, "336"),
        ("REIDSVILLE", "NC", "27320", 5, "336"), ("EDEN", "NC", "27288", 3, "336"), ("ASHEBORO", "NC", "27203 27205", 6, "336"),
        ("LEXINGTON", "NC", "27292 27295", 5, "336"), ("THOMASVILLE", "NC", "27360", 4, "336"),
        ("YANCEYVILLE", "NC", "27379", 1, "336"), ("ROXBORO", "NC", "27573", 2, "336"),
        ("HILLSBOROUGH", "NC", "27278", 3, "919"), ("CHAPEL HILL", "NC", "27514 27516 27517", 8, "919 984"),
        ("CARRBORO", "NC", "27510", 2, "919"), ("DURHAM", "NC", "27701 27703 27704 27705 27707 27712 27713", 25, "919 984"),
        ("RALEIGH", "NC", "27603 27604 27606 27607 27609 27610 27612 27613 27614 27615 27616 27617", 40, "919 984"),
        ("CARY", "NC", "27511 27513 27518 27519", 14, "919 984"), ("APEX", "NC", "27502 27523 27539", 6, "919 984"),
        ("MORRISVILLE", "NC", "27560", 3, "919"), ("WAKE FOREST", "NC", "27587", 5, "919 984"),
        ("GARNER", "NC", "27529", 4, "919"), ("FUQUAY VARINA", "NC", "27526", 3, "919"),
        ("HOLLY SPRINGS", "NC", "27540", 3, "919"), ("CLAYTON", "NC", "27520 27527", 3, "919"),
        ("SMITHFIELD", "NC", "27577", 2, "919"), ("SANFORD", "NC", "27330 27332", 3, "919"),
        ("SILER CITY", "NC", "27344", 1, "919"), ("PITTSBORO", "NC", "27312", 1, "919"), ("OXFORD", "NC", "27565", 1, "919"),
        ("HENDERSON", "NC", "27536", 2, "252"),
        ("CHARLOTTE", "NC", "28202 28203 28204 28205 28206 28207 28208 28209 28210 28211 28212 28213 28214 28215 28216 "
                            "28217 28226 28227 28262 28269 28270 28273 28277", 45, "704 980"),
        ("MATTHEWS", "NC", "28104 28105", 4, "704"), ("HUNTERSVILLE", "NC", "28078", 4, "704 980"),
        ("CONCORD", "NC", "28025 28027", 6, "704 980"), ("KANNAPOLIS", "NC", "28081 28083", 4, "704"),
        ("GASTONIA", "NC", "28052 28054 28056", 5, "704"), ("MOORESVILLE", "NC", "28115 28117", 5, "704 980"),
        ("SALISBURY", "NC", "28144 28146 28147", 4, "704"), ("STATESVILLE", "NC", "28625 28677", 4, "704"),
        ("HICKORY", "NC", "28601 28602", 4, "828"), ("ASHEVILLE", "NC", "28801 28803 28804 28805 28806", 7, "828"),
        ("BOONE", "NC", "28607", 1, "828"), ("MORGANTON", "NC", "28655", 2, "828"),
        ("FAYETTEVILLE", "NC", "28301 28303 28304 28305 28311 28314", 10, "910 472"),
        ("WILMINGTON", "NC", "28401 28403 28405 28409 28411 28412", 8, "910"), ("JACKSONVILLE", "NC", "28540 28546", 4, "910"),
        ("LUMBERTON", "NC", "28358", 2, "910"), ("GREENVILLE", "NC", "27834 27858", 5, "252"),
        ("ROCKY MOUNT", "NC", "27801 27803 27804", 3, "252"), ("WILSON", "NC", "27893 27896", 3, "252"),
        ("GOLDSBORO", "NC", "27530 27534", 3, "919"), ("NEW BERN", "NC", "28560 28562", 2, "252"),
        ("DANVILLE", "VA", "24540 24541", 3, "434"), ("MARTINSVILLE", "VA", "24112", 1, "276"),
        ("SOUTH BOSTON", "VA", "24592", 1, "434"), ("ROCK HILL", "SC", "29730 29732", 2, "803"),
        ("FORT MILL", "SC", "29715", 2, "803"),
    ],
    "TX": [
        ("AUSTIN", "TX", "78701 78702 78703 78704 78705 78721 78722 78723 78724 78727 78728 78729 78731 78734 78735 78736 "
                         "78737 78739 78741 78744 78745 78746 78747 78748 78749 78750 78751 78752 78753 78754 78756 78757 "
                         "78758 78759", 60, "512 737"),
        ("ROUND ROCK", "TX", "78664 78665 78681", 10, "512 737"), ("CEDAR PARK", "TX", "78613", 6, "512"),
        ("LEANDER", "TX", "78641", 5, "512 737"), ("GEORGETOWN", "TX", "78626 78628 78633", 6, "512"),
        ("PFLUGERVILLE", "TX", "78660", 6, "512 737"), ("HUTTO", "TX", "78634", 2, "512"), ("MANOR", "TX", "78653", 1, "512"),
        ("KYLE", "TX", "78640", 4, "512 737"), ("BUDA", "TX", "78610", 2, "512"), ("SAN MARCOS", "TX", "78666", 5, "512"),
        ("LOCKHART", "TX", "78644", 1, "512"), ("BASTROP", "TX", "78602", 2, "512"),
        ("DRIPPING SPRINGS", "TX", "78620", 1, "512"), ("TAYLOR", "TX", "76574", 1, "512"),
        ("NEW BRAUNFELS", "TX", "78130 78132", 5, "830"), ("SEGUIN", "TX", "78155", 2, "830"),
        ("SAN ANTONIO", "TX", "78201 78207 78209 78212 78213 78216 78217 78223 78228 78229 78230 78232 78240 78245 78249 "
                              "78250 78251 78253 78258", 30, "210 726"),
        ("KILLEEN", "TX", "76541 76542 76543 76549", 6, "254"), ("TEMPLE", "TX", "76502 76504", 4, "254"),
        ("BELTON", "TX", "76513", 2, "254"), ("WACO", "TX", "76701 76705 76706 76708 76710 76712", 6, "254"),
        ("BRYAN", "TX", "77801 77802 77803", 3, "979"), ("COLLEGE STATION", "TX", "77840 77845", 3, "979"),
        ("HOUSTON", "TX", "77002 77004 77006 77007 77008 77024 77025 77036 77042 77055 77057 77063 77074 77080 77084 "
                          "77096", 25, "713 281 832 346"),
        ("DALLAS", "TX", "75201 75204 75206 75214 75218 75228 75243", 15, "214 469 972"),
        ("FORT WORTH", "TX", "76102 76104 76109 76116 76132", 8, "817 682"),
        ("CORPUS CHRISTI", "TX", "78401 78404 78411 78412 78414", 4, "361"), ("MCALLEN", "TX", "78501 78503 78504", 3, "956"),
        ("LAREDO", "TX", "78040 78041 78045", 2, "956"), ("EL PASO", "TX", "79902 79912 79924 79936", 3, "915"),
        ("LUBBOCK", "TX", "79401 79413 79424", 2, "806"), ("TYLER", "TX", "75701 75703", 2, "903 430"),
        ("VICTORIA", "TX", "77901 77904", 1, "361"),
    ],
}


@dataclass(frozen=True)
class Place:
    city: str
    state: str
    zip5: str
    area_codes: tuple


def _place_table(state: str) -> tuple[list, list]:
    out, cum, total = [], [], 0.0
    for city, st, zips, weight, codes in PLACES[state]:
        z = zips.split()
        for zp in z:
            out.append(Place(city, st, zp, tuple(codes.split())))
            total += weight / len(z)
            cum.append(total)
    return out, cum


_PLACE_TABLES = {s: _place_table(s) for s in PLACES}


def place(r, state: str) -> Place:
    places, cum = _PLACE_TABLES[state]
    return places[bisect.bisect_left(cum, r.random() * cum[-1])]


def phone(r, area_code: str) -> str:
    """A 10-digit NANP number: exchange 2-9XX, never N11 or 555."""
    while True:
        exchange = r.randint(200, 999)
        if exchange % 100 != 11 and exchange != 555:
            return f"{area_code}{exchange}{r.randint(0, 9999):04d}"


# --- streets ----------------------------------------------------------------------------------------
_STREETS = """MAIN|OAK|PINE|MAPLE|CEDAR|ELM|PARK|WASHINGTON|LAKE|HILL|WALNUT|SPRING|CHURCH|RIDGE|MEADOW|FOREST|WILLOW|
HICKORY|DOGWOOD|MAGNOLIA|CHESTNUT|SYCAMORE|BIRCH|POPLAR|LAUREL|HOLLY|CHERRY|MILL|RIVER|CREEK|VALLEY|SUNSET|HIGHLAND|
LINCOLN|JEFFERSON|MADISON|JACKSON|FRANKLIN|MONROE|ADAMS|KING|MARKET|WATER|BRIDGE|SCHOOL|GROVE|ORCHARD|FAIRVIEW|PLEASANT|
CENTER|UNION|BROAD|HIGH|COLLEGE|ACADEMY|PROSPECT|SUMMIT|WOODLAND|FARM|BROOK|STONE|SHADY|GREEN|WHITE OAK|RED OAK|LIVE OAK|
PECAN|MOCKINGBIRD|CARDINAL|BLUEBIRD|QUAIL|DEER|FOX|HAWK|EAGLE|TIMBER|PINEHURST|KENSINGTON|WINDSOR|BRISTOL|CAMBRIDGE|
OXFORD|HAMPTON|CHAPEL|DUKE|UNIVERSITY|GLENWOOD|HOLLOW|LANDING|HARBOR|COUNTRY CLUB|MEADOWBROOK|BRIARWOOD|ROLLING HILLS|
CREEKSIDE|STONEWALL|HERITAGE|PLANTATION|OLD MILL|TWIN OAKS|FOXCROFT|WILDWOOD|HUNTINGTON|WESTOVER|EASTWOOD|NORTHWOOD|
SOUTHERN PINES|FIELDSTONE|AUTUMN|SPRINGWOOD|SILVER LAKE|CROSS CREEK|INDIAN TRAIL|VILLAGE|PRESTON|WOODRIDGE|HAWTHORNE|
COVENTRY|ASHLEY|BROOKSIDE|MANOR|HARVEST|LAKEVIEW|RIVERSIDE|BAYBERRY"""
STREET_NAMES = WeightedList.zipf(_STREETS.replace("\n", "").split("|"), .6)
ROUTES = {"NC": ("NC HIGHWAY {n}", "US HIGHWAY {n}", "OLD {name} RD"), "TX": ("FM {n}", "COUNTY ROAD {n}", "RANCH ROAD {n}")}
# Which suffixes a street stem takes. A downtown name is a street, a country name a road, a subdivision name a drive
# or court; trees, birds and people vary. Each stem gets its own stable mix, so MAIN is nearly always MAIN ST.
_SUFFIX_MIX = {
    "downtown": (("ST", 75), ("AVE", 18), ("BLVD", 4), ("PL", 3)),
    "country": (("RD", 70), ("DR", 12), ("LN", 10), ("TRL", 5), ("WAY", 3)),
    "subdivision": (("DR", 40), ("CT", 20), ("LN", 14), ("WAY", 10), ("CIR", 10), ("PL", 6)),
    "person": (("ST", 40), ("AVE", 22), ("RD", 22), ("DR", 12), ("LN", 4)),
    "nature": (("ST", 25), ("DR", 25), ("LN", 18), ("AVE", 12), ("CT", 10), ("RD", 6), ("CIR", 4)),
}
_STEM_KIND = {}
for _kind, _stems in (("downtown", "MAIN MARKET CHURCH WATER HIGH BROAD KING BRIDGE SCHOOL UNION CENTER COLLEGE ACADEMY "
                                    "PROSPECT PLEASANT SUMMIT GROVE PARK"),
                      ("country", "MILL RIVER LAKE SPRING CREEK VALLEY FARM RIDGE COUNTRY_CLUB OLD_MILL PLANTATION HOLLOW "
                                  "STONEWALL FOREST MEADOW ORCHARD TIMBER HARVEST INDIAN_TRAIL CROSS_CREEK"),
                      ("subdivision", "CREEKSIDE BRIARWOOD MEADOWBROOK FOXCROFT WILDWOOD ROLLING_HILLS TWIN_OAKS "
                                      "SOUTHERN_PINES FIELDSTONE SPRINGWOOD SILVER_LAKE WOODRIDGE HAWTHORNE COVENTRY "
                                      "BROOKSIDE LAKEVIEW BAYBERRY KENSINGTON WINDSOR BRISTOL CAMBRIDGE PINEHURST "
                                      "HUNTINGTON WESTOVER EASTWOOD NORTHWOOD GLENWOOD HERITAGE LANDING HARBOR VILLAGE "
                                      "MANOR ASHLEY PRESTON OXFORD HAMPTON AUTUMN WOODLAND FAIRVIEW SUNSET HIGHLAND"),
                      ("person", "WASHINGTON LINCOLN JEFFERSON MADISON JACKSON FRANKLIN MONROE ADAMS DUKE UNIVERSITY "
                                 "CHAPEL")):
    for _s in _stems.split():
        _STEM_KIND[_s.replace("_", " ")] = _kind


def _stable(*parts) -> int:
    import hashlib
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


_SUFFIX_CACHE: dict = {}


def _suffixes(stem: str, kind: str) -> WeightedList:
    """The stem's own suffix mix: its kind's mix, with one suffix (stable per stem) taking most of the weight."""
    key = (stem, kind)
    if key not in _SUFFIX_CACHE:
        mix = _SUFFIX_MIX[kind]
        favored = _stable("suffix", stem) % len(mix) if kind in ("nature", "person") else 0
        _SUFFIX_CACHE[key] = WeightedList(tuple(x for x, _ in mix),
                                           tuple(accumulate(w * (6 if i == favored else 1) if kind in ("nature", "person")
                                                            else w for i, (_, w) in enumerate(mix))))
    return _SUFFIX_CACHE[key]


_STREET_SURNAME_CUM = list(accumulate(c for _, c, _ in _SURNAME_ROWS[:3000]))
_NC_ROAD_NAMES = ("MILL", "CHAPEL", "FARM", "STORE", "BRIDGE", "DAIRY", "SCHOOL", "CHURCH")


def street_address(r, state: str) -> tuple[str, str | None]:
    """(N301, N302): house number and street, plus an apartment / unit / lot on some."""
    u = r.random()
    if u < .06:
        line1 = f"PO BOX {r.randint(1, 4999)}"
    elif u < .13:
        pattern = r.choice(ROUTES.get(state, ROUTES["NC"]))
        line1 = f"{r.randint(100, 9999)} " + pattern.format(n=r.choice([10, 22, 49, 54, 61, 62, 70, 87, 150, 158, 421,
                                                                          620, 685, 973, 1431, 1626, 2222]),
                                                             name=STREET_NAMES.pick(r))
    else:
        number = r.choice([r.randint(1, 99), r.randint(100, 999), r.randint(100, 999), r.randint(1000, 9999),
                           r.randint(1000, 9999), r.randint(10000, 19999)])
        v = r.random()
        if v < .35:                                       # named after a family: HARRIS RD, PATTERSON MILL RD
            stem = SURNAMES[bisect.bisect_left(_STREET_SURNAME_CUM, r.random() * _STREET_SURNAME_CUM[-1])]
            if r.random() < .25:
                stem, kind = f"{stem} {r.choice(_NC_ROAD_NAMES)}", "country"
            else:
                kind = "person"
        elif v < .40:
            stem, kind = r.choice(["1ST", "2ND", "3RD", "4TH", "5TH", "6TH", "7TH", "8TH", "9TH", "10TH", "12TH",
                                   "14TH"]), "downtown"
        else:
            stem = STREET_NAMES.pick(r)
            kind = _STEM_KIND.get(stem, "nature")
        prefix = f"{r.choice('NSEW')} " if r.random() < .12 else ""
        line1 = f"{number} {prefix}{stem} {_suffixes(stem, kind).pick(r)}"
    line2 = None
    if not line1.startswith("PO BOX") and r.random() < .14:
        line2 = r.choice([f"APT {r.randint(1, 40)}{r.choice('ABCD')}", f"APT {r.randint(101, 1420)}",
                          f"UNIT {r.randint(1, 300)}", f"LOT {r.randint(1, 90)}", f"APT {r.randint(1, 24)}"])
    return line1, line2


# --- diagnoses ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Dx:
    code: str
    sex: str            # "" | "F" | "M"
    min_age: int
    max_age: int

    def fits(self, sex: str, age: float) -> bool:
        return (not self.sex or self.sex == sex) and self.min_age <= age <= self.max_age


def _load_dx() -> dict[str, list]:
    domains: dict[str, list] = {}
    with open(Path(__file__).with_name("data") / "icd10cm_lab_dx_2026.csv") as fh:
        for row in csv.DictReader(line for line in fh if not line.startswith("#")):
            domains.setdefault(row["domain"], []).append(
                (int(row["rank"]), Dx(row["code"], row["sex"], int(row["min_age"]), int(row["max_age"]))))
    return {d: [dx for _, dx in sorted(v, key=lambda t: t[0])] for d, v in domains.items()}


DX_DOMAINS = _load_dx()
def _dx_by_code() -> dict:
    """A code's widest limits across the domains it is listed in."""
    out = {}
    for v in DX_DOMAINS.values():
        for dx in v:
            old = out.get(dx.code)
            out[dx.code] = dx if old is None else Dx(dx.code, old.sex if old.sex == dx.sex else "",
                                                     min(old.min_age, dx.min_age), max(old.max_age, dx.max_age))
    return out


DX_BY_CODE = _dx_by_code()
DX_ZIPF = 2.0          # within a domain: the head codes carry most claims, a long tail carries the rest


@dataclass
class DomainSampler:
    """Draw a domain's codes by rank (Zipf), restricted to codes that fit the patient's sex and age."""
    codes: list
    cache: dict

    def _band(self, sex: str, age: float) -> tuple:
        band = (sex, min(int(age), 110))
        if band not in self.cache:
            ok = [dx for dx in self.codes if dx.fits(sex, band[1])]
            self.cache[band] = (ok, list(accumulate(1 / (i + 1) ** DX_ZIPF for i in range(len(ok)))))
        return self.cache[band]

    def has_codes(self, sex: str, age: float) -> bool:
        return bool(self._band(sex, age)[0])

    def pick(self, r, sex: str, age: float) -> str | None:
        ok, cum = self._band(sex, age)
        if not ok:
            return None
        return ok[bisect.bisect_left(cum, r.random() * cum[-1])].code


SAMPLERS = {d: DomainSampler(v, {}) for d, v in DX_DOMAINS.items()}

# Which clinical domains justify each test (relative weights). A test's line pointer names a diagnosis from one
# of these domains. Codes missing here fall back to their family (DEFAULT_DOMAINS_BY_FAMILY).
CPT_DOMAINS: dict[str, dict] = {}


def _domains(codes: str, **w):
    for c in codes.split():
        CPT_DOMAINS[c] = w


_domains("80053", htn=3, dm=2, ckd=2, liver=1.5, meds=1.5, general=1.5, electrolytes=1, gi=.5)
_domains("80048 80047 80051", htn=2, ckd=2, electrolytes=2, dm=1.5, meds=1.5, general=1)
_domains("85025 85027", anemia=3, general=2, infect=1, heme=1.5, ckd=1, meds=1)
_domains("80061 82465 83718 84478 83721", lipid=6, dm=1.5, htn=1.5, cardio=1)
_domains("83036 82947 83525 82951 82950", dm=6, nutrition=.6)
_domains("84443 84439 84436 84480 84481 86376", thyroid=6, general=1.2, nutrition=.4)
_domains("82306", nutrition=4, bone=1.5, ckd=1, general=.5)
_domains("82607 82746", anemia=2, nutrition=2, neuro=1, general=1)
_domains("82728 83540 83550 84466 85044 85014 85018", anemia=4, general=.8, ckd=.8)
_domains("84153 84154 84152 84270", male=1)
_domains("85610 85730 85384", anticoag=6, liver=1, preop=.5)
_domains("81001 81003 81015 87086 87088 87077 87186", uti=5, renal_stone=.4, proteinuria=.4, dm=.4)
_domains("82043 82042 84156 82575", dm=3, ckd=3, htn=1, proteinuria=1)
_domains("82570", dm=2, ckd=2, htn=1, proteinuria=1, drug=2)
_domains("87491 87591 87389 87536", sti=5)
_domains("86592 86780", sti=5, infect=.5, neuro=.3)
_domains("87661 87510 87480", vaginitis=4, sti=1)
_domains("86803 87522 86704 86706 86708 80074 87340", hepatitis=4)
_domains("87624 87623 87625", gyn=1)
_domains("87637 87631 87635 87880", resp=1)
_domains("87798 87481 87150", vaginitis=3, uti=2)
_domains("80307 G0480 G0481 G0482 G0483", drug=1)
_domains("81225 81226", pgx=1)
_domains("81162", onc_hx=4, cancer=1)
_domains("81479", onc_hx=2, cancer=2)
_domains("81220", carrier=1)
_domains("81240 81241 81291", thrombophilia=1)
_domains("81270", myeloproliferative=1)
_domains("88305", path_skin=5, path_gi=3, path_gyn=1)
_domains("80055 80081 86762 84702 84703 81025 84144", prenatal=1)
_domains("86900 86901 86850", preop=3, prenatal=2, anemia=1)
_domains("82274 G0328", gi=1, anemia=.5)
_domains("82310 82330 83970 84100", bone=2, ckd=2, electrolytes=1)
_domains("84295 84132 82565 84520 83735 80069", ckd=3, electrolytes=2, htn=1, meds=1)
_domains("80076 84460 84450 82977 84075 82247 82248 82040", liver=4, meds=1)
_domains("86140 85652", rheum=1.5, infect=1, general=2, cardio=.5)
_domains("86038 86431 86235 86200 82085", rheum=5)
_domains("86141 83880 84484", cardio=5)
_domains("85379", thrombophilia=3, cardio=1)
_domains("85049", heme=4)
_domains("86677 83690 82150 87338", gi=4)
_domains("86787", infect=2)
_domains("82785", allergy=5)
_domains("82533 82088", endocrine=4)
_domains("82627", endocrine=2, gyn=2)
_domains("83001 83002 82670", endocrine=1, infertility=1)
_domains("84403 84402", male=2, gyn=1, endocrine=1)
_domains("87070 87081", infect=4)
_domains("86618", general=3, rheum=1, infect=.3)            # Lyme serology: fatigue, joint pain; Lyme itself is rare in NC
_domains("84550", gout=3, ckd=2)
_domains("87045 87177 87493", gi_infect=4)
_domains("80178", psych=5)
_domains("80164 80156", neuro=4, psych=1)
_domains("80162", cardio=4)
_domains("80197", transplant=5)
_domains("80202", infect=5)
_domains("83655", peds=1)
_domains("G0103", male=1)
DEFAULT_DOMAINS_BY_FAMILY = {"80": dict(meds=3, psych=1, neuro=1), "82": dict(general=2, electrolytes=1, nutrition=1),
                             "83": dict(general=2, nutrition=1, meds=1), "84": dict(general=2, ckd=1, electrolytes=1),
                             "85": dict(anemia=2, heme=2, anticoag=1), "86": dict(infect=3, general=1),
                             "87": dict(infect=3, uti=1, gi_infect=1, resp=1), "81": dict(uti=2, proteinuria=1)}
# Screening codes a test is covered by (first-listed on screening orders). General exams cover any routine test.
SCREENING_BY_CPT = {"80061": ("Z13220", "Z136"), "82465": ("Z13220", "Z136"), "83718": ("Z13220", "Z136"),
                    "84478": ("Z13220", "Z136"), "83036": ("Z131",), "82947": ("Z131",), "84443": ("Z1329",),
                    "84439": ("Z1329",), "85025": ("Z130",), "85027": ("Z130",), "84153": ("Z125",), "G0103": ("Z125",),
                    "82274": ("Z1211",), "G0328": ("Z1211",), "87491": ("Z113",), "87591": ("Z113",),
                    "87661": ("Z113",), "86592": ("Z113",), "87389": ("Z114",), "86803": ("Z1159",),
                    "87624": ("Z1151", "Z124"), "82306": ("Z1321",), "80053": ("Z13228",)}
GENERAL_EXAM_CODES = ("Z0000", "Z0001", "Z00129", "Z00121", "Z01419")
# Tests a general or preventive exam orders without a symptom; anything else on a screening requisition is ordered
# for a condition and points at that diagnosis.
SCREENING_ROUTINE_TESTS = frozenset("85025 85027 80053 80048 80061 83036 82947 84443 82306 81001 81003 84153 G0103 "
                                    "82274 G0328 87491 87591 87661 86592 87389 86803 87624 82570 82043".split())
SCREENING_CODES = frozenset(c for v in SCREENING_BY_CPT.values() for c in v) | set(GENERAL_EXAM_CODES) | {"Z1389", "Z1212"}

# Tests only one sex can have (as ordered for a patient of that sex), and age limits.
CPT_SEX = {"84153": "M", "84154": "M", "84152": "M", "G0103": "M", "87624": "F", "87623": "F", "87625": "F",
           "84702": "F", "84703": "F", "81025": "F", "84144": "F", "80055": "F", "80081": "F", "86762": "F"}
CPT_AGE = {"84153": (40, 120), "84154": (40, 120), "84152": (40, 120), "G0103": (50, 120), "87624": (21, 70),
           "87623": (21, 70), "87625": (21, 70), "84702": (12, 55), "84703": (12, 55), "81025": (12, 55),
           "84144": (12, 55), "80055": (14, 48), "80081": (14, 48), "82274": (45, 85), "G0328": (45, 85),
           "83655": (0, 17), "84403": (12, 120), "84402": (12, 120), "83001": (12, 120), "83002": (12, 120),
           "82670": (12, 120), "80061": (9, 120), "82465": (9, 120), "83718": (9, 120), "84478": (9, 120),
           "81220": (15, 50), "83880": (30, 120), "86141": (30, 120), "84484": (30, 120), "83036": (8, 120), "84443": (1, 120), "82306": (1, 120),
           "80307": (12, 120), "G0480": (12, 120), "G0481": (12, 120), "G0482": (12, 120), "G0483": (12, 120)}


def cpt_fits(code: str, sex: str, age: float) -> bool:
    """The test can be ordered for this patient: right sex and age, and some diagnosis that justifies it applies
    to them (no digoxin level on a child, no urine pregnancy test at 60)."""
    key = (code, sex, min(int(age), 110))
    if key not in _FITS_CACHE:                            # decided on whole years, so the cache cannot drift
        lo, hi = CPT_AGE.get(code, (0, 200))
        _FITS_CACHE[key] = (CPT_SEX.get(code, sex) == sex and lo <= key[2] <= hi and
                            any(SAMPLERS[d].has_codes(sex, key[2]) for d in cpt_domains(code) if d in SAMPLERS))
    return _FITS_CACHE[key]


_FITS_CACHE: dict = {}


# Broad panels ordered as part of a general work-up (fatigue, weight change, medication monitoring): those
# diagnoses justify them too. Targeted tests (PSA, uric acid, a drug level) keep only their own domains.
GENERAL_WORKUP_TESTS = frozenset("80053 80048 80047 80051 80069 80076 85025 85027 82947 84132 84295 82565 84520 82310 "
                                 "84460 84450 83735 84100 82040 85014 85018 85048 81001 81003".split())


def cpt_domains(code: str) -> dict:
    """The clinical domains that justify a test."""
    if code not in _DOMAIN_CACHE:
        domains = dict(CPT_DOMAINS.get(code) or DEFAULT_DOMAINS_BY_FAMILY.get(code[:2], dict(general=1)))
        if code in GENERAL_WORKUP_TESTS:
            domains.setdefault("general", .6)
            domains.setdefault("meds", .4)
        _DOMAIN_CACHE[code] = domains
    return _DOMAIN_CACHE[code]


_DOMAIN_CACHE: dict = {}


# Chronic conditions: prevalence among tested patients by age band (<18, 18-44, 45-64, 65+), as the domain whose
# code the patient carries on every claim. Low confidence (shaped after NHANES / CDC chronic disease prevalence).
CHRONIC = {"htn": (.01, .12, .38, .62), "lipid": (.01, .10, .35, .52), "dm": (.01, .05, .16, .26),
           "thyroid": (.01, .06, .10, .16), "ckd": (0, .01, .06, .22), "anemia": (.04, .06, .06, .12),
           "anticoag": (0, .003, .02, .10), "nutrition": (.08, .18, .22, .18), "psych": (.03, .12, .12, .08),
           "cardio": (0, .005, .05, .18), "rheum": (0, .01, .03, .06), "liver": (0, .02, .04, .03),
           "meds": (0, .08, .16, .28), "gout": (0, .005, .03, .06)}
CHRONIC_FEMALE_MULT = {"thyroid": 2.2, "anemia": 1.6, "rheum": 1.4}


# --- NCCI: lab panels and their components ------------------------------------------------------------
# Medicare NCCI procedure-to-procedure edits a lab claim scrubber enforces (CPT panel definitions; urinalysis codes
# are mutually exclusive). Billing a component with its panel is a coding error the scrubber removes.
PANEL_COMPONENTS = {
    "80053": "82040 82247 82310 82374 82435 82565 82947 84075 84132 84155 84295 84450 84460 84520 80048 80047 80051 80069 80076",
    "80048": "82310 82374 82435 82565 82947 84132 84295 84520 80051 80047",
    "80047": "82330 82374 82435 82565 82947 84132 84295 84520 80051",
    "80069": "82040 82310 82374 82435 82565 82947 84100 84132 84295 84520 80051 80048 80047",
    "80076": "82040 82247 82248 84075 84155 84450 84460",
    "80061": "82465 83718 84478",
    "80051": "82374 82435 84132 84295",
    "85025": "85004 85007 85008 85009 85013 85014 85018 85027 85032 85041 85048 85049",
    "85027": "85013 85014 85018 85032 85041 85048 85049",
    "80081": "80055 85025 85027 87340 86762 86592 86900 86901 86850 87389",
    "80055": "85025 85027 87340 86762 86592 86900 86901 86850",
    "80074": "86709 87340 86705 86803",
    "81001": "81000 81002 81003 81005 81015",
    "81000": "81002 81003 81005 81015",
    "81003": "81002 81005",
}
NCCI_COMPONENT_OF = {}
for _panel, _comps in PANEL_COMPONENTS.items():
    for _c in _comps.split():
        NCCI_COMPONENT_OF.setdefault(_c, []).append(_panel)


def ncci_conflicts(codes) -> list[tuple[str, str]]:
    """(component, panel) pairs on one claim that NCCI does not pay together."""
    present = set(codes)
    return [(c, p) for c in present for p in NCCI_COMPONENT_OF.get(c, ()) if p in present]


DOMAIN_CODES = {d: frozenset(dx.code for dx in v) for d, v in DX_DOMAINS.items()}


def dx_fits_cpt(code: str, cpt: str) -> bool:
    """The diagnosis justifies the test: it is in one of the test's clinical domains, or it is a screening code
    that covers the test (a general exam covers any routine test)."""
    key = (code, cpt)
    fits = _DX_FITS_CACHE.get(key)
    if fits is None:
        fits = _DX_FITS_CACHE[key] = (code in GENERAL_EXAM_CODES or code in SCREENING_BY_CPT.get(cpt, ()) or
                                      any(code in DOMAIN_CODES.get(d, ()) for d in cpt_domains(cpt)))
    return fits


_DX_FITS_CACHE: dict = {}


def pick_dx_for_cpt(r, cpt: str, sex: str, age: float) -> str:
    """A diagnosis for this test from its domains (weighted), fitting the patient's sex and age."""
    domains = cpt_domains(cpt)
    names = list(domains)
    for _ in range(4):
        d = r.choices(names, weights=[domains[n] for n in names])[0]
        code = SAMPLERS[d].pick(r, sex, age) if d in SAMPLERS else None
        if code:
            return code
    for d in names:                                       # deterministic fallback: any domain with a fitting code
        code = SAMPLERS[d].pick(r, sex, age) if d in SAMPLERS else None
        if code:
            return code
    return SAMPLERS["general"].pick(r, sex, age)
