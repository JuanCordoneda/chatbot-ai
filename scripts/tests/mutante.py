"""Inyecta una regresión a propósito y corre la prueba masiva.
Si la prueba sigue en verde, la prueba no sirve."""
import sys, runpy
sys.path.insert(0, '/app')
import app
MUT = sys.argv[1]
if MUT == "precio":
    app._corregir_costo = lambda o, aid: None            # vuelve a creerle al front
elif MUT == "facu":
    _orig = app.resolver_venta
    def caido(account_id, ig, idventa_elegida=None, refrescar=False):
        r = _orig(account_id, ig, idventa_elegida, refrescar)
        return {**r, "idvendedor": "634", "idventa": "32600"}   # el bug original
    app.resolver_venta = caido
elif MUT == "fecha":
    from datetime import date
    app._fecha_ar_crm = lambda aid=None: "1999-01-01"     # fecha equivocada
elif MUT == "programada":
    app._ordenes.ahora_ar_texto = lambda: ""              # programada sin fecha
print(f"--- MUTACIÓN INYECTADA: {MUT} ---")
runpy.run_path('/tmp/test_masivo.py', run_name='__main__')
