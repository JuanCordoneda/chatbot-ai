"""Detecta la palabra clave que pide un post ("comment CLAUDE to get the PDF").

Es una SUGERENCIA para precargar el campo del vendedor, no una decisión: el
front la muestra editable y él confirma antes de generar. Por eso el criterio es
conservador — preferimos no sugerir nada antes que sugerir mal: 40 comentarios
con la palabra equivocada no se notan hasta que ya están publicados.

Solo sugerimos cuando la palabra viene MARCADA en el caption (entre comillas o
en MAYÚSCULAS después de un verbo tipo "comment"). Un "comment below" o un
"comentá lo que pienses" no devuelven nada.
"""
import re
import unicodedata

# Verbos con los que un creador pide el comentario, en inglés y castellano.
_TRIGGERS = (
    r"comment(?:ed|ing)?|comenta|comentá|comentar|"
    r"type|write|escribe|escribí|escribir|"
    r"drop|deja|dejá|dejar|put|pon|poné|poner|say|di|decí|"
    r"dm|manda|mandá|mandame|send"
)

# Relleno entre el verbo y la palabra: "comment THE WORD claude", "comentá LA
# PALABRA claude", "dm ME claude". Se saltea sin consumir la palabra.
_FILLER = (
    r"(?:\s+(?:the|a|an|el|la|los|las|un|una|"
    r"word|words|palabra|palabras|"
    r"me|us|nos|mi|conmigo|"
    r"below|down|now|here|abajo|debajo|acá|aca|aquí|aqui|ahora|ya|"
    r"exactly|exacto|solo|sólo|just|only))*"
)

# Palabras que NUNCA son la keyword: son parte de la instrucción, no lo que hay
# que comentar. Sin esto, "COMMENT BELOW TO GET IT" sugería "BELOW TO GET IT".
_NO_SON_KEYWORD = {
    "below", "down", "now", "here", "me", "us", "you", "your", "this", "that",
    "the", "and", "or", "to", "for", "if", "it", "im", "i", "yes", "no", "ok",
    "please", "pls", "link", "dm", "dms", "comment", "comments", "word", "words",
    "abajo", "debajo", "aca", "aqui", "ahora", "ya", "si", "no", "porfa", "por",
    "favor", "la", "el", "los", "las", "un", "una", "y", "o", "para", "que",
    "esto", "eso", "te", "lo", "mando", "palabra", "comenta", "comentario",
}

# Máximo de palabras que puede tener la keyword ("OPEN ART", "FREE GUIDE").
_MAX_PALABRAS = 2

# Comillas de todos los sabores que usa la gente en Instagram.
_COMILLAS = {"\"": "\"", "'": "'", "“": "”", "‘": "’",
             "«": "»"}


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _descartable(token: str) -> bool:
    return _sin_tildes(token).lower() in _NO_SON_KEYWORD


def _es_mayuscula(token: str) -> bool:
    """MAYÚSCULAS de verdad: al menos 2 caracteres y ninguna minúscula. Deja
    pasar números y símbolos (ej "AI2", "OPEN-ART")."""
    letras = [c for c in token if c.isalpha()]
    return len(token) >= 2 and bool(letras) and all(c.isupper() for c in letras)


def _limpiar(token: str) -> str:
    """Saca la puntuación de los bordes, no la de adentro (OPEN-ART se conserva)."""
    return token.strip(" \t\n.,;:!?¡¿()[]{}…\"'“”‘’«»")


def _desde_comillas(resto: str) -> str:
    """La palabra entre comillas justo después del verbo: comment "CLAUDE"."""
    resto = resto.lstrip()
    if not resto:
        return ""
    cierre = _COMILLAS.get(resto[0])
    if not cierre:
        return ""
    fin = resto.find(cierre, 1)
    if fin <= 1:
        return ""
    candidato = _limpiar(resto[1:fin])
    palabras = candidato.split()
    if not palabras or len(palabras) > _MAX_PALABRAS:
        return ""
    if any(_descartable(p) for p in palabras):
        return ""
    return candidato


def _desde_mayusculas(resto: str) -> str:
    """La racha de MAYÚSCULAS justo después del verbo: comment CLAUDE below.
    Corta en la primera palabra que no sea mayúscula o que sea instrucción."""
    palabras = []
    for bruto in resto.split()[:_MAX_PALABRAS + 2]:
        token = _limpiar(bruto)
        if not token or not _es_mayuscula(token) or _descartable(token):
            break
        palabras.append(token)
        if len(palabras) == _MAX_PALABRAS:
            break
    return " ".join(palabras)


def detectar_keyword(caption: str) -> str:
    """La palabra clave que pide el caption, o "" si no hay una clara.

    Devuelve la palabra tal cual está escrita en el post: el modo keyword ya se
    encarga de variar mayúsculas y minúsculas."""
    if not caption or not caption.strip():
        return ""
    # El caption viene en una sola línea o en varias: los saltos son separadores
    # como cualquier espacio.
    texto = re.sub(r"\s+", " ", caption)

    patron = re.compile(rf"\b(?:{_TRIGGERS}){_FILLER}\b", re.IGNORECASE)
    for m in patron.finditer(texto):
        resto = texto[m.end():]
        # Las comillas mandan sobre las mayúsculas: si el creador se tomó el
        # trabajo de entrecomillar, eso es la palabra.
        candidato = _desde_comillas(resto) or _desde_mayusculas(resto)
        if candidato:
            return candidato
    return ""
