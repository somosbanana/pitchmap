#!/usr/bin/env python3
"""pitchmap: find local businesses on Google Maps, check their websites, write one relevant email each, queue it in n8n.

  python3 scripts/pitchmap.py find    batch1     # Google Places API -> data/batch1.places.jsonl
  python3 scripts/pitchmap.py profile batch1     # check websites, find emails -> data/batch1.leads.csv
  python3 scripts/pitchmap.py emails  batch1     # write the emails -> data/batch1.queue.json + data/batch1.sample.md
  python3 scripts/pitchmap.py load    batch1     # send the queue to your n8n intake webhook
  python3 scripts/pitchmap.py test               # self-check, no network

Needs only Python 3.9+. Settings in config.json (see config.example.json). Google key in env GOOGLE_PLACES_KEY.
"""
import concurrent.futures as cf, csv, hashlib, json, os, re, ssl, sys, urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = {"User-Agent": "Mozilla/5.0 (compatible; pitchmap/1.0; website check)"}
CTX = ssl.create_default_context()
EM = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EM = re.compile(r"\.(png|jpg|jpeg|gif|webp|svg)$|sentry|wixpress|example|domain\.com|godaddy|yourdomain|email\.com|@2x", re.I)
FREE_EM = re.compile(r"@(gmail|yahoo|hotmail|outlook|aol|icloud|live|msn|comcast|att|bigpond|xtra|btinternet|sky)\.", re.I)
FIELDS = "#;country;city;tz;name;category;rating;phone;web;email;signals;review_quote;status".split(";")

# Time zone per country / region. Add yours to config.json -> "timezones": {"XX": "Area/City"}.
TZ = {
    "US": "America/New_York", "CA": "America/Toronto", "AU": "Australia/Sydney", "NZ": "Pacific/Auckland", "UK": "Europe/London", "IE": "Europe/Dublin",
    "US-CT": "America/Chicago", "US-MT": "America/Denver", "US-PT": "America/Los_Angeles", "US-AZ": "America/Phoenix", "US-AK": "America/Anchorage", "US-HI": "Pacific/Honolulu",
    "BC": "America/Vancouver", "AB": "America/Edmonton", "SK": "America/Regina", "MB": "America/Winnipeg", "ON": "America/Toronto", "QC": "America/Toronto",
    "NB": "America/Halifax", "NS": "America/Halifax", "PE": "America/Halifax", "NL": "America/St_Johns",
    "QLD": "Australia/Brisbane", "NSW": "Australia/Sydney", "VIC": "Australia/Melbourne", "TAS": "Australia/Hobart", "SA": "Australia/Adelaide",
    "WA": "Australia/Perth", "NT": "Australia/Darwin", "ACT": "Australia/Sydney",
}
US_ZONE = {**dict.fromkeys("AL AR IA IL KS LA MN MO MS ND NE OK SD TX WI".split(), "US-CT"),
           **dict.fromkeys("CO ID MT NM UT WY".split(), "US-MT"), **dict.fromkeys("CA NV OR WA".split(), "US-PT"),
           "AZ": "US-AZ", "AK": "US-AK", "HI": "US-HI"}


def cfg():
    p = ROOT / "config.json"
    if not p.exists():
        sys.exit("No config.json yet: copy config.example.json to config.json and fill it in (or ask Claude to).")
    return json.loads(p.read_text(encoding="utf-8"))


def pick(options, key):
    """Same lead -> same choice every run (so re-generating doesn't change emails)."""
    return options[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(options)]


# ---------- 1. find ----------
MASK = ("places.id,places.displayName,places.formattedAddress,places.addressComponents,places.websiteUri,places.rating,"
        "places.userRatingCount,places.nationalPhoneNumber,places.primaryTypeDisplayName,places.businessStatus,places.googleMapsUri,nextPageToken")


def places(query, key, reviews):
    """Text Search (New): up to 20 per page, up to 3 pages. Split big areas into several queries to get more."""
    out, token = [], None
    for _ in range(3):
        body = {"textQuery": query, "pageSize": 20, **({"pageToken": token} if token else {})}
        req = urllib.request.Request("https://places.googleapis.com/v1/places:searchText", data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": MASK + (",places.reviews" if reviews else "")})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        out += d.get("places", [])
        token = d.get("nextPageToken")
        if not token:
            break
    return out


def find(batch):
    c, key = cfg(), os.environ.get("GOOGLE_PLACES_KEY") or sys.exit("Set GOOGLE_PLACES_KEY (Google Cloud -> Places API (New) -> API key, restricted to that API).")
    DATA.mkdir(exist_ok=True)
    seen, n = set(), 0
    with open(DATA / f"{batch}.places.jsonl", "w", encoding="utf-8") as f:
        for q in c["search"]["queries"]:
            got = places(q, key, c["search"].get("reviews"))
            for p in got:
                if p["id"] not in seen:
                    seen.add(p["id"]); n += 1
                    f.write(json.dumps({**p, "_query": q}, ensure_ascii=False) + "\n")
            print(f"  {len(got):3} · {q}", flush=True)
    print(n, "places ->", f"data/{batch}.places.jsonl")


# ---------- 2. profile ----------
def place_of(p, extra_tz=None):
    """(country, 'City, REGION', time zone) from Google's address components."""
    comp = {t: c for c in p.get("addressComponents", []) for t in c.get("types", [])}
    cc = (comp.get("country") or {}).get("shortText", "")
    cc = "UK" if cc == "GB" else cc
    city = (comp.get("locality") or comp.get("postal_town") or comp.get("administrative_area_level_2") or {}).get("longText", "")
    reg = (comp.get("administrative_area_level_1") or {}).get("shortText", "")
    if not cc or not city:
        return None
    tzs = {**TZ, **(extra_tz or {})}
    if cc in ("US", "CA", "AU"):
        tz = tzs.get(US_ZONE.get(reg, "US")) if cc == "US" else tzs.get(reg, tzs[cc])
        return cc, f"{city}, {reg}", tz
    return cc, f"{city}, {cc}", tzs.get(cc, "")


def get(url):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12, context=CTX)
    return r.geturl(), r.read(600000).decode("utf-8", "ignore")


def check_site(host, signals):
    """Which configured signals this website shows, plus the emails it lists. None if it doesn't load."""
    https = True
    try:
        url, h = get("https://" + host)
    except Exception:
        https = False
        try:
            url, h = get("http://" + host)
        except Exception:
            return None
    found = {}
    for s in signals:
        ck = s["check"]
        if ck == "no_https" and (not https or url.startswith("http://")):
            found[s["id"]] = {}
        elif ck == "not_mobile" and not re.search(r"<meta[^>]+name=[\"']?viewport", h, re.I):
            found[s["id"]] = {}
        elif ck == "old_copyright":
            yrs = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", h, re.I)]
            if yrs and max(yrs) <= date.today().year - 2:
                found[s["id"]] = {"year": max(yrs)}
        elif ck == "no_description" and not re.search(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'][^\"']{20,}", h, re.I):
            found[s["id"]] = {}
        elif ck == "old_html" and re.search(r"<table[^>]*(width|bgcolor)=|<font |<center>|\.swf|<frameset|jquery[-.]1\.\d", h, re.I):
            found[s["id"]] = {}
        elif ck == "missing" and not re.search(s["pattern"], h, re.I):
            found[s["id"]] = {}
        elif ck == "present" and re.search(s["pattern"], h, re.I):
            found[s["id"]] = {}
    ems = set(e.lower().rstrip(".") for e in re.findall(r"mailto:([^\"'?>\s]+)", h, re.I) + EM.findall(h))
    if not ems:
        for path in ("/contact", "/contact-us", "/contact.html", "/contact-us/"):
            try:
                ems |= set(e.lower().rstrip(".") for e in EM.findall(get(re.match(r"https?://[^/]+", url).group(0) + path)[1]))
                if ems:
                    break
            except Exception:
                pass
    return found, [e for e in ems if e.count("@") == 1 and not BAD_EM.search(e) and len(e) < 60]


def best_email(host, ems):
    base = host.split(".")[-2] if "." in host else host
    own = [e for e in ems if base[:6] in e.split("@")[1]]
    free = [e for e in ems if FREE_EM.search(e) and not re.match(r"(info|test|user|name|email)@", e)]
    good = sorted(own or free, key=lambda e: (not e.startswith(("info", "office", "service", "contact", "sales", "hello")), len(e)))
    return good[0] if good else ""


# Complaints in 1-3 star reviews that a better website or process fixes. Only used when search.reviews is true.
COMPLAINTS = [
    r"(never|didn'?t|did not|don'?t|doesn'?t|won'?t) (answer|pick(ed)? up|call(ed)? (me |us )?back|return(ed)? (my |our )?(call|message)s?)|nobody (answer|pick)|straight to voicemail",
    r"(never|didn'?t|did not) (get|got|receive|received|send|sent)( me| us)? (a |the |my |our )?(quote|estimate)|(quote|estimate)s? never (came|arrived)",
    r"(never|didn'?t|did not|don'?t|doesn'?t) (respond|reply|replied|responded|get back)",
    r"(web ?site|site)\W+(is |was )?(down|broken|outdated|out of date|useless|not working)|(can'?t|couldn'?t|no way to) (book|schedule) online",
]


def review_quote(p):
    """A short quote (max 15 words, no author) from a recent bad review that names a fixable problem."""
    for r in p.get("reviews", []):
        txt = (r.get("originalText") or r.get("text") or {}).get("text", "")
        if (r.get("rating") or 5) > 3 or not txt:
            continue
        for rx in COMPLAINTS:
            m = re.search(rx, txt, re.I)
            if m:
                ini = max(txt.rfind(c, 0, m.start()) for c in ".!?\n") + 1
                fin = min([i for i in (txt.find(c, m.start()) for c in ".!?\n") if i != -1] or [len(txt)])
                w = txt[ini:fin].split()
                return " ".join(w[:15]) + ("…" if len(w) > 15 else "")
    return ""


def known():
    """Websites and emails already in earlier batches: never contact anyone twice."""
    hosts, emails = set(), set()
    for f in DATA.glob("*.leads.csv"):
        for r in csv.DictReader(open(f, encoding="utf-8"), delimiter=";"):
            hosts.add(r["web"]); emails.add(r["email"])
    return hosts, emails


def profile(batch):
    c = cfg()
    skip = re.compile("|".join(map(re.escape, c["search"].get("skip_hosts", []))) or r"^$")
    rows = [json.loads(l) for l in open(DATA / f"{batch}.places.jsonl", encoding="utf-8") if l.strip()]
    old_hosts, old_emails = known()
    cand = {}
    for p in rows:
        host = re.sub(r"^https?://(www\.)?", "", (p.get("websiteUri") or "").lower()).split("/")[0]
        if host and not skip.search(host) and host not in old_hosts and host not in cand and p.get("businessStatus", "OPERATIONAL") == "OPERATIONAL":
            cand[host] = p
    print(len(rows), "places ·", len(cand), "new websites to check")
    with cf.ThreadPoolExecutor(24) as ex:
        res = dict(zip(cand, ex.map(lambda h: check_site(h, c["signals"]), cand)))
    start = 1 + max([int(r["#"]) for f in DATA.glob("*.leads.csv") for r in csv.DictReader(open(f, encoding="utf-8"), delimiter=";")] or [0])
    out, n = [], start
    for host, p in cand.items():
        if not res[host]:
            continue
        found, ems = res[host]
        email, where = best_email(host, ems), place_of(p, c.get("timezones"))
        if not email or email in old_emails or not where or (c["offer"].get("only_with_signal", True) and not found):
            continue
        old_emails.add(email)
        out.append([n, *where, p.get("displayName", {}).get("text", ""), (p.get("primaryTypeDisplayName") or {}).get("text", ""),
                    f"{p.get('rating') or 0}({p.get('userRatingCount') or 0})", p.get("nationalPhoneNumber", ""), host, email,
                    json.dumps(found), review_quote(p), "pending"])
        n += 1
    with open(DATA / f"{batch}.leads.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(FIELDS)
        w.writerows(out)
    print(f"{len(out)} leads with an email (and a signal) -> data/{batch}.leads.csv (#{start}-#{n - 1})")


# ---------- 3. emails ----------
TRADES = [  # (pattern in the Google category, plural, moment when customers look for them)
    (r"plumb", "plumbers", "When a pipe bursts or the water heater quits"),
    (r"electric", "electricians", "When the power goes out or a breaker keeps tripping"),
    (r"roof", "roofers", "When there's a leak after a storm"),
    (r"hvac|air cond|heating", "HVAC companies", "When the AC dies in the middle of summer"),
    (r"paint", "painters", "When someone's getting a few quotes to repaint the house"),
    (r"landscap|lawn|garden", "landscapers", "When someone wants the yard sorted before summer"),
    (r"pest", "pest control companies", "When someone spots termites or a mouse in the kitchen"),
    (r"lock", "locksmiths", "When someone's locked out at night"),
    (r"clean", "cleaning companies", "When someone's comparing cleaners on a Sunday night"),
    (r"dent", "dentists", "When a tooth starts hurting on a Friday"),
    (r"physio|chiro", "clinics", "When someone's back goes out and they want an appointment this week"),
    (r"restaurant|cafe|bar", "restaurants", "When someone's deciding where to eat tonight"),
]
LOCAL = {"UK": [("HVAC companies", "heating engineers"), ("the AC dies in the middle of summer", "the boiler packs in mid-winter"), ("the water heater quits", "the boiler packs in"), ("yard", "garden")],
         "AU": [("HVAC companies", "air con companies"), ("the AC dies", "the air con dies"), ("yard", "garden")],
         "NZ": [("HVAC companies", "heat pump installers"), ("the AC dies in the middle of summer", "the heat pump dies in the middle of winter"), ("yard", "garden")]}


def trade(category):
    for rx, plural, moment in TRADES:
        if re.search(rx, category or "", re.I):
            return plural, moment
    return "local businesses", "When someone needs help fast"


def clean_name(name):
    name = re.split(r"\s+[-|–]\s+", name.strip())[0]
    name = re.sub(r",?\s+(inc|llc|l\.l\.c|ltd|limited|pty\.? ?ltd|co|corp|company)\.?$", "", name, flags=re.I).strip(" .,")
    return name.title() if name.isupper() else name


def build(r, c):
    b, o = c["business"], c["offer"]
    name, k, country = clean_name(r["name"]), r["email"], r["country"]
    city = r["city"].rsplit(",", 1)[0] if country in ("UK", "NZ", "IE") else r["city"]
    plural, moment = trade(r["category"])
    for a, z in LOCAL.get(country, []):
        plural, moment = plural.replace(a, z), moment.replace(a, z)
    sig = {s["id"]: s for s in c["signals"]}
    found = json.loads(r["signals"] or "{}")
    lines = [sig[i]["line"].format(**v) for i, v in found.items() if i in sig]
    m = re.match(r"([\d.]+)\((\d+)\)", r["rating"])
    rating, reviews = m.groups() if m else ("0", "0")
    praise = f" {rating} stars from {reviews} reviews on Google, so your customers clearly like you." if float(rating) >= 4.5 and int(reviews) >= 10 else ""
    opener = pick([f"I was looking up {plural} around {city} and came across {name}.",
                   f"I came across {name} while searching for {plural} in {city}.",
                   f"I found {name} on Google Maps while looking at {plural} in {city}."], k + "o")
    problem = ""
    if r.get("review_quote"):
        problem = f"One of your recent Google reviews says \"{r['review_quote']}\". That's usually not about the work itself, it's about how easy you are to reach. "
    if lines:
        problem += f"One thing I noticed: {pick(lines, k + 'p')}. {moment}, people are usually on their phone, and they call whoever looks the most trustworthy."
    price = o["prices"].get(country, o["prices"].get("default", ""))
    what = o["what"].format(plural=plural, city=city, name=name)
    subject = pick([f"Quick question about {r['web']}", f"Your website, {name}", f"Noticed something on {r['web']}"] if lines
                   else [f"Quick question, {name}", f"{name} + {b['name']}"], k + "s")
    body = (f"Hi there,\n\n{opener}{praise}\n\n" + (problem + "\n\n" if problem else "")
            + f"I {'build' if c['mode'] == 'fix' else 'offer'} {what}." + (f" Starting at {price}." if price else "")
            + f"\n\n{o['cta']}\n\n{b['sender_name']}\n{b['name']} · {b['website']}\n\n--\n"
            + f"Not for you? Just reply \"no thanks\" and I won't email again.\n{b['name']} · {b['postal_address']}")
    return {"n": int(r["#"]), "email": k, "name": name, "city": r["city"], "country": country, "tz": r["tz"], "web": r["web"],
            "problem": ", ".join(found), "sector": plural, "subject": subject, "body": body}


def emails(batch):
    c = cfg()
    rows = [r for r in csv.DictReader(open(DATA / f"{batch}.leads.csv", encoding="utf-8"), delimiter=";") if r["status"] == "pending"]
    q = [build(r, c) for r in rows]
    assert len({e["email"] for e in q}) == len(q), "duplicate emails"
    assert all("{" not in e["body"] for e in q), "a placeholder was not filled in: check config.json"
    json.dump(q, open(DATA / f"{batch}.queue.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(DATA / f"{batch}.sample.md", "w", encoding="utf-8") as f:
        for e in q[:3] + q[-2:]:
            f.write(f"### #{e['n']} · {e['name']} · {e['email']}\n**Subject:** {e['subject']}\n\n```\n{e['body']}\n```\n\n")
    print(len(q), f"emails -> data/{batch}.queue.json · read data/{batch}.sample.md before loading")


# ---------- 4. load ----------
def load(batch):
    c = cfg()["n8n"]
    q = json.load(open(DATA / f"{batch}.queue.json", encoding="utf-8"))
    for i in range(0, len(q), 100):
        req = urllib.request.Request(c["intake_url"], data=json.dumps({"rows": q[i:i + 100]}).encode(),
                                     headers={"Content-Type": "application/json", "X-Pitchmap-Key": c["secret"]})
        with urllib.request.urlopen(req, timeout=60) as r:
            print(" ", r.read().decode()[:200])
    print(len(q), "rows sent to n8n")


def test():
    p = {"addressComponents": [{"longText": "Geelong", "types": ["locality"]}, {"shortText": "VIC", "types": ["administrative_area_level_1"]},
                               {"shortText": "AU", "types": ["country"]}]}
    assert place_of(p) == ("AU", "Geelong, VIC", "Australia/Melbourne")
    p = {"addressComponents": [{"longText": "Exeter", "types": ["postal_town"]}, {"shortText": "England", "types": ["administrative_area_level_1"]},
                               {"shortText": "GB", "types": ["country"]}]}
    assert place_of(p) == ("UK", "Exeter, UK", "Europe/London")
    p = {"addressComponents": [{"longText": "Austin", "types": ["locality"]}, {"shortText": "TX", "types": ["administrative_area_level_1"]},
                               {"shortText": "US", "types": ["country"]}]}
    assert place_of(p) == ("US", "Austin, TX", "America/Chicago")
    assert best_email("acmeplumbing.com", ["info@acmeplumbing.com", "joe@gmail.com", "bad@@x.com"]) == "info@acmeplumbing.com"
    assert review_quote({"reviews": [{"rating": 1, "text": {"text": "Great price. Nobody answered the phone for two days."}}]}) == "Nobody answered the phone for two days"
    c = json.loads((ROOT / "config.example.json").read_text())
    e = build({"#": "1", "name": "ACME PLUMBING LLC", "email": "info@acme.co.uk", "city": "Exeter, UK", "country": "UK", "tz": "Europe/London",
               "web": "acme.co.uk", "category": "Plumber", "rating": "4.8(52)", "signals": '{"old_copyright": {"year": 2019}}', "review_quote": ""}, c)
    assert "Acme Plumbing" in e["body"] and "2019" in e["body"] and "£449" in e["body"] and "boiler" in e["body"] and "{" not in e["body"], e["body"]
    print("ok")


if __name__ == "__main__":
    cmds = {"find": find, "profile": profile, "emails": emails, "load": load}
    if sys.argv[1:] == ["test"]:
        test()
    elif len(sys.argv) == 3 and sys.argv[1] in cmds:
        cmds[sys.argv[1]](sys.argv[2])
    else:
        sys.exit(__doc__)
