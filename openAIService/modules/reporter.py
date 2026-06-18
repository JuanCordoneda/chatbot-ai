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

    errores_str = ""
    if resultado and resultado.errores:
        errores_str = "\nErrores:\n" + "\n".join(f"  - {e}" for e in resultado.errores)

    estado = "OK" if (resultado and resultado.success) else "PARCIAL"

    return (
        f"Informe de campaña — {now}\n"
        f"Post: {post_url}\n"
        f"Estado: {estado}\n\n"
        f"Comentarios enviados: {resultado.comentarios_enviados if resultado else 0}/{len(comentarios)}\n"
        f"Likes: {resultado.likes if resultado else 0}\n"
        f"Views: {resultado.views if resultado else 0}\n"
        f"Reposts: {resultado.reposts if resultado else 0}\n"
        f"Shares: {resultado.shares if resultado else 0}\n"
        f"{errores_str}\n\n"
        f"Comentarios generados:\n{lineas_comentarios}"
    )
