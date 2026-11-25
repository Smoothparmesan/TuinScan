"""Commandoregel: één subcommando per stap."""

import argparse
import logging
import os
import sys
from pathlib import Path

from tuinscan.config import ConfigFout, laad_config
from tuinscan.mappen import Mappen, OneDriveFout, controleer_buiten_onedrive

STAPPEN = {
    "inspecteer": "Stel kolomkoppeling voor uit de Tobias-export",
    "download": "Download PDOK-data voor het werkgebied",
    "kalibreer": "Toon ExG/NDVI op wegen, daken en groen en stel drempels per laag voor",
    "tuinen": "Bepaal de tuinvlakken",
    "score": "Bereken signalen en score",
    "rapport": "Maak het HTML-rapport",
    "voorbeeld": "Maak een voorbeeld-export uit BAG (tot de Tobias-export er is)",
    "alles": "Voer download, tuinen, score, rapport en terugkoppeling maak uit",
    "opruimen": "Verwijder cache, ronde-uitvoer en exportbestanden",
    "terugkoppeling": "Maak of lees het terugkoppelbestand",
}


LOGBESTAND = "tuinscan.log"
log = logging.getLogger("tuinscan")


class _KortFormaat(logging.Formatter):
    """Console: alleen niveau en melding, geen traceback (die staat in tuinscan.log)."""

    def format(self, record: logging.LogRecord) -> str:
        niveau = {"WARNING": "waarschuwing", "ERROR": "fout", "CRITICAL": "fout"}
        return f"{niveau.get(record.levelname, record.levelname.lower())}: {record.getMessage()}"


def _start_logging(mappen: Mappen, stap: str) -> tuple[list[logging.Handler], int]:
    """INFO naar ronde_dir/tuinscan.log (UTF-8), WARNING kort naar de console.

    `opruimen` schrijft geen logbestand: die verwijdert juist de ronde-map.
    """
    handlers: list[logging.Handler] = []
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(_KortFormaat())
    handlers.append(console)
    if stap != "opruimen":
        mappen.ronde_dir.mkdir(parents=True, exist_ok=True)
        bestand = logging.FileHandler(mappen.ronde_dir / LOGBESTAND, encoding="utf-8")
        bestand.setLevel(logging.INFO)
        bestand.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        handlers.append(bestand)
    oud = log.level
    log.setLevel(logging.INFO)
    for h in handlers:
        h._tuinscan = True  # herkenbaar, zodat een volgende aanroep niets dubbel logt
        log.addHandler(h)
    return handlers, oud


def _stop_logging(handlers: list[logging.Handler], oud_niveau: int) -> None:
    for h in handlers:
        log.removeHandler(h)
        h.close()  # Windows: anders blijft tuinscan.log vergrendeld
    log.setLevel(oud_niveau)


def _console_veilig() -> None:
    """Tekens die de Windows-console (cp1252) niet kent, worden vervangen i.p.v. te crashen."""
    for stroom in (sys.stdout, sys.stderr):
        if hasattr(stroom, "reconfigure"):
            try:
                stroom.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _basis(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--config", required=True, metavar="PAD", help="pad naar config-YAML")
    sp.add_argument(
        "--ronde", default=None, metavar="NAAM", help="rondenaam (standaard gebied.naam)"
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tuinscan", description="Rangschik tuinen op open geodata.")
    sub = p.add_subparsers(dest="stap", metavar="STAP")
    for naam, hulp in STAPPEN.items():
        sp = sub.add_parser(naam, help=hulp, description=hulp)
        if naam == "terugkoppeling":
            sub2 = sp.add_subparsers(dest="actie", metavar="ACTIE", required=True)
            for actie, ahulp in (
                ("maak", "Maak het terugkoppelbestand"),
                ("lees", "Lees het ingevulde bestand en bereken kwaliteit"),
            ):
                ap = sub2.add_parser(actie, help=ahulp, description=ahulp)
                _basis(ap)
                if actie == "maak":
                    ap.add_argument(
                        "--overschrijf",
                        action="store_true",
                        help="overschrijf ook een bestand met ingevulde oordelen",
                    )
                else:
                    ap.add_argument(
                        "--bestand",
                        default=None,
                        metavar="PAD",
                        help="ander Excel-bestand dan terugkoppeling.xlsx",
                    )
            continue
        _basis(sp)
        if naam == "download":
            sp.add_argument("--vernieuw", action="store_true", help="haal alles opnieuw op")
            sp.add_argument("--lijst-lagen", action="store_true", help="toon WMTS-lagen en stop")
        if naam == "voorbeeld":
            sp.add_argument("--aantal", type=int, default=200, metavar="N", help="aantal VHE's")
            sp.add_argument("--seed", type=int, default=1, metavar="S", help="toevalszaad")
        if naam == "opruimen":
            sp.add_argument("--bewaar-correcties", action="store_true",
                            help="bewaar invoer/tuincorrecties.gpkg")  # fmt: skip
    return p


def _extra_stap(args, cfg, mappen) -> int:
    from tuinscan.stappen import StapFout, stap_alles, stap_opruimen
    from tuinscan.voorbeeld import maak_voorbeeld_export

    try:
        if args.stap == "voorbeeld":
            pad = maak_voorbeeld_export(cfg, mappen, args.aantal, args.seed)
            print(f"Voorbeeld-export geschreven naar {pad}")
            print(f"Gebruik: zet in de config tobias.bestand: {pad.name}, draai 'tuinscan "
                  "inspecteer' en controleer kolommen.yaml")  # fmt: skip
        elif args.stap == "alles":
            stap_alles(cfg, mappen)
        else:
            naam = mappen.ronde_dir.name
            tekst = input(f"Typ de rondenaam '{naam}' om alles te verwijderen: ")
            stap_opruimen(cfg, mappen, tekst, args.bewaar_correcties)
    except (ConfigFout, StapFout) as e:
        print(f"Fout: {e}", file=sys.stderr)
        return 1
    except EOFError:
        print("Fout: geen bevestiging ontvangen; niets verwijderd", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    _console_veilig()
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
    handlers, oud = _start_logging(mappen, args.stap)
    try:
        from tuinscan.config import parameterset_hash

        stap = " ".join(filter(None, (args.stap, getattr(args, "actie", None))))
        log.info("stap %s gestart (ronde %s, parameterset %s)", stap, mappen.ronde_dir.name,
                 parameterset_hash(cfg))  # fmt: skip
        code = _voer_uit(args, cfg, mappen)
        log.info("stap %s klaar (code %d)", stap, code)
        return code
    except PermissionError as e:
        naam = e.filename or "een uitvoerbestand"
        log.error("bestand niet te schrijven: %s", naam)
        print(f"Fout: Sluit bestand {naam} (bijv. in QGIS/Excel) en probeer opnieuw",
              file=sys.stderr)  # fmt: skip
        return 1
    finally:
        _stop_logging(handlers, oud)


def _voer_uit(args, cfg, mappen) -> int:
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
    if args.stap == "kalibreer":
        from tuinscan.kalibreer import kalibreer
        from tuinscan.stappen import StapFout

        try:
            kalibreer(cfg, mappen)
        except (ConfigFout, StapFout) as e:
            print(f"Fout: {e}", file=sys.stderr)
            return 1
        return 0
    if args.stap == "tuinen":
        from tuinscan.stappen import StapFout, stap_tuinen

        try:
            stap_tuinen(cfg, mappen)
        except (ConfigFout, StapFout) as e:
            print(f"Fout: {e}", file=sys.stderr)
            return 1
        return 0
    if args.stap == "score":
        from tuinscan.stappen import StapFout, stap_score

        try:
            stap_score(cfg, mappen)
        except (ConfigFout, StapFout) as e:
            print(f"Fout: {e}", file=sys.stderr)
            return 1
        return 0
    if args.stap == "rapport":
        from tuinscan.stappen import StapFout, stap_rapport

        try:
            stap_rapport(cfg, mappen)
        except (ConfigFout, StapFout) as e:
            print(f"Fout: {e}", file=sys.stderr)
            return 1
        return 0
    if args.stap == "terugkoppeling":
        from tuinscan.stappen import StapFout, stap_terugkoppeling_lees, stap_terugkoppeling_maak

        try:
            if args.actie == "maak":
                stap_terugkoppeling_maak(cfg, mappen, args.overschrijf)
            else:
                stap_terugkoppeling_lees(cfg, mappen, args.bestand)
        except (ConfigFout, StapFout) as e:
            print(f"Fout: {e}", file=sys.stderr)
            return 1
        return 0
    if args.stap in ("voorbeeld", "alles", "opruimen"):
        return _extra_stap(args, cfg, mappen)
    print(f"Stap '{args.stap}': nog niet beschikbaar")
    return 2


if __name__ == "__main__":
    sys.exit(main())
