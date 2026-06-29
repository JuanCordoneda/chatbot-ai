from datetime import datetime
from modules.growi_client import GrowiResult


def generar_informe(post_url: str, comentarios: list[str], resultado: GrowiResult | None, error: str | None = None) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    if error:
        return (
            f"Informe de campaña — {now}\n"
            f"Post: {post_url}\n"
            f"Estado: ERROR\n"
            f"Detalle: {error}"
        )

    lineas_comentarios = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(comentarios))

    msgs_str = ""
    if resultado and resultado.messages:
        msgs_str = "\nMensajes del CRM:\n" + "\n".join(f"  {m}" for m in resultado.messages)

    errores_str = ""
    if resultado and resultado.errors:
        errores_str = "\nErrores:\n" + "\n".join(f"  - {e}" for e in resultado.errors)

    estado = "OK" if (resultado and resultado.success) else "PARCIAL"

    return (
        f"Informe de campaña — {now}\n"
        f"Post: {post_url}\n"
        f"Estado: {estado}\n"
        f"Órdenes insertadas: {resultado.insertadas if resultado else 0}\n"
        f"{msgs_str}"
        f"{errores_str}\n\n"
        f"Comentarios generados ({len(comentarios)}):\n{lineas_comentarios}"
    )
