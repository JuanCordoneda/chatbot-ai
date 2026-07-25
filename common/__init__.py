"""
Capa de datos compartida entre webService y openAIService.

Introducida en la etapa 2 (multi-tenant). Todo el modelo que antes vivía en
archivos (clients_map.json, prompts/*.txt) y en el .env (credenciales del CRM)
pasa a Postgres, pero la lectura mantiene fallback a los archivos para no romper
el flujo actual de Facu si la DB no está disponible.
"""
