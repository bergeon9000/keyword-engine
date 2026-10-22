"""Command line: python -m keyword_engine expand "reef tank" --niche reef"""
import argparse
import sys
from pathlib import Path

from .pipeline import load_niche, run_compare, run_expand
from .sources import SUGGEST_ENGINES


def _progress(label, i, total):
    if i == total or i % 10 == 0:
        print(f"  {label}: {i}/{total}", flush=True)


def _summary(out, run):
    status = "passed" if run["validation_passed"] else "FAILED"
    print(f"\nValidation {status}.")
    for c in run["validation"]:
        print(f"  [{'ok' if c['ok'] else 'X '}] {c['name']}: {c['detail']}")
    print("\nSources:")
    for name, s in run["sources"].items():
        print(f"  {name:14} {s['attempted']:4} requests  live {s['live']:4}  cache {s['cache']:4}  "
              f"stale {s['stale']:3}  failed {s['failed']:3}")
    print(f"\nWrote {out / 'report.md'}")


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(prog="keyword_engine", description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--niche", default="default", help="niche name in niches/ or a path to a .json")
    common.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    common.add_argument("--cache", type=Path, help="cache folder (default: ./cache in the repo)")
    common.add_argument("--out", type=Path, help="runs folder (default: ./runs in the repo)")

    ex = sub.add_parser("expand", parents=[common], help="one seed -> ranked phrases and clusters")
    ex.add_argument("seed")
    ex.add_argument("--engines", default=",".join(SUGGEST_ENGINES),
                    help=f"comma list from {','.join(SUGGEST_ENGINES)}")
    ex.add_argument("--prefix-top", type=int, default=25,
                    help="find the shortest typed prefix for the top N phrases (0 = skip)")

    cp = sub.add_parser("compare", parents=[common], help="rank many seeds against each other")
    cp.add_argument("seeds", nargs="*", help="seeds, or use --file")
    cp.add_argument("--file", type=Path, help="text file with one seed per line")

    args = ap.parse_args(argv)
    niche = load_niche(args.niche)

    if args.mode == "expand":
        engines = [e.strip() for e in args.engines.split(",") if e.strip()]
        unknown = set(engines) - set(SUGGEST_ENGINES)
        if unknown:
            ap.error(f"unknown engines: {', '.join(sorted(unknown))}")
        print(f"Expanding '{args.seed}' (niche {niche['name']}) on {', '.join(engines)}...")
        out, run = run_expand(args.seed, niche, engines, args.cache, args.out, args.refresh,
                              args.prefix_top, _progress)
        print(f"\n{run['counts']['phrases']} phrases in {run['counts']['clusters']} clusters, "
              f"{run['counts']['questions']} questions, {run['duration_s']} s.")
    else:
        seeds = list(args.seeds)
        if args.file:
            seeds += [l.strip() for l in args.file.read_text(encoding="utf-8").splitlines()
                      if l.strip() and not l.startswith("#")]
        if not seeds:
            ap.error("give seeds or --file")
        print(f"Comparing {len(seeds)} seeds (niche {niche['name']})...")
        out, run = run_compare(seeds, niche, args.cache, args.out, args.refresh, _progress)

    _summary(out, run)
    return 0 if run["validation_passed"] else 1
