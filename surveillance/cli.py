"""Command-line wrapper so any agent framework that can run shell commands can use the tools.

  python -m surveillance.cli tools                       # list tools + one-line descriptions
  python -m surveillance.cli schema > tools_schema.json  # OpenAI-style tool schemas
  python -m surveillance.cli days                        # which trading days have data
  python -m surveillance.cli call list_alerts limit=5
  python -m surveillance.cli call get_alert alert_id=A-0047
  python -m surveillance.cli call search_chats '{"participant": "T01", "start": "14:30", "end": "15:00"}'
  python -m surveillance.cli --day 2026-09-26 call list_alerts   # work on another day
  python -m surveillance.cli reset                       # clear decisions + audit log for the day
  python -m surveillance.cli reset --all                 # ...for every day

The day defaults to the demo day (2026-10-03), or the SURV_DAY environment variable.
Output is always JSON on stdout.
"""
import json
import os
import shutil
import sys

from . import config as C


def parse_args(argv):
    if len(argv) == 1 and argv[0].lstrip().startswith("{"):
        return json.loads(argv[0])
    out = {}
    for a in argv:
        if "=" not in a:
            raise SystemExit(f"argument {a!r} should look like key=value")
        k, v = a.split("=", 1)
        try:
            out[k] = json.loads(v)          # numbers, lists, true/false
        except json.JSONDecodeError:
            out[k] = v                      # plain strings
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "--day":
        os.environ["SURV_DAY"] = argv[1]
        argv = argv[2:]
    from .tools import TOOL_SCHEMAS, TOOLS, audit_path, dispositions_path   # after SURV_DAY is set

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "tools":
        result = {s["function"]["name"]: s["function"]["description"] for s in TOOL_SCHEMAS}
    elif cmd == "schema":
        result = TOOL_SCHEMAS
    elif cmd == "days":
        result = {"days": sorted(p.name for p in C.DATA_ROOT.iterdir() if p.is_dir()) if C.DATA_ROOT.exists() else [],
                  "current": C.day()}
    elif cmd == "reset":
        if "--all" in rest:
            shutil.rmtree(C.OUT_ROOT, ignore_errors=True)
            result = {"ok": True, "cleared": "all days"}
        else:
            for p in (dispositions_path(), audit_path()):
                p.unlink(missing_ok=True)
            result = {"ok": True, "cleared": C.day()}
    elif cmd == "call":
        if not rest or rest[0] not in TOOLS:
            result = {"error": f"unknown tool; choose one of: {', '.join(TOOLS)}"}
        else:
            result = TOOLS[rest[0]](**parse_args(rest[1:]))
    else:
        result = {"error": f"unknown command {cmd!r}; use tools | schema | days | call | reset"}
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
