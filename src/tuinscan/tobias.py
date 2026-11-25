"""Tobias-export: lezen, koptekst-detectie, kolomkoppeling en filters.

Tobias-kolomnamen staan nooit in code; alles loopt via canonieke namen die in
`invoer/kolommen.yaml` op exportkoppen worden gekoppeld.
"""

import logging
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml

from tuinscan.config import Config, ConfigFout
from tuinscan.fouten import Foutenlijst
from tuinscan.mappen import Mappen

log = logging.getLogger(__name__)

VERPLICHT = ("vhe_nummer", "bag_vbo_id", "wijk", "complex", "woningtype")
ADRES_VELDEN = ("adres", "straat", "huisnummer", "huisletter", "toevoeging", "postcode", "plaats")
OPTIONEEL = ("eigen_tuin", "standaardberging", "status", "sloop_renovatie")
ALLE_VELDEN = VERPLICHT + ADRES_VELDEN + OPTIONEEL

STAP = "tobias"
ALGEMENE_VHE = "*"
BAG_HINT = "BAG-id mogelijk afgerond door Excel; exporteer de kolom als tekst"
# Excel bewaart 15 significante cijfers: een BAG-id met gemeentecode >= 1000 (16 cijfers
# zonder voorloopnul) verliest dan zijn laatste cijfer, dat 0 wordt. Bij toeval eindigt ~10 %
# op 0; pas ruim daarboven is afronding waarschijnlijk.
AFRONDING_AANDEEL = 0.25
AFRONDING_MIN_N = 10
AFRONDING_TEKST = (
    "{n} van {totaal} BAG-id's met gemeentecode >= 1000 eindigen op 0: waarschijnlijk door "
    "Excel afgerond (15 cijfers). Koppelingen kunnen naar het verkeerde adres wijzen; "
    "exporteer de BAG-kolom als tekst"
)
_BAG_VBO = re.compile(r"\d{4}01\d{10}")
_POSTCODE = re.compile(r"\d{4}\s?[A-Za-z]{2}")
_HEADER_RIJEN = 15

ALIASSEN: dict[str, tuple[str, ...]] = {
    "vhe_nummer": (
        "vhe", "vhenummer", "vhenr", "eenheid", "verhuureenheid",
        "rentalobject", "rentalobjectid", "objectnummer",
    ),  # fmt: skip
    "wijk": ("wijk", "buurt", "district", "wijknaam"),
    "complex": ("complex", "complexnummer", "complexnr", "cluster", "bouwblok"),
    "woningtype": ("woningtype", "type", "objecttype", "woningsoort", "typewoning"),
    "eigen_tuin": ("tuin", "eigentuin", "heefttuin"),
    "standaardberging": ("berging", "standaardberging"),
    "status": ("status", "verhuurstatus", "leegstand"),
    "sloop_renovatie": ("sloop", "renovatie", "project"),
    "adres": ("adres", "volledigadres"),
    "straat": ("straat", "straatnaam"),
    "huisnummer": ("huisnummer", "huisnr"),
    "huisletter": ("huisletter",),
    "toevoeging": ("toevoeging", "huisnummertoevoeging"),
    "postcode": ("postcode",),
    "plaats": ("plaats", "woonplaats"),
}


@dataclass(frozen=True)
class MappingVoorstel:
    kolom: str | None
    zekerheid: Literal["hoog", "middel", "laag"]
    reden: str


# --- hulpfuncties -----------------------------------------------------------------------


def _leeg(w: object) -> bool:
    if w is None or w is pd.NA or (isinstance(w, float) and w != w):
        return True
    return str(w).strip().casefold() in ("", "nan", "none", "<na>")


def _is_getal(w: object) -> bool:
    try:
        float(str(w).strip().replace(",", "."))
    except ValueError:
        return False
    return True


def _schoon(w: object) -> str:
    """Celwaarde als nette tekst; '' voor leeg; '12.0' wordt '12'."""
    if _leeg(w):
        return ""
    s = str(w).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def _norm_kop(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --- lezen ----------------------------------------------------------------------------------


def _vind_kopregel(raw: pd.DataFrame) -> int:
    for i in range(min(_HEADER_RIJEN, len(raw))):
        cellen = [c for c in raw.iloc[i] if not _leeg(c)]
        if len(cellen) < 3:
            continue
        tekst = sum(1 for c in cellen if not _is_getal(c))
        if tekst / len(cellen) >= 0.5:
            return i
    raise ConfigFout(
        f"geen koptekst gevonden in de eerste {_HEADER_RIJEN} rijen van de export; "
        "zet 'tobias.kopregel' in de config"
    )


def lees_ruwe_export(pad: Path, blad: str | None, kopregel: int | None) -> pd.DataFrame:
    """Alle cellen als str; koptekst automatisch of via `kopregel` (1-based)."""
    try:
        raw = pd.read_excel(pad, sheet_name=blad if blad is not None else 0, header=None, dtype=str)
    except FileNotFoundError as e:
        raise ConfigFout(f"exportbestand niet gevonden: {pad}") from e
    except (ValueError, OSError, zipfile.BadZipFile) as e:
        raise ConfigFout(f"{pad}: kan export niet lezen ({e})") from e
    raw = raw.dropna(axis=1, how="all")  # rijen blijven staan: kopregel telt originele rijen
    if kopregel is not None:
        if not 1 <= kopregel <= len(raw):
            raise ConfigFout(f"tobias.kopregel: {kopregel} ligt buiten de export")
        kop_i = kopregel - 1
    else:
        kop_i = _vind_kopregel(raw)
    koppen: list[str] = []
    for n, c in enumerate(raw.iloc[kop_i]):
        naam = "" if _leeg(c) else str(c).strip()
        naam = naam or f"kolom_{n + 1}"
        basis, k = naam, 2
        while naam in koppen:
            naam, k = f"{basis}_{k}", k + 1
        koppen.append(naam)
    df = raw.iloc[kop_i + 1 :].copy()
    df.columns = koppen
    # index = 0-based Excel-rijnummer, zodat meldingen naar de echte rij verwijzen
    return df.dropna(how="all")


# --- BAG-id -----------------------------------------------------------------------------------


def normaliseer_bag_id(waarde: object) -> str | None:
    """int/float/str naar 16-cijferige string; None als het geen geldig VBO-id is."""
    if _leeg(waarde):
        return None
    try:
        d = Decimal(str(waarde).strip())
    except InvalidOperation:
        return None
    if not d.is_finite() or d != d.to_integral_value() or d < 0:
        return None
    s = str(int(d)).zfill(16)
    return s if _BAG_VBO.fullmatch(s) else None


# --- mappingvoorstel -----------------------------------------------------------------


def _waarden(df: pd.DataFrame, kol: str) -> list[str]:
    return [s for s in (_schoon(v) for v in df[kol]) if s]


def _aandeel(waarden: list[str], test) -> float:
    return sum(1 for w in waarden if test(w)) / len(waarden) if waarden else 0.0


def _is_huisnummer(w: str) -> bool:
    return w.isdigit() and 1 <= int(w) <= 99999


def stel_mapping_voor(df: pd.DataFrame) -> dict[str, MappingVoorstel]:
    """Stel per canoniek veld een kolom voor; waardepatronen gaan voor kopnamen."""
    voorstel: dict[str, MappingVoorstel] = {}
    gebruikt: set[str] = set()

    def kies(veld, test, drempel, zekerheid, reden):
        beste, beste_a = None, 0.0
        for kol in df.columns:
            if kol in gebruikt:
                continue
            a = _aandeel(_waarden(df, kol), test)
            if a >= drempel and a > beste_a:
                beste, beste_a = kol, a
        if beste is not None:
            gebruikt.add(beste)
            voorstel[veld] = MappingVoorstel(beste, zekerheid, f"{reden} ({beste_a:.0%})")

    kies("bag_vbo_id", lambda w: normaliseer_bag_id(w) is not None, 0.8, "hoog",
         "waarden zijn BAG-VBO-id's")  # fmt: skip
    kies("postcode", lambda w: bool(_POSTCODE.fullmatch(w)), 0.8, "hoog", "waarden zijn postcodes")
    for veld, aliassen in ALIASSEN.items():
        if veld in voorstel:
            continue
        for kol in df.columns:
            if kol not in gebruikt and _norm_kop(kol) in aliassen:
                gebruikt.add(kol)
                voorstel[veld] = MappingVoorstel(kol, "middel", "kopnaam komt overeen")
                break
    if "huisnummer" not in voorstel:
        kies("huisnummer", _is_huisnummer, 0.8, "middel", "waarden zijn gehele getallen 1-99999")
    for veld in ALLE_VELDEN:
        voorstel.setdefault(veld, MappingVoorstel(None, "laag", "geen kolom herkend"))
    return {veld: voorstel[veld] for veld in ALLE_VELDEN}


# --- mapping laden -------------------------------------------------------------------------


def laad_mapping(pad: Path, koppen: list[str] | None = None) -> dict[str, str]:
    """Lees kolommen.yaml; ConfigFout bij ontbrekende verplichte velden."""
    try:
        data = yaml.safe_load(Path(pad).read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigFout(f"{pad}: kan niet lezen ({e}); draai eerst 'tuinscan inspecteer'") from e
    except yaml.YAMLError as e:
        raise ConfigFout(f"{pad}: ongeldige YAML ({e})") from e
    if not isinstance(data, dict):
        raise ConfigFout(f"{pad}: verwacht canoniek_veld: exportkop")
    onbekend = sorted(set(data) - set(ALLE_VELDEN))
    if onbekend:
        raise ConfigFout(
            f"{pad}: onbekend veld {onbekend[0]!r}; toegestaan: {', '.join(ALLE_VELDEN)}"
        )
    mapping = {k: str(v) for k, v in data.items() if not _leeg(v)}
    ontbreekt = [v for v in VERPLICHT if v not in mapping]
    if "adres" not in mapping and not {"straat", "huisnummer"} <= mapping.keys():
        ontbreekt.append("adres (of straat + huisnummer)")
    if ontbreekt:
        msg = f"{pad}: niet gekoppeld: {', '.join(ontbreekt)}"
        if koppen is not None:
            msg += f". Kolommen in de export: {', '.join(koppen)}"
        raise ConfigFout(msg)
    return mapping


# --- VHE's lezen -----------------------------------------------------------------------


def _adres(rij: dict[str, str], mapping: dict[str, str]) -> str:
    if "adres" in mapping and rij.get("adres"):
        return rij["adres"]
    nr = rij.get("huisnummer", "") + rij.get("huisletter", "")
    toev = rij.get("toevoeging", "")
    if toev:
        nr += toev if (len(toev) == 1 and toev.isalpha()) else f"-{toev}"
    return f"{rij.get('straat', '')} {nr}".strip()


def _filter(df: pd.DataFrame, filters: dict[str, list]) -> pd.DataFrame:
    def n(lijst):
        return {str(x).strip().lower() for x in lijst}

    def kol(veld):
        return df[veld].map(lambda w: _schoon(w).lower())

    regels = [
        ("wijk", "wijk", True), ("complex", "complex", True), ("woningtype", "woningtype", True),
        ("eigen_tuin_ja", "eigen_tuin", True), ("status_uitsluiten", "status", False),
        ("sloop_renovatie_uitsluiten", "sloop_renovatie", False),
    ]  # fmt: skip
    for filternaam, veld, insluiten in regels:
        waarden = filters.get(filternaam) or []
        if not waarden:
            continue
        if veld not in df.columns:
            log.warning("filter tobias.filters.%s is gezet maar '%s' is niet gekoppeld; genegeerd",
                        filternaam, veld)  # fmt: skip
            continue
        treft = kol(veld).isin(n(waarden))
        df = df[treft if insluiten else ~treft]
    return df


def _controleer_afronding(bag_ids: list[str], fouten: Foutenlijst) -> None:
    """Eén waarschuwing (log + foutenlijst, VHE '*') als Excel-afronding waarschijnlijk is."""
    lang = [b for b in bag_ids if int(b[:4]) >= 1000]
    nul = sum(b.endswith("0") for b in lang)
    if len(lang) >= AFRONDING_MIN_N and nul / len(lang) > AFRONDING_AANDEEL:
        melding = AFRONDING_TEKST.format(n=nul, totaal=len(lang))
        log.warning(melding)
        fouten.voeg_toe(ALGEMENE_VHE, STAP, melding)


def lees_vhes(cfg: Config, mappen: Mappen, fouten: Foutenlijst) -> pd.DataFrame:
    """Lees de export en pas mapping en filters toe (schrijven: `schrijf_vhes`)."""
    t = cfg.tobias
    ruw = lees_ruwe_export(mappen.invoer / t.bestand, t.blad, t.kopregel)
    koppen = list(ruw.columns)
    mapping = laad_mapping(mappen.invoer / t.kolommen_bestand, koppen)
    ontbrekend = [h for h in mapping.values() if h not in koppen]
    if ontbrekend:
        raise ConfigFout(
            f"{t.kolommen_bestand}: koppen niet in de export: {', '.join(ontbrekend)}. "
            f"Kolommen in de export: {', '.join(koppen)}"
        )
    df = pd.DataFrame({veld: ruw[kop].map(_schoon) for veld, kop in mapping.items()})
    df = _filter(df, t.filters)

    rijen, gezien = [], set()
    for idx, rij in zip(df.index, df.to_dict("records"), strict=True):
        i = int(idx) + 1  # Excel-rijnummer (1-based, incl. koptekst)
        vhe = rij["vhe_nummer"] or f"rij {i}"
        if not rij["vhe_nummer"]:
            fouten.voeg_toe(vhe, STAP, "VHE-nummer ontbreekt")
            continue
        if vhe in gezien:
            fouten.voeg_toe(
                vhe, STAP, f"dubbel VHE-nummer (rij {i} genegeerd, eerste rij behouden)"
            )
            continue
        gezien.add(vhe)
        bag = normaliseer_bag_id(rij["bag_vbo_id"])
        if bag is None:
            reden = f"ongeldig BAG-id {rij['bag_vbo_id']!r}; {BAG_HINT}" if rij["bag_vbo_id"] \
                else "BAG-id ontbreekt"  # fmt: skip
            fouten.voeg_toe(vhe, STAP, reden)
            continue
        rij["bag_vbo_id"] = bag
        rij["adres"] = _adres(rij, mapping)
        rijen.append(rij)

    _controleer_afronding([r["bag_vbo_id"] for r in rijen], fouten)
    kern = ["vhe_nummer", "bag_vbo_id", "adres", "wijk", "complex", "woningtype"]
    opt = [v for v in OPTIONEEL if v in mapping]
    uit = pd.DataFrame(rijen, columns=kern + opt)
    uit = uit.astype(str) if len(uit) else uit.astype({c: "string" for c in uit.columns})
    return uit


def schrijf_vhes(vhes: pd.DataFrame, pad: Path) -> None:
    """Schrijf de ingelezen VHE's (ronde_dir/vhe.parquet; de stap kiest het moment)."""
    vhes.to_parquet(pad, index=False)
