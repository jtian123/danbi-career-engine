"""Deterministic reading of a posting: is it an internship, in the US, in scope;
which direction (lane) and industry; which season; what eligibility lines it states.

Everything here is a first pass for ranking the review queue. Claude's review of
the full posting decides; these flags only tell the reviewer where to look.
Rules come from registry/lanes.json and registry/industries.json so they can be
tuned without touching code.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date
from functools import lru_cache

from ..paths import registry

# ------------------------------------------------------------------ text
def text(value) -> str:
    """Strip HTML (Greenhouse sometimes double-encodes it) and squeeze spaces."""
    s = html.unescape(html.unescape(str(value or '')))
    s = re.sub(r'<(br|/p|/li|/h\d)[^>]*>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'[ \t\r\f\v]+', ' ', s)
    return re.sub(r'\n\s*\n+', '\n', s).strip()


def norm_title(t: str) -> str:
    t = re.sub(r'\([^)]*\)', ' ', (t or '').lower())
    t = re.sub(r'\b(summer|spring|fall|winter|autumn)\b|\b20\d\d\b|\b(start|starts|bs/ms|ms|mba)\b', ' ', t)
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()


def canon_company(c: str) -> str:
    c = html.unescape(str(c or '')).lower().replace('&', ' and ')
    c = re.sub(r'\b(inc|llc|ltd|corp|corporation|co|company|the|group|holdings|plc|usa|us|america|north america)\b\.?', ' ', c)
    return re.sub(r'[^a-z0-9]+', '', c)


# ------------------------------------------------------------------ registries
@lru_cache(maxsize=None)
def _lanes():
    return json.loads(registry('lanes.json').read_text())


@lru_cache(maxsize=None)
def _industries():
    return json.loads(registry('industries.json').read_text())


@lru_cache(maxsize=None)
def _enterprises():
    data = json.loads(registry('enterprises.json').read_text())
    index = {}
    for e in data['employers']:
        for name in [e['name']] + e.get('aliases', []):
            index[canon_company(name)] = e
    for name in data.get('large_names', []):
        index.setdefault(canon_company(name), {'name': name, 'tier': 'large', 'industry': None})
    for name in data.get('usc_recruiters', []):
        k = canon_company(name)
        index[k] = dict(index.get(k) or {'name': name, 'tier': '', 'industry': None}, usc=True)
    return index


@lru_cache(maxsize=None)
def _staffing():
    return {canon_company(x) for x in json.loads(registry('exclusions.json').read_text())['staffing']}


def employer(company: str) -> dict:
    """Registry facts about an employer, or {} when we know nothing."""
    return _enterprises().get(canon_company(company)) or {}


def is_staffing(company: str) -> bool:
    return canon_company(company) in _staffing()


# ------------------------------------------------------------------ gates
INTERN = re.compile(r'\b(intern|interns|internship|internships|co-?op|summer (associate|analyst)|'
                    r'student (worker|associate|analyst|assistant|trainee)|apprentice(ship)?|'
                    r'werkstudent|working student)\b', re.I)
NOT_INTERN = re.compile(r'\b(internal|international|new grad(uate)?|fellowship|graduate program|'
                        r'rotational program|full[- ]time)\b', re.I)
OUT_OF_SCOPE = [  # (pattern, reason) — titles only; counted, never silent
    (r'\bsoftware\b|\bswe\b|\bsde\b|developer|full[- ]?stack|front[- ]?end|back[- ]?end|\bios\b|android|'
     r'devops|site reliability|\bsre\b|firmware|embedded|kernel|compiler', 'software engineering'),
    (r'machine learning engineer|\bml engineer|\bai engineer|\bmle\b|ml infra|ml systems|deep learning|'
     r'computer vision|\bnlp\b|\bllm\b|robotics|autonomy|perception', 'ML / AI engineering'),
    (r'research scientist|applied scien|\bphd\b|ph\.d|postdoc|student researcher|research intern',
     'PhD research'),
    (r'hardware|silicon|asic|fpga|rf |analog|circuit|semiconductor process|optical|photonics|'
     r'(mechanical|electrical|civil|chemical|structural|aerospace|propulsion|materials|test|validation|'
     r'manufacturing|design|controls|systems|reliability|packaging|thermal|process|environmental|nuclear|'
     r'petroleum|biomedical|network|security|cloud|data center|facilities|field|power)\s+engineer',
     'engineering discipline'),
    (r'\bengineer(s|ing)?\b|technologist|technical staff|\bmts\b|\b(structural|mechanical|electrical|thermal|'
     r'hydraulic|aerodynamic|propulsion|fluid dynamics|stress analysis|avionics|manufacturing technician)\b',
     'engineering discipline'),
    (r'(?<!data )(?<!decision )(?<!data-)scientist|\b(discovery|translational|bioanalytical|cmc|toxicolog\w*|'
     r'pharmacolog\w*|chemist\w*|biolog\w*|genomic\w*|assay|preclinical|in vivo|cell therapy|process development|'
     r'clinical (research|lab|technolog|experience|specialist|trial))\b', 'lab / life science'),
    (r'cyber|security analyst|penetration|soc analyst|it support|help ?desk|desktop support|network admin',
     'IT / security'),
    (r'nurs|pharmac|physician|medical assistant|clinical (lab|research coordinator)|dental|therap(y|ist)|'
     r'veterinar|lab (tech|assistant)|laboratory|(lab|bench|research|clinical|food|formulation|r&d) scientist',
     'clinical / lab science'),
    (r'attorney|law clerk|legal intern|\bjd\b|paralegal', 'law'),
    (r'quant(itative)? (trad|research|develop)|\btrader\b|actuar', 'quant / actuarial'),
    (r'teacher|tutor|driver|warehouse|cashier|technician|mechanic|electrician|welder|lifeguard|'
     r'custodian|groundskeeper|line cook|barista|store associate|sales associate', 'not a professional internship'),
]
_OUT = [(re.compile(p, re.I), why) for p, why in OUT_OF_SCOPE]
KEEP_ENGINEERING = re.compile(r'industrial (and systems |& systems )?engineer|systems engineering & operations|'
                              r'process improvement|operations research|data engineer|analytics engineer|'
                              r'sales engineer|engineering (program|project) manag', re.I)
NON_ENGLISH = re.compile(r'^\s*stage\b|stagiaire|praktik|pasant|becari|tirocin|est[áa]gio|alternance|\bhiver\b|'
                         r'\b[ée]t[ée] 20\d\d|pr[áa]ctica|werkstudent|\bL\. 68/99', re.I)


def out_of_scope(title: str):
    if NON_ENGLISH.search(title or ''):
        return 'non-US posting (title language)'
    if KEEP_ENGINEERING.search(title or ''):
        return None
    for rx, why in _OUT:
        if rx.search(title or ''):
            return why
    return None


US_STATES = ('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND '
             'OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC').split()
US_NAMES = ('alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|'
            'idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|'
            'minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|'
            'north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|'
            'south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|'
            'united states|u\\.s\\.a?\\.?|\\bnyc\\b|\\bsf\\b|san francisco|bay area|los angeles|seattle|'
            'boston|chicago|atlanta|austin|denver|dallas|houston|miami|san jose|san diego|irvine|'
            'santa monica|culver city|burbank|pasadena|mountain view|sunnyvale|menlo park|palo alto|'
            'redmond|bellevue|new york city|brooklyn|philadelphia|pittsburgh|minneapolis|detroit|'
            'nashville|charlotte|raleigh|phoenix|portland|salt lake|columbus|cincinnati|cleveland|st\\. louis|'
            'kansas city|indianapolis|milwaukee|baltimore|richmond|tampa|orlando|las vegas|sacramento|'
            'oakland|cupertino|santa clara|foster city|torrance|el segundo|long beach|anaheim|newport beach')
FOREIGN = (r"\b(?:afghanistan|albania|algeria|andorra|angola|argentina|armenia|australia|austria|azerbaijan|bahrain|bangladesh|belarus|belgium|bolivia|bosnia|botswana|brazil|bulgaria|cambodia|cameroon|canada|chile|china|colombia|costa rica|croatia|cyprus|czech|czechia|denmark|dominican republic|ecuador|egypt|el salvador|estonia|ethiopia|finland|france|georgia \(country\)|germany|ghana|greece|guatemala|honduras|hong kong|hungary|iceland|india|indonesia|iran|iraq|ireland|israel|italy|jamaica|japan|jordan|kazakhstan|kenya|korea|kuwait|latvia|lebanon|lithuania|luxembourg|macau|malaysia|malta|mauritius|mexico|moldova|monaco|mongolia|morocco|mozambique|myanmar|namibia|nepal|netherlands|new zealand|nicaragua|nigeria|north macedonia|norway|oman|pakistan|panama|paraguay|peru|philippines|poland|portugal|qatar|romania|russia|rwanda|saudi|scotland|senegal|serbia|singapore|slovakia|slovenia|south africa|spain|sri lanka|sweden|switzerland|taiwan|tanzania|thailand|tunisia|turkey|türkiye|uganda|ukraine|united arab emirates|uae|united kingdom|england|wales|uruguay|uzbekistan|venezuela|vietnam|zambia|zimbabwe|emea|apac|latam|toronto|vancouver|montreal|ontario|british columbia|quebec|alberta|calgary|ottawa|mississauga|waterloo|london|dublin|bangalore|bengaluru|hyderabad|pune|mumbai|delhi|gurgaon|gurugram|noida|chennai|kolkata|beijing|shanghai|shenzhen|guangzhou|hangzhou|taipei|tokyo|osaka|seoul|berlin|munich|hamburg|frankfurt|paris|lyon|madrid|barcelona|amsterdam|rotterdam|eindhoven|warsaw|krakow|wroclaw|sao paulo|são paulo|rio de janeiro|buenos aires|bogota|bogotá|medellin|sydney|melbourne|brisbane|tel aviv|haifa|manila|jakarta|kuala lumpur|bangkok|dubai|abu dhabi|riyadh|zurich|zürich|geneva|stockholm|copenhagen|oslo|helsinki|milan|milano|rome|lisbon|porto|bucharest|prague|budapest|vienna|brussels|antwerp|casablanca|cairo|lagos|nairobi|johannesburg|cape town|istanbul|mexico city|guadalajara|monterrey|santiago|lima|edinburgh|manchester|cambridge, uk|lombardia|lombardy|bavaria|flemish|wallonia|catalonia|ile-de-france|île-de-france)\b")
_ISO3 = re.compile(r"\b(?:DEU|CAN|GBR|IND|ITA|FRA|ESP|NLD|POL|BRA|MEX|CHN|JPN|KOR|SGP|AUS|IRL|ISR|CHE|SWE|BEL|AUT|DNK|NOR|FIN|PRT|CZE|HUN|ROU|PHL|IDN|MYS|THA|VNM|ARG|COL|CHL|PER|ZAF|EGY|NGA|KEN|TUR|ARE|SAU|HKG|TWN|NZL|CRI|MAR|UKR|GRC|BGR|SVK|LTU|LVA|EST|HRV|SRB|LUX)\b")   # case-sensitive country codes ("DEU", "CAN, ON")
_US = re.compile(r'(\b(' + '|'.join(US_STATES + ['US', 'USA']) + r')\b)|(?i:' + US_NAMES + ')')
_US_ABBR_ONLY = re.compile(r',\s*(' + '|'.join(US_STATES) + r')\b')
_FOREIGN = re.compile(FOREIGN, re.I)


def us_status(location: str) -> str:
    """'US', 'foreign', or 'unknown'. Any US place in a multi-location string counts."""
    loc = (location or '').strip()
    if not loc:
        return 'unknown'
    parts = re.split(r';|\||/| or |\n', loc)
    seen_foreign = False
    for p in parts:
        foreign = bool(_FOREIGN.search(p) or _ISO3.search(p))
        if (_US_ABBR_ONLY.search(p) and not _ISO3.search(p)) or (_US.search(p) and not foreign):
            return 'US'
        if foreign:
            seen_foreign = True
    if re.search(r'remote|anywhere|multiple|various|hybrid', loc, re.I) and not seen_foreign:
        return 'unknown'
    return 'foreign' if seen_foreign else 'unknown'


# ------------------------------------------------------------------ direction + industry
@lru_cache(maxsize=None)
def _lane_rules():
    rules = []
    for rule in _lanes()['title_rules']:
        rules.append((re.compile(rule['pattern'], re.I), rule['lane'], rule.get('stretch', False)))
    return rules


def lane_of(title: str):
    """(lane_id, stretch). Unmatched titles land in 'explore' — kept, not dropped."""
    for rx, lane, stretch in _lane_rules():
        if rx.search(title or ''):
            return lane, stretch
    return 'explore', False


@lru_cache(maxsize=None)
def _industry_rules():
    return [(re.compile(r['pattern'], re.I), r['id']) for r in _industries()['keyword_rules']]


def industry_of(company: str, description: str = '', title: str = '') -> str:
    e = employer(company)
    if e.get('industry'):
        return e['industry']
    head = (company or '') + ' ' + (title or '') + ' ' + (description or '')[:1500]
    for rx, ind in _industry_rules():
        if rx.search(company or ''):
            return ind
    for rx, ind in _industry_rules():
        if rx.search(head):
            return ind
    return 'other'


# ------------------------------------------------------------------ season + eligibility
SEASON = re.compile(r'\b(summer|spring|fall|autumn|winter)[\s,]*[\'’]?\s*(20)?(2[5-9])\b', re.I)
SEASON2 = re.compile(r'\b(20)(2[5-9])\s+(summer|spring|fall|autumn|winter)\b', re.I)


def seasons(*texts) -> list:
    out = []
    for t in texts:
        for m in SEASON.finditer(t or ''):
            s = m.group(1).lower().replace('autumn', 'fall')
            out.append(f'{s.title()} 20{m.group(3)}')
        for m in SEASON2.finditer(t or ''):
            s = m.group(3).lower().replace('autumn', 'fall')
            out.append(f'{s.title()} 20{m.group(2)}')
    return list(dict.fromkeys(out))


_SEASON_END = {'Spring': (5, 31), 'Summer': (8, 31), 'Fall': (12, 15), 'Winter': (3, 15)}


def season_past(season: str, today: date) -> bool:
    """True when the whole season is over (Summer 2026 on 2026-10-06)."""
    try:
        name, year = season.split()
        m, d = _SEASON_END[name]
        return date(int(year), m, d) < today
    except (KeyError, ValueError):
        return False


DEGREE_MS = re.compile(r"master'?s|\bm\.?s\.?\b(?! (word|office|excel))|graduate (student|program|degree|school)|"
                       r"\bmba or m\.?s|\bms/|/ms\b|bs/ms|ms or phd|advanced degree|post-?graduate", re.I)
DEGREE_UG_ONLY = re.compile(r'rising (junior|senior|sophomore)|undergraduate (student|degree program)s? only|'
                            r'currently (enrolled|pursuing) (in )?an? (undergraduate|bachelor)|'
                            r'pursuing a bachelor|working towards? a bachelor|sophomore|junior or senior|'
                            r'(class|graduating class) of 2028', re.I)
DEGREE_MBA = re.compile(r'\bmba\b', re.I)
DEGREE_PHD = re.compile(r'\bph\.?d\b', re.I)
CITIZEN = re.compile(r'(u\.?s\.?|united states) citizen(ship)? (is |are )?(required|only)|'
                     r'must be (a )?(u\.?s\.?|united states) citizen|citizenship (is )?required|'
                     r'only (u\.?s\.? )?citizens', re.I)
CLEARANCE = re.compile(r'(security|secret|ts/sci|top secret|public trust) clearance|'
                       r'(obtain|maintain|hold|eligib\w+ (for|to obtain)) (a |an )?(active )?(dod |government )?'
                       r'(security )?clearance', re.I)
GRAD_WINDOW = re.compile(r'graduat\w*\s+(?:date\s+)?(?:between|from)\s+([A-Za-z]+\.?\s+20\d\d)\s+(?:and|to|-|–)\s+'
                         r'([A-Za-z]+\.?\s+20\d\d)', re.I)
GPA = re.compile(r'(minimum|min\.?|at least|cumulative)\s+(of\s+)?(a\s+)?gpa\s+(of\s+)?(\d\.\d)|'
                 r'(\d\.\d)\s*(\+|or (higher|above|better))\s*(cumulative\s+)?gpa|gpa\s+of\s+(\d\.\d)\s+or', re.I)
PAY = re.compile(r'\$\s?\d[\d,]*(\.\d+)?\s*(k\b)?(\s*(-|–|to)\s*\$?\s?\d[\d,]*(\.\d+)?\s*(k\b)?)?'
                 r'(\s*(/|per|an|a)\s*(hour|hr|year|yr|month|week|annually))?', re.I)
_PAY_CONTEXT = re.compile(r'hour|hourly|\bhr\b|year|annual|salary|pay|compensation|stipend|month|week|wage', re.I)
_NOT_PAY = re.compile(r'billion|million|\bbn\b|\bmm\b|revenue|assets|sales of|valuation|funding|raised', re.I)


def pay_text(desc: str) -> str:
    """First dollar amount that reads like pay (unit or pay words nearby), else ''.
    Guards against '$65 billion in revenue' and '$200bn of assets'."""
    for m in PAY.finditer(desc or ''):
        amount = m.group(0).strip()
        if not re.search(r'\d{2}', amount):
            continue
        window = desc[max(0, m.start() - 70):m.end() + 40]
        after = desc[m.end():m.end() + 25]
        if _NOT_PAY.search(after) or _NOT_PAY.search(window) and not _PAY_CONTEXT.search(amount):
            continue
        if _PAY_CONTEXT.search(window):
            return amount
    return ''


KOREAN = re.compile(r'\bkorean\b|\bk-?beauty\b|\bk-?pop\b|\bhangul\b', re.I)
BEAUTY_COMMERCE = re.compile(r'beauty|cosmetic|skincare|skin care|fragrance|tiktok shop|influencer|'
                             r'creator (economy|partnership|marketing|growth)|live ?stream|social commerce|'
                             r'e-?commerce', re.I)
_MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct',
                                       'nov', 'dec'], 1)}


def _month(s: str):
    m = re.match(r'([A-Za-z]+)\.?\s+(20\d\d)', s.strip())
    if not m or m.group(1)[:3].lower() not in _MONTHS:
        return None
    return f'{m.group(2)}-{_MONTHS[m.group(1)[:3].lower()]:02d}'


def eligibility(title: str, desc: str, degrees=None) -> dict:
    """Flags only. degree_rule mirrors engine.import_reviewed's vocabulary."""
    blob = f'{title}\n{desc or ""}'
    flags = {}
    deg = [d.lower() for d in (degrees or [])]
    if deg:
        if any("master" in d for d in deg):
            flags['degree_rule'] = 'graduate_allowed'
        elif deg == ['phd']:
            flags['degree_rule'] = 'phd_only'
        elif all(d in ('mba',) for d in deg):
            flags['degree_rule'] = 'mba_only'
        elif all("bachelor" in d or "associate" in d for d in deg):
            flags['degree_rule'] = 'unknown'
            flags['degree_note'] = "List tags it Bachelor's — confirm a master's student is eligible"
    if re.search(r'\bundergrad', title or '', re.I) and not DEGREE_MS.search(title or ''):
        flags['degree_rule'] = 'undergraduate_only'
    elif DEGREE_MS.search(blob):
        flags['degree_rule'] = 'graduate_allowed'
    elif DEGREE_UG_ONLY.search(blob):
        flags['degree_rule'] = 'undergraduate_only'
    elif DEGREE_MBA.search(title or '') and not DEGREE_MS.search(blob):
        flags['degree_rule'] = 'mba_only'
    elif re.search(r'\bphd\b|ph\.d', title or '', re.I):
        flags['degree_rule'] = 'phd_only'
    flags.setdefault('degree_rule', 'unknown')
    if CITIZEN.search(blob):
        flags['citizenship_only'] = True
    if CLEARANCE.search(blob):
        flags['clearance'] = True
    m = GRAD_WINDOW.search(blob)
    if m:
        lo, hi = _month(m.group(1)), _month(m.group(2))
        if lo and hi:
            flags['grad_min'], flags['grad_max'] = lo, hi
    g = GPA.search(blob)
    if g:
        val = next((x for x in g.groups() if x and re.fullmatch(r'\d\.\d', x)), None)
        if val:
            flags['min_gpa'] = float(val)
    pay = pay_text(desc or '')
    if pay:
        flags['pay_text'] = pay
    arenas = []
    if KOREAN.search(blob):
        arenas.append('korean')
    if BEAUTY_COMMERCE.search(blob):
        arenas.append('beauty_commerce')
    if arenas:
        flags['arenas'] = arenas
    return flags
