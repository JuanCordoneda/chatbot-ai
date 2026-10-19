"""Base de datos del prospector: prospectos, sus mensajes y los ajustes del bot.

SQLite en un solo archivo (DB_PATH). En Railway hay que montar un volumen en la
carpeta de DB_PATH para que los datos sobrevivan a cada deploy.

Cada prospecto se identifica por su `clave`: los últimos 10 dígitos del
teléfono (código de área + número). Así un mismo número escrito como
5491155550101, 1155550101 o 011 15 5555 0101 cae siempre en el mismo prospecto.
"""

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "prospector.db"))

ESTADOS = ("nuevo", "contactado", "en_charla", "calificado", "descartado", "error")
CAMPOS_FICHA = ("flota", "unidades", "marcas", "quien_compra", "contacto_compras", "propuesta", "zona")

DEFAULT_AJUSTES = {
    "oferta": "vendemos filtros para camiones y colectivos: gasoil, aceite y aire",
    "zona": "Boedo, CABA y GBA",
    "minimo": 5,
}

_lock = threading.Lock()


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS prospectos (
                clave TEXT PRIMARY KEY,
                telefono TEXT NOT NULL,
                nombre TEXT DEFAULT '',
                empresa TEXT DEFAULT '',
                estado TEXT DEFAULT 'nuevo',
                origen TEXT DEFAULT 'lista',
                ficha TEXT DEFAULT '{}',
                bot_activo INTEGER DEFAULT 1,
                atencion TEXT DEFAULT '',
                ultimo_error TEXT DEFAULT '',
                creado TEXT,
                actualizado TEXT
            );
            CREATE TABLE IF NOT EXISTS mensajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clave TEXT NOT NULL,
                de TEXT NOT NULL,          -- 'bot' | 'cliente' | 'humano'
                texto TEXT NOT NULL,
                ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mensajes_clave ON mensajes(clave, id);
            CREATE TABLE IF NOT EXISTS ajustes (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            """
        )


# ---------------------------------------------------------------------------
# Teléfonos
# ---------------------------------------------------------------------------

def normalizar_telefono(tel: str) -> str:
    """Devuelve el número en formato WhatsApp para Argentina (549 + área + número).

    Acepta 11 5555 0101, 011 15 5555 0101, +54 9 11 5555 0101, etc. Si el número
    ya viene con código de país 54 se respeta tal cual.
    """
    d = re.sub(r"\D", "", tel or "")
    if not d:
        return ""
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("54"):
        return d
    if d.startswith("0"):
        d = d[1:]
    # Celular con 15 escrito a la argentina: área (2 a 4 dígitos) + 15 + número.
    if len(d) == 12:
        for largo_area in (2, 3, 4):
            if d[largo_area:largo_area + 2] == "15":
                d = d[:largo_area] + d[largo_area + 2:]
                break
    return "549" + d


def clave_de(tel: str) -> str:
    d = re.sub(r"\D", "", tel or "")
    return d[-10:]


# ---------------------------------------------------------------------------
# Prospectos
# ---------------------------------------------------------------------------

def _fila(row) -> dict | None:
    if row is None:
        return None
    p = dict(row)
    p["ficha"] = json.loads(p.get("ficha") or "{}")
    p["bot_activo"] = bool(p["bot_activo"])
    return p


def obtener(clave: str) -> dict | None:
    with _conn() as c:
        return _fila(c.execute("SELECT * FROM prospectos WHERE clave = ?", (clave,)).fetchone())


def obtener_o_crear(telefono: str) -> dict:
    """Prospecto de este teléfono; si no existe lo crea como contacto entrante."""
    clave = clave_de(telefono)
    p = obtener(clave)
    if p:
        return p
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO prospectos (clave, telefono, estado, origen, creado, actualizado) "
            "VALUES (?, ?, 'en_charla', 'entrante', ?, ?)",
            (clave, telefono, ahora(), ahora()),
        )
    return obtener(clave)


def cargar_lista(texto: str) -> dict:
    """Carga prospectos pegados como texto: una línea por contacto.

    Cada línea tiene nombre, empresa y teléfono separados por coma, punto y coma
    o tabulación (así sirve pegar columnas de Excel o Google Sheets). El teléfono
    se detecta solo, en cualquier columna.
    """
    nuevos, repetidos, invalidos = 0, 0, []
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        partes = [x.strip() for x in re.split(r"\t|;|,", linea) if x.strip()]
        idx = next((i for i, x in enumerate(partes) if len(re.sub(r"\D", "", x)) >= 8), None)
        if idx is None:
            # La fila de encabezado ("Nombre, Empresa, Teléfono") no cuenta como error.
            if not re.search(r"tel[eé]fono|celular|whatsapp", linea, re.I):
                invalidos.append(linea)
            continue
        tel = normalizar_telefono(partes.pop(idx))
        nombre = partes[0] if partes else ""
        empresa = partes[1] if len(partes) > 1 else ""
        clave = clave_de(tel)
        with _lock, _conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO prospectos (clave, telefono, nombre, empresa, estado, origen, creado, actualizado) "
                "VALUES (?, ?, ?, ?, 'nuevo', 'lista', ?, ?)",
                (clave, tel, nombre, empresa, ahora(), ahora()),
            )
        if cur.rowcount:
            nuevos += 1
        else:
            repetidos += 1
    return {"nuevos": nuevos, "repetidos": repetidos, "invalidos": invalidos}


def listar() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """
            SELECT p.*,
                   (SELECT texto FROM mensajes m WHERE m.clave = p.clave ORDER BY id DESC LIMIT 1) AS ultimo_texto,
                   (SELECT de    FROM mensajes m WHERE m.clave = p.clave ORDER BY id DESC LIMIT 1) AS ultimo_de,
                   (SELECT ts    FROM mensajes m WHERE m.clave = p.clave ORDER BY id DESC LIMIT 1) AS ultimo_ts,
                   (SELECT COUNT(*) FROM mensajes m WHERE m.clave = p.clave) AS total_mensajes
            FROM prospectos p
            ORDER BY COALESCE(ultimo_ts, p.actualizado) DESC
            """
        ).fetchall()
    return [_fila(r) for r in rows]


def actualizar(clave: str, **campos):
    permitidos = {"nombre", "empresa", "estado", "bot_activo", "atencion", "ultimo_error", "telefono"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    if "estado" in campos and campos["estado"] not in ESTADOS:
        raise ValueError(f"Estado inválido: {campos['estado']}")
    if "bot_activo" in campos:
        campos["bot_activo"] = 1 if campos["bot_activo"] else 0
    if not campos:
        return
    campos["actualizado"] = ahora()
    sets = ", ".join(f"{k} = ?" for k in campos)
    with _lock, _conn() as c:
        c.execute(f"UPDATE prospectos SET {sets} WHERE clave = ?", (*campos.values(), clave))


def actualizar_ficha(clave: str, datos: dict) -> dict:
    """Mezcla en la ficha los datos no vacíos y devuelve la ficha resultante."""
    p = obtener(clave)
    if not p:
        return {}
    ficha = dict(p["ficha"])
    for k in CAMPOS_FICHA:
        v = datos.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if k == "unidades":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
        elif isinstance(v, str):
            v = v.strip()
            if k == "propuesta":
                v = v.lower()
                if v not in ("si", "no"):
                    continue
        ficha[k] = v
    with _lock, _conn() as c:
        c.execute(
            "UPDATE prospectos SET ficha = ?, actualizado = ? WHERE clave = ?",
            (json.dumps(ficha, ensure_ascii=False), ahora(), clave),
        )
    return ficha


def borrar(clave: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM mensajes WHERE clave = ?", (clave,))
        c.execute("DELETE FROM prospectos WHERE clave = ?", (clave,))


# ---------------------------------------------------------------------------
# Mensajes
# ---------------------------------------------------------------------------

def agregar_mensaje(clave: str, de: str, texto: str):
    with _lock, _conn() as c:
        c.execute("INSERT INTO mensajes (clave, de, texto, ts) VALUES (?, ?, ?, ?)", (clave, de, texto, ahora()))
        c.execute("UPDATE prospectos SET actualizado = ? WHERE clave = ?", (ahora(), clave))


def mensajes(clave: str, limite: int = 500) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT de, texto, ts FROM (SELECT * FROM mensajes WHERE clave = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (clave, limite),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Ajustes
# ---------------------------------------------------------------------------

def ajustes() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT clave, valor FROM ajustes").fetchall()
    out = dict(DEFAULT_AJUSTES)
    out.update({r["clave"]: json.loads(r["valor"]) for r in rows})
    return out


def guardar_ajustes(nuevos: dict) -> dict:
    with _lock, _conn() as c:
        for k in DEFAULT_AJUSTES:
            if k in nuevos:
                v = nuevos[k]
                if k == "minimo":
                    v = max(1, int(v))
                else:
                    v = str(v).strip()
                    if not v:
                        continue
                c.execute(
                    "INSERT INTO ajustes (clave, valor) VALUES (?, ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                    (k, json.dumps(v, ensure_ascii=False)),
                )
    return ajustes()
