"""python -m feeverified build --out dist/fees --base-url https://example.invalid
python -m feeverified verify            # nightly source-page check
python -m feeverified reviewed <platform> --note "..."   # human re-read the schedule"""

import argparse
import json
from pathlib import Path

from feeverified import site, verify


def main() -> None:
    parser = argparse.ArgumentParser(prog="feeverified")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default="dist/fees")
    b.add_argument("--base-url", default="https://example.invalid")
    sub.add_parser("verify")
    r = sub.add_parser("reviewed")
    r.add_argument("platform")
    r.add_argument("--note", default="schedule re-read against the source page")
    args = parser.parse_args()
    if args.command == "build":
        pages = site.build(args.base_url)
        n = site.write(pages, Path(args.out))
        print(f"fees_pages={n} platforms={len(site.load_schedules())} out={args.out}")
    elif args.command == "verify":
        print(json.dumps(verify.check_all()))
    elif args.command == "reviewed":
        entry = verify.mark_reviewed(args.platform, args.note)
        print(f"{args.platform} {entry['status']} reviewed_at={entry['reviewed_at']}")


if __name__ == "__main__":
    main()
