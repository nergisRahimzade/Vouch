import argparse
import json
from pathlib import Path
from vouch.evaluator.run import evaluate
from vouch.evaluator.bench import bench


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("command", choices=["score", "bench"], default="score", nargs="?")
    p.add_argument("--scripted", action="store_true")
    p.add_argument("--holdout", action="store_true")
    p.add_argument("--reveal", action="store_true")
    p.add_argument("--runs",type=int,default=3)
    p.add_argument("--concurrency",type=int,default=1)
    p.add_argument("--case",action="append")
    p.add_argument("--model",default=None)
    p.add_argument("--effort",choices=["low","medium","high","max"],default="medium")
    p.add_argument("--conversation-cache",action="store_true",help="experimental moving cache breakpoint; requires regression measurements")
    p.add_argument("--output",type=Path,default=Path("perf_baseline.json"))
    args=p.parse_args(argv)
    result=bench(args.output,args.scripted) if args.command=="bench" else evaluate(
        scripted=args.scripted,holdout=args.holdout,reveal=args.reveal,runs=args.runs,
        concurrency=args.concurrency,case_ids=args.case,model_options={"effort":args.effort,"cache_conversation":args.conversation_cache, **({"model":args.model} if args.model else {})})
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
