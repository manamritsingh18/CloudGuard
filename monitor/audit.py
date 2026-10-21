"""Local-only audit writer; invoked by the PowerShell operator script, never HTTP."""
import argparse
import os
from storage import Store

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["stop", "start", "crash"])
parser.add_argument("target", choices=["app-1", "app-2"])
parser.add_argument("outcome", choices=["requested", "succeeded", "failed"])
args = parser.parse_args()
Store(os.getenv("DB_PATH", "/data/cloudguard.db")).record_action(args.target, args.action, args.outcome)
print(f"Audit recorded: {args.action} {args.target} {args.outcome}")
