# Capturas de la guía de onboarding

Las imágenes de `/ayuda` (`paso-N.png`) y de `/ayuda-ordenes` (`orden-N.png`),
con su versión `-light`, en webService/static/onboarding/, salen de la interfaz REAL del panel, con clientes de demo: `harness.py`
sirve los templates de verdad y falsea las respuestas de `/api/*`, así que no
toca la base, ni el CRM, ni expone datos de un cliente.

Regenerarlas después de cambiar el panel:

    cd scripts/capturas_onboarding
    python3 harness.py 8899 &
    python3 shoot.py
    kill %1

Necesita Google Chrome instalado, `jinja2` y `pillow`. Los datos de demo (clientes, campañas, productos, y el post con sus comentarios
para el flujo de órdenes) están arriba de todo en `harness.py`, junto con
`ANOTACIONES` — los aros y carteles que se dibujan encima de cada captura. Qué se
fotografía y con qué tamaño, en `SHOTS` de `shoot.py`.
