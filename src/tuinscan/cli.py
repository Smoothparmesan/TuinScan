"""Commandoregel: één subcommando per stap."""

import argparse
import os
import sys
from pathlib import Path

from tuinscan.config import ConfigFout, laad_config
from tuinscan.mappen import Mappen, OneDriveFout, controleer_buiten_onedrive

STAPPEN = {
    "inspecteer": "Stel kolomkoppeling voor uit de Tobias-export",
    "download": "Download PDOK-data voor het werkgebied",
    "tuinen": "Bepaal de tuinvlakken",
    "score": "Bereken signalen en score",
    "rapport": "Maak het HTML-rapport",
    "terugkoppeling": "Maak of lees het terugkoppelbestand",
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tuinscan", description="Rangschik tuinen op open geodata.")
    sub = p.add_subparsers(dest="stap", metavar="STAP")
    for naam, hulp in STAPPEN.items():
        sp = sub.add_parser(naam, help=hulp, description=hulp)
        sp.add_argument("--config", required=True, metavar="PAD", help="pad naar config-YAML")
        sp.add_argument(
            "--ronde", default=None, metavar="NAAM", help="rondenaam (standaard gebied.naam)"
        )
    return p


def main(argv: list[str] | None = None) -> int:
    p = _parser()
    args = p.parse_args(argv)
    if args.stap is None:
        p.print_help()
        return 2
    try:
        cfg = laad_config(Path(args.config))
        mappen = Mappen.uit_config(cfg, args.ronde)
        controleer_buiten_onedrive(mappen.alle(), os.environ)
    except (ConfigFout, OneDriveFout) as e:
        print(f"Fout: {e}", file=sys.stderr)
        return 1
    print(f"Stap '{args.stap}': nog niet beschikbaar")
    return 2


if __name__ == "__main__":
    sys.exit(main())
