#!/usr/bin/env python3
"""
Averigua el chat_id de Telegram al que mandar las alertas.

Telegram no deja que un bot escriba primero: hace falta que VOS le hables una
vez, y de ese mensaje sale el chat_id. Es un número, no el @usuario, y no hay
forma de deducirlo — por eso este script.

Cómo usarlo:
  1. En Telegram, buscá el bot (@growisupbot) y mandale /start o cualquier cosa.
  2. Corré esto con el token del bot (el de @BotFather):

       TELEGRAM_BOT_TOKEN=123456:AAA... python3 scripts/telegram_chat_id.py

  3. Poné el número que te imprime en TELEGRAM_ALERTA_CHAT_ID del servicio
     `openai`, que es donde corren los monitores:

       railway variables --set "TELEGRAM_ALERTA_CHAT_ID=<numero>" --service openai

Con --probar además te manda un mensaje de prueba a cada chat que encuentra,
para confirmar de una que llega.
"""
import argparse
import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def api(metodo: str, payload=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{metodo}"
    datos = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url, data=datos, method="POST" if datos else "GET",
        headers={"Content-Type": "application/json"} if datos else {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probar", action="store_true",
                   help="mandar un mensaje de prueba a cada chat encontrado")
    args = p.parse_args()

    if not TOKEN:
        sys.exit("falta TELEGRAM_BOT_TOKEN (el token del bot, de @BotFather).\n"
                 "  TELEGRAM_BOT_TOKEN=123456:AAA... python3 scripts/telegram_chat_id.py")

    try:
        yo = api("getMe")
    except Exception as e:
        sys.exit(f"el token no sirve o no hay red: {e}")
    print(f"bot: @{yo.get('result', {}).get('username', '?')}")

    d = api("getUpdates")
    chats = {}
    for u in d.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            quien = chat.get("username") or chat.get("title") or chat.get("first_name") or "?"
            chats[str(chat["id"])] = f"{quien} ({chat.get('type')})"

    if not chats:
        # getUpdates solo devuelve lo NO leído y de los últimos ~2 días: si el
        # /start es viejo, o si hay un webhook configurado que ya se los llevó,
        # no aparece nada aunque el chat exista.
        sys.exit("no encontré ningún chat.\n"
                 "Mandale un mensaje al bot desde Telegram y volvé a correr esto.\n"
                 "(getUpdates solo trae mensajes recientes y sin leer.)")

    print("\nchats encontrados:")
    for cid, quien in chats.items():
        print(f"  {cid:>16}  {quien}")
        if args.probar:
            try:
                api("sendMessage", {"chat_id": cid,
                                    "text": "Prueba de las alertas de Growi. "
                                            "Si te llegó esto, el aviso de sesión de "
                                            "Instagram caída también te va a llegar."})
                print("                    → mensaje de prueba enviado")
            except Exception as e:
                print(f"                    → no pude mandarlo: {e}")

    print("\nPoné el número en el servicio openai:")
    print(f'  railway variables --set "TELEGRAM_ALERTA_CHAT_ID={list(chats)[0]}" --service openai')


if __name__ == "__main__":
    main()
