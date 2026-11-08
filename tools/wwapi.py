"""
War Watch: Claude API calls (plain and Batch API), with honest cost accounting in budget.json.

Every call adds its cost to budget.json, also when it fails part-way (a failed call that may have been billed counts
a safe estimate), so the monthly cap never trusts a number that is too low. Standard library only.
"""
import json, os, re, time, datetime, socket, http.client, urllib.request, urllib.error

API = "https://api.anthropic.com/v1"
SEARCH_TOOL = os.environ.get("WW_SEARCH_TOOL", "web_search_20250305")
MONTHLY_CAP_USD = float(os.environ.get("WW_MONTHLY_CAP_USD", "45"))
USE_BATCH = os.environ.get("WW_USE_BATCH", "1") == "1"
BATCH_WAIT_MIN = float(os.environ.get("WW_BATCH_WAIT_MIN", "100"))
# One time budget for the whole process (the daily job's limit is 240 min): each batch waits at most until JOB_MIN minus
# a reserve for running late requests directly and saving, so 5-6 slow stages in a row can't run past the job's limit
# and lose the day's work.
JOB_MIN = float(os.environ.get("WW_JOB_MIN", "200"))
RESERVE_MIN = 25
_T0 = time.time()
EFFORT_ON = os.environ.get("WW_EFFORT", "on") != "off"
# USD per million tokens: input, output, cache write (5 min), cache read. platform.claude.com pricing, checked 2026-11-03.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.10),
          "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01)}
SEARCH_USD = 0.01                                        # $10 per 1,000 searches, batch or not
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET = os.path.join(ROOT, "budget.json")

def step(m): print(f"[STEP] {m}", flush=True)
def ok(m): print(f"[OK] {m}", flush=True)
def warn(m): print(f"[WARN] {m}", flush=True)
def err(m): print(f"[ERROR] {m}", flush=True)
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def save_json(path, obj, indent=1):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)

# ---------- budget ----------
def load_budget():
    month = datetime.date.today().isoformat()[:7]
    try: b = json.load(open(BUDGET, encoding="utf-8"))
    except Exception: b = {}
    if b.get("month") != month: b = {"month": month, "usd": 0.0, "runs": []}
    b["cap"] = MONTHLY_CAP_USD        # read by Aegis, which paces Matt's other Claude jobs against $100 minus this cap
    return b

def usage_usd(model, u, batch=False):
    pin, pout, pcw, pcr = PRICES.get(model, (4.0, 20.0, 5.0, 0.20))
    tok = (u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout +
           u.get("cache_creation_input_tokens", 0) * pcw + u.get("cache_read_input_tokens", 0) * pcr) / 1e6
    if batch: tok *= 0.5
    return tok + (u.get("server_tool_use") or {}).get("web_search_requests", 0) * SEARCH_USD

def _retotal(b):
    """total_usd (the stages here + translation + focus updates) kept current, not a copy from the start of the run."""
    if "total_usd" in b: b["total_usd"] = round(b["usd"] + b.get("headlines_usd", 0.0) + b.get("focus_usd", 0.0), 4)
    return b.get("total_usd", b["usd"])

def add_cost(b, model, u, label, batch=False):
    usd = usage_usd(model, u, batch)
    tin = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    searches = (u.get("server_tool_use") or {}).get("web_search_requests", 0)
    b["usd"] = round(b["usd"] + usd, 4)
    b["runs"] = (b.get("runs") or [])[-80:] + [{"at": now_iso(), "what": label, "model": model, "in": tin,
                                                "out": u.get("output_tokens", 0), "searches": searches,
                                                "batch": batch, "usd": round(usd, 4)}]
    total = _retotal(b)
    save_json(BUDGET, b)
    ok(f"{label}: {tin} in / {u.get('output_tokens', 0)} out tokens, {searches} searches{' (batch, half price)' if batch else ''}, "
       f"about ${usd:.2f} (month so far ${total:.2f} of ${MONTHLY_CAP_USD:.0f})")

def add_estimate(b, model, usd, label):
    """A call that failed after it was sent may still be billed: count a safe estimate so the cap stays honest."""
    b["usd"] = round(b["usd"] + usd, 4)
    b["runs"] = (b.get("runs") or [])[-80:] + [{"at": now_iso(), "what": label + " (failed; estimated)", "model": model,
                                                "in": 0, "out": 0, "searches": 0, "usd": usd}]
    _retotal(b)
    save_json(BUDGET, b)
    warn(f"{label}: failed; counted an estimated ${usd:.2f} toward the cap")

def empty_usage(): return {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                           "cache_read_input_tokens": 0, "server_tool_use": {"web_search_requests": 0}}

def add_usage(total, u):
    for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        total[k] += (u or {}).get(k, 0) or 0
    total["server_tool_use"]["web_search_requests"] += ((u or {}).get("server_tool_use") or {}).get("web_search_requests", 0) or 0

# ---------- URLs ----------
def norm_url(u):
    """For comparing URLs only: no fragment, no tracking parameters, no www, no trailing slash, lower case."""
    u = str(u or "").strip().split("#", 1)[0]
    if "?" in u:
        base, q = u.split("?", 1)
        keep = [p for p in q.split("&") if p and not re.match(r"(utm_|at_|fbclid|gclid|ocid|cmpid|mc_)", p, re.I)]
        u = base + ("?" + "&".join(keep) if keep else "")
    u = re.sub(r"^https?://(www\.)?", "https://", u, flags=re.I)
    return u.rstrip("/").lower()

def search_urls(obj):
    """Every URL the search tool really returned (results and citations, also nested inside code-execution results),
    so research notes can't carry invented links."""
    out = set()
    def walk(x):
        if isinstance(x, dict):
            if x.get("type") in ("web_search_result", "web_search_result_location") and x.get("url"): out.add(norm_url(x["url"]))
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(obj)
    return out

# ---------- HTTP ----------
class CallFailed(Exception):
    """The API call failed for good. Whatever it cost has already been counted in budget.json."""

NET_ERRORS = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, http.client.HTTPException)

def _key():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key: raise SystemExit("[ERROR] ANTHROPIC_API_KEY is not set (run Set-WarWatchSetup.cmd on the PC)")
    return key

def http(method, url, body=None, timeout=900, raw=False):
    """One HTTP call to the API with retries on rate limits, overload and network trouble. Raises CallFailed
    (with .billable False when the API refused the request before doing any work)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"x-api-key": _key(), "anthropic-version": "2023-06-01", "content-type": "application/json"}
    for attempt in range(4):
        wait = 20 * (attempt + 1)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", errors="replace")
                return text if raw else json.loads(text)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                warn(f"API {e.code}, retrying in {wait} s"); time.sleep(wait); continue
            f = CallFailed(f"API {e.code}: {detail}"); f.billable = e.code not in (400, 401, 403, 404, 413); raise f
        except NET_ERRORS as e:
            if attempt < 3: warn(f"network problem ({type(e).__name__}: {e}), retrying in {wait} s"); time.sleep(wait); continue
            f = CallFailed(f"network problem: {type(e).__name__}: {e}"); f.billable = True; raise f
    f = CallFailed("gave up"); f.billable = True; raise f

# ---------- requests ----------
def make_body(r, messages):
    body = {"model": r["model"], "max_tokens": r["max_tokens"], "system": r["system"], "messages": messages}
    if r.get("max_uses", 0) > 0:
        tool = {"type": SEARCH_TOOL, "name": "web_search", "max_uses": r["max_uses"]}
        if r.get("blocked_domains"): tool["blocked_domains"] = r["blocked_domains"][:50]
        body["tools"] = [tool]
    # Effort: lower on the routine Opus stages (boiling point, AI risk, actions, weekly reviews) to cut their thinking
    # tokens, most of what they cost. The daily analysis and the gathers keep the default. WW_EFFORT=off turns it off.
    if r.get("effort") and EFFORT_ON: body["output_config"] = {"effort": r["effort"]}
    return body

class Job:
    """One request's state across pause_turn rounds."""
    def __init__(self, r):
        self.r, self.messages = r, [{"role": "user", "content": r["user"]}]
        self.usage, self.text, self.seen, self.done, self.error = empty_usage(), "", set(), False, ""
        self.batch_usage = empty_usage()
    def take(self, resp, batch):
        add_usage(self.batch_usage if batch else self.usage, resp.get("usage"))
        content = resp.get("content") or []
        self.seen |= search_urls(content)
        self.text += "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        if resp.get("stop_reason") == "pause_turn":
            self.messages = self.messages + [{"role": "assistant", "content": content}]
            return False
        if resp.get("stop_reason") == "max_tokens": warn(f"{self.r['label']}: answer hit max_tokens; it may be cut off")
        self.done = True
        return True

def _book(job, budget):
    r = job.r
    if any(v for k, v in job.batch_usage.items() if k != "server_tool_use") or job.batch_usage["server_tool_use"]["web_search_requests"]:
        add_cost(budget, r["model"], job.batch_usage, r["label"] + ("" if job.done else " (failed part-way)"), batch=True)
    if any(v for k, v in job.usage.items() if k != "server_tool_use") or job.usage["server_tool_use"]["web_search_requests"]:
        add_cost(budget, r["model"], job.usage, r["label"] + ("" if job.done else " (failed part-way)"))

def _run_sync(job, budget):
    sent = False
    try:
        for _ in range(6):
            try:
                sent = True
                resp = http("POST", API + "/messages", make_body(job.r, job.messages))
            except CallFailed as e:
                if not getattr(e, "billable", True): sent = False
                raise
            if job.take(resp, batch=False): return
        job.done = True                                    # six rounds: keep what we have
    except CallFailed as e:
        job.error = str(e)
    finally:
        if sent and not job.done and not job.text and not job.usage["output_tokens"] and not job.batch_usage["output_tokens"]:
            add_estimate(budget, job.r["model"], job.r.get("estimate", 0.5), job.r["label"])

def _run_batch(jobs, budget, deadline):
    """Rounds of one batch each (a round per pause_turn). Jobs not finished by the deadline are left for the caller."""
    for _ in range(6):
        todo = [j for j in jobs if not j.done and not j.error]
        if not todo or time.time() > deadline: return
        reqs = [{"custom_id": f"r{i}", "params": make_body(j.r, j.messages)} for i, j in enumerate(jobs) if j in todo]
        try: b = http("POST", API + "/messages/batches", {"requests": reqs})
        except CallFailed as e: warn(f"couldn't start a batch ({e}); running the requests directly"); return
        step(f"batch {b.get('id')}: {len(reqs)} request(s) queued")
        while b.get("processing_status") != "ended":
            if time.time() > deadline:
                warn(f"batch {b.get('id')} not done in time; cancelling it and running directly at full price")
                try: http("POST", f"{API}/messages/batches/{b['id']}/cancel", {})
                except CallFailed: pass
                # Wait for the cancel to finish (up to 5 min) and read what did finish, so it is booked and not paid twice.
                for _ in range(20):
                    time.sleep(15)
                    try: b = http("GET", f"{API}/messages/batches/{b['id']}")
                    except CallFailed: continue
                    if b.get("processing_status") == "ended": break
                if b.get("processing_status") != "ended" or not b.get("results_url"):
                    for j in todo: add_estimate(budget, j.r["model"], round(j.r.get("estimate", 0.5) * 0.5, 4), j.r["label"] + " (batch cancelled)")
                    return
                break
            time.sleep(30)
            try: b = http("GET", f"{API}/messages/batches/{b['id']}")
            except CallFailed as e: warn(f"batch status check failed ({e}); retrying")
        try: lines = http("GET", b["results_url"], raw=True, timeout=300).splitlines()
        except (CallFailed, KeyError) as e: warn(f"couldn't read batch results ({e}); running directly"); return
        for line in lines:
            if not line.strip(): continue
            res = json.loads(line)
            i = int(res["custom_id"][1:])
            out = res.get("result") or {}
            if out.get("type") == "succeeded": jobs[i].take(out["message"], batch=True)
            elif out.get("type") == "errored":
                e = (out.get("error") or {}).get("error") or out.get("error") or {}
                if str(e.get("type", "")) in ("invalid_request_error", "authentication_error", "permission_error"):
                    jobs[i].error = f"API refused: {e.get('message', e)}"
            # canceled / expired / overloaded errors: retried below (next round or directly)

def run(requests, budget, batch=None):
    """Run requests ({label, model, system, user, max_uses, max_tokens, estimate}) and return {label: (text, urls)}.
    Uses the Batch API (half price) when enabled, falling back to direct calls; costs are booked either way.
    A request that failed for good is missing from the result."""
    batch = USE_BATCH if batch is None else batch
    jobs = [Job(r) for r in requests]
    left = _T0 + JOB_MIN * 60 - time.time()
    if left < 10 * 60:
        warn(f"out of time for {', '.join(r['label'] for r in requests)} ({left / 60:.0f} min left of {JOB_MIN:.0f}); skipped")
        return {}
    if batch:
        wait = min(BATCH_WAIT_MIN * 60, left - RESERVE_MIN * 60)
        try: _run_batch(jobs, budget, time.time() + max(wait, 5 * 60))
        except Exception as e: warn(f"batch mode failed ({type(e).__name__}: {e}); running directly")
    for j in jobs:
        if not j.done and not j.error: _run_sync(j, budget)
    for j in jobs:                    # the API refused the effort setting: once more without it, rather than lose the stage
        if j.error and j.r.get("effort") and ("effort" in j.error or "output_config" in j.error):
            warn(f"{j.r['label']}: effort setting refused ({j.error[:120]}); running it again without")
            j.r = dict(j.r, effort=None); j.error = ""; j.messages = [{"role": "user", "content": j.r["user"]}]
            _run_sync(j, budget)
    out = {}
    for j in jobs:
        _book(j, budget)
        if j.done: out[j.r["label"]] = (j.text, j.seen)
        else: err(f"{j.r['label']}: {j.error or 'no answer'}")
    return out
