"""Vouch event-tax commands."""
import argparse
import json
import sys
from contextlib import closing
from pathlib import Path
from vouch.db import DEFAULT_DB,connect
from vouch.db.rebuild import rebuild,report
from vouch.world.scenario import load_file


def main():
    if len(sys.argv)>1 and sys.argv[1]=="preflight":
        from vouch.agent.policy import load_credentials
        from vouch.agent.chat_backend import configured, preflight
        load_credentials()
        import os
        if os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or os.environ.get("VOUCH_PROVIDER") == "jev":
            from vouch.agent.jev_backend import preflight as jev_preflight
            results = jev_preflight()
            for name,ok,reason in results: print(("GREEN " if ok else "RED   ")+name+("" if ok else ": "+reason))
            sys.exit(0 if all(ok for _,ok,_ in results) else 1)
        if not configured():
            print("RED: no model provider configured (set VOUCH_PROVIDER=deepseek and VOUCH_API_KEY in .env)");sys.exit(1)
        results=preflight()
        for name,ok,reason in results: print(("GREEN " if ok else "RED   ")+name+("" if ok else ": "+reason))
        sys.exit(0 if all(ok for _,ok,_ in results) else 1)
    if len(sys.argv)>1 and sys.argv[1] in {"score","bench"}:
        from vouch.evaluator.__main__ import main as evaluate
        evaluate(sys.argv[1:]);return
    p=argparse.ArgumentParser(prog="python -m vouch")
    sub=p.add_subparsers(dest="command",required=True)
    for command in ("rebuild","run"):
        s=sub.add_parser(command);s.add_argument("--db",type=Path,default=DEFAULT_DB);s.add_argument("--case",type=Path)
        if command=="run":
            s.add_argument("--scripted",action="store_true")
            s.add_argument("--model",default=None)
            s.add_argument("--effort",default="medium",choices=["low","medium","high","max"])
    api=sub.add_parser("api",aliases=["ui"]);api.add_argument("--db",type=Path,default=DEFAULT_DB)
    api.add_argument("--host",default="127.0.0.1");api.add_argument("--port",type=int,default=8000)
    why=sub.add_parser("why");why.add_argument("case_id");why.add_argument("--db",type=Path,default=DEFAULT_DB)
    args=p.parse_args()
    if args.command in {"api","ui"}:
        from vouch.ui.server import serve
        serve(args.host,args.port,args.db);return
    if args.command=="why":
        from vouch.agent.defend import why
        from vouch.agent.policy import ScriptedPolicy
        with closing(connect(args.db)) as conn:print(json.dumps(why(conn,args.case_id,"Why?",ScriptedPolicy()),indent=2))
        return
    scenario=load_file(args.case)[0] if args.case else None
    if args.command=="rebuild":print(report(rebuild(args.db,scenario)));return
    if not scenario:p.error("run requires --case FILE")
    if not args.db.exists():rebuild(args.db,scenario)
    from vouch.agent.runner import run_scenario
    from vouch.agent.policy import ModelPolicy,ScriptedPolicy
    with closing(connect(args.db)) as conn:
        print(json.dumps(run_scenario(conn,scenario,ScriptedPolicy() if args.scripted else ModelPolicy(model=args.model,effort=args.effort)),indent=2))


if __name__=="__main__":main()
