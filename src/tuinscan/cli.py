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
        if naam == "download":
            sp.add_argument("--vernieuw", action="store_true", help="haal alles opnieuw op")
            sp.add_argument("--lijst-lagen", action="store_true", help="toon WMTS-lagen en stop")
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
    if args.stap == "inspecteer":
        from tuinscan.inspecteer import inspecteer

        return inspecteer(cfg, mappen)
    if args.stap == "download":
        from tuinscan.download import druk_lagen_af, voer_download_uit

        if args.lijst_lagen:
            druk_lagen_af(cfg)
            return 0
        r = voer_download_uit(cfg, mappen, args.vernieuw)
        print(
            f"download: opgehaald {len(r.opgehaald)}, overgeslagen {len(r.overgeslagen)}, "
            f"niet geconfigureerd {r.niet_geconfigureerd}, leeg {r.leeg}, mislukt {r.mislukt}"
        )
        return 0
    print(f"Stap '{args.stap}': nog niet beschikbaar")
    return 2


if __name__ == "__main__":
    sys.exit(main())
