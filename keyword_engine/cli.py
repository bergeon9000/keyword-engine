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


def _volume(args):
    from .pipeline import REPO_ROOT
    from .volume import PROD, SANDBOX, DataForSEO, VolumeError, get_credentials, run_volume

    run_dir = args.run
    if run_dir is None:
        runs = sorted((p for p in (REPO_ROOT / "runs").glob("*") if (p / "phrases.jsonl").exists()),
                      key=lambda p: p.name)
        if not runs:
            print("No expand runs found.")
            return 1
        run_dir = runs[-1]
    ledger = REPO_ROOT / "data" / ("volume_ledger.sandbox.json" if args.sandbox else "volume_ledger.json")
    print(f"Run: {run_dir}{'  [SANDBOX: free, fake numbers]' if args.sandbox else ''}")
    try:
        client = None
        if args.spend or args.sandbox or ledger.exists():
            client = DataForSEO(*get_credentials(), base=SANDBOX if args.sandbox else PROD)
        summary = run_volume(run_dir, ledger, spend=args.spend or args.sandbox, max_cost=args.max_cost,
                             wait_minutes=args.wait, location_code=args.location,
                             language_code=args.language, client=client)
    except VolumeError as e:
        print(f"Stopped: {e}")
        return 1
    if summary:
        ev = summary["evaluation"]
        print(f"\nRank agreement (Spearman): {ev['spearman_engine_rank_vs_volume']}; "
              f"engine top 50 in real top 100: {ev['engine_top50_in_real_top100']}/50; "
              f"cost ${summary['cost_usd']}; balance {summary['balance_before']} -> {summary['balance_after']}")
        print(f"Wrote {run_dir / 'volume_report.md'}")
    return 0


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

    vo = sub.add_parser("volume", help="optional paid check: real Google volume for a run (DataForSEO)")
    vo.add_argument("run", nargs="?", type=Path, help="run folder (default: newest expand run)")
    vo.add_argument("--spend", action="store_true", help="actually queue paid tasks (otherwise a dry run)")
    vo.add_argument("--sandbox", action="store_true", help="use DataForSEO's free sandbox (fake numbers)")
    vo.add_argument("--max-cost", type=float, default=0.10, help="refuse to send if the estimate is higher (USD)")
    vo.add_argument("--wait", type=float, default=180, help="minutes to wait for queued tasks")
    vo.add_argument("--location", type=int, default=2840, help="DataForSEO location code (2840 = US)")
    vo.add_argument("--language", default="en")

    args = ap.parse_args(argv)
    if args.mode == "volume":
        return _volume(args)
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
