# Capturas de la guía de onboarding

Las imágenes de `/ayuda` (webService/static/onboarding/paso-N.png y su versión
`-light`) salen de la interfaz REAL del panel, con clientes de demo: `harness.py`
sirve los templates de verdad y falsea las respuestas de `/api/*`, así que no
toca la base, ni el CRM, ni expone datos de un cliente.

Regenerarlas después de cambiar el panel:

    cd scripts/capturas_onboarding
    python3 harness.py 8899 &
    python3 shoot.py
    kill %1

Necesita Google Chrome instalado, `jinja2` y `pillow`. Los datos de demo
(clientes, campañas, productos) están arriba de todo en `harness.py`; qué se
fotografía y con qué tamaño, en `SHOTS` de `shoot.py`.
