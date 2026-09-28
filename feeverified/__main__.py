"""python -m feeverified build --out dist/fees --base-url https://example.invalid
python -m feeverified verify            # nightly source-page check
python -m feeverified reviewed <platform> --note "..."   # human re-read the schedule
python -m feeverified install-browser   # (re)download the verifier's headless browser

`verify` and `reviewed` exit 3 without fetching or writing anything when the
browser cannot start."""

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
    sub.add_parser("install-browser")
    args = parser.parse_args()
    if args.command == "build":
        pages = site.build(args.base_url)
        n = site.write(pages, Path(args.out))
        print(f"fees_pages={n} platforms={len(site.load_schedules())} out={args.out}")
    elif args.command == "install-browser":
        done = verify.install_browser()
        tail = (done.stdout.strip() or done.stderr.strip()).splitlines()[-1:] or [""]
        print(f"install-browser rc={done.returncode} {tail[0][:200]}")
        raise SystemExit(done.returncode)
    else:
        try:
            if args.command == "verify":
                print(json.dumps(verify.check_all()))
            else:
                entry = verify.mark_reviewed(args.platform, args.note)
                print(f"{args.platform} {entry['status']} reviewed_at={entry['reviewed_at']}")
        except verify.BrowserUnavailable as exc:
            print(json.dumps({"error": "browser_unavailable", "detail": str(exc)}))
            raise SystemExit(verify.BROWSER_UNAVAILABLE_EXIT) from exc


if __name__ == "__main__":
    main()
