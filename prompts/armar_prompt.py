"""Arma el prompt de prompts/toloache-whatsapp.md con la fecha, el calendario y las ofertas vigentes.

Llamalo en cada mensaje, no una vez por conversación: así la hora no queda vieja
y las ofertas vencidas desaparecen solas.
"""

import datetime
import os
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
CERRADO = {0, 6}  # lunes y domingo

# Cada oferta se muestra hasta su fecha de fin inclusive.
OFERTAS = [
    (datetime.date(2026, 12, 18),
     "Midi Toloache, jusqu'au vendredi 18 décembre : le midi du mardi au vendredi (12h–15h), −10 % sur la carte "
     "et −15 % dès 4 personnes, sur place, hors boissons alcoolisées. Il suffit de montrer le message ou de dire "
     "« Midi Toloache »."),
]

PLANTILLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "toloache-whatsapp.md")


def _dia(d: datetime.date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def armar_prompt(ahora: datetime.datetime | None = None) -> str:
    ahora = ahora or datetime.datetime.now(PARIS)
    hoy = ahora.date()
    calendario = ", ".join(
        _dia(d) + (" (fermé)" if d.weekday() in CERRADO else "")
        for d in (hoy + datetime.timedelta(days=i) for i in range(1, 15))
    )
    vigentes = [texto for fin, texto in OFERTAS if hoy <= fin]
    with open(PLANTILLA, encoding="utf-8") as f:
        prompt = f.read()
    return (prompt
            .replace("{{DATE_HEURE}}", f"{_dia(hoy)} {hoy.year}, {ahora:%H}h{ahora:%M}"
                     + (" (fermé aujourd'hui)" if hoy.weekday() in CERRADO else ""))
            .replace("{{CALENDRIER}}", calendario)
            .replace("{{OFFRES}}", "\n".join(vigentes) or "Aucune offre en ce moment."))
