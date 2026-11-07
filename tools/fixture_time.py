"""Keep test fixtures current: they were written on FIXTURE_DAY, and the jobs drop anything old,
so a fixed copy slowly stops working (that took the hourly and daily jobs down on 2026-11-06).
Every test loads the fixture feed through here, with all its dates moved forward to today."""
import datetime, json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DAY = datetime.date(2026, 11, 4)          # the day tools/fixtures/feed.json was captured
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?=$|T)")

def shift_days():
    return (datetime.date.fromtimestamp(time.time()) - FIXTURE_DAY).days

def shift(obj, days):
    """Every string that starts with a YYYY-MM-DD date (plain dates and ISO timestamps), moved by `days`."""
    if isinstance(obj, dict): return {k: shift(v, days) for k, v in obj.items()}
    if isinstance(obj, list): return [shift(v, days) for v in obj]
    if isinstance(obj, str):
        m = _DATE.match(obj)
        if m:
            try: d = datetime.date(int(m[1]), int(m[2]), int(m[3])) + datetime.timedelta(days=days)
            except ValueError: return obj
            return d.isoformat() + obj[10:]
    return obj

def feed():
    """tools/fixtures/feed.json as if it had been written today."""
    return shift(json.load(open(os.path.join(HERE, "fixtures", "feed.json"), encoding="utf-8")), shift_days())

def write_feed(dst_dir):
    json.dump(feed(), open(os.path.join(dst_dir, "feed.json"), "w", encoding="utf-8"), ensure_ascii=False)

def headlines():
    """tools/fixtures/headlines.json with the newest item 30 minutes old."""
    hl = json.load(open(os.path.join(HERE, "fixtures", "headlines.json"), encoding="utf-8"))
    move = int(time.time()) - 1800 - max(x["p"] for x in hl["items"])
    for x in hl["items"]: x["p"] += move
    return hl
