"""Run the offline tests with the clock moved forward, to catch a test that only passes near the day it was written
(a fixed date in a fixture went stale on 2026-11-06 and stopped the hourly and daily jobs for a day).
Usage: python3 tools/check_tests_ahead.py DAYS [test_module ...]   (default: all four suites)"""
import datetime, os, sys, time, unittest

off = int(sys.argv[1]) * 86400
names = sys.argv[2:] or ["test_feed", "test_headlines", "test_focus", "test_actions"]
_t, _gm, _lt, _sf = time.time, time.gmtime, time.localtime, time.strftime
time.time = lambda: _t() + off                                   # date.today() reads time.time() too
time.gmtime = lambda s=None: _gm(time.time() if s is None else s)
time.localtime = lambda s=None: _lt(time.time() if s is None else s)
time.strftime = lambda f, tt=None: _sf(f, time.localtime() if tt is None else tt)

class _Later(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return datetime.datetime.fromtimestamp(time.time(), tz)
    @classmethod
    def utcnow(cls): return datetime.datetime.utcfromtimestamp(time.time())
datetime.datetime = _Later

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
result = unittest.TextTestRunner(verbosity=0).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
