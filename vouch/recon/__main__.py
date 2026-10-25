import argparse
import json
from contextlib import closing
from vouch.db import connect,DEFAULT_DB
from vouch.recon.run import prepass


def main():
    p=argparse.ArgumentParser();p.add_argument("event");p.add_argument("--db",default=DEFAULT_DB)
    a=p.parse_args()
    with closing(connect(a.db)) as conn:print(json.dumps(prepass(conn,{"type":"process_event","event":a.event}),indent=2))


if __name__=="__main__":main()
