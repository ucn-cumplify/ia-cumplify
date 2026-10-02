# ia-cumplify

Servicio HTTP de inteligencia artificial de Cumplify. Clasifica los artículos de un cuerpo legal y genera embeddings de texto. No reemplaza al backend: lee la misma base PostgreSQL que escribe `backend-cumplify` y expone endpoints para que ese backend los consuma.

## Tecnologías

- Python 3.12
- FastAPI y Uvicorn
- OpenAI (clasificación con el modelo configurado en `OPENAI_MODEL`, embeddings con `OPENAI_EMBEDDING_MODEL`)
- PostgreSQL vía psycopg (misma base que el backend)
- uv

## Relación con los otros repositorios

| Proceso | Puerto | Rol |
|---|---|---|
| PostgreSQL | 5432 | Lo levanta `backend-cumplify` |
| backend-cumplify | 8080 | Crea e hidrata cuerpos legales. Envía textos a embeber |
| frontend-cumplify | 3000 | Habla solo con el backend |
| ia-cumplify | 8000 | Clasifica cuerpos legales y embebe textos |

El backend crea el cuerpo legal y, si viene de la BCN, lo hidrata antes de clasificar. Este servicio no crea ni hidrata normas, y no guarda la clasificación ni los vectores. La persistencia queda en el backend.

`DATABASE_URL` debe apuntar a la misma base, usando `localhost` cuando este proceso corre en el host y Postgres está publicado por Docker:

```text
postgresql://postgres:postgres@localhost:5432/cumplify_db
```

## Configuración

1. Copia el ejemplo:

```bash
cp .env.example .env
```

En PowerShell: `copy .env.example .env`.

2. Completa al menos la clave compartida con el backend, la clave de OpenAI y la base:

```env
SERVICE_API_KEY=un-secreto-largo-y-aleatorio

OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
OPENAI_REASONING_EFFORT=low

DATABASE_URL=postgresql://postgres:postgres@localhost:5432/cumplify_db

INCLUDE_DEV_METRICS=true
CLASSIFY_BATCH_SIZE=25

OPENAI_EMBEDDING_MODEL=text-embedding-3-large
OPENAI_EMBEDDING_DIMENSIONS=1024
EMBEDDINGS_MAX_TEXTS=256
```

`SERVICE_API_KEY` es el secreto que el backend envía en el header `X-API-Key` y tiene que ser igual a su `AI_SERVICE_API_KEY`. Todo `/api/v1` lo exige: sin header o con otro valor responde 401, y si `SERVICE_API_KEY` está vacía responde 503. `/health` queda abierto. Para generar uno: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

`OPENAI_EMBEDDING_DIMENSIONS` tiene que coincidir con la columna `vector(1024)` del backend. Si el request de embeddings no manda `model` ni `dimensions`, se usan estos valores.

`PROFILE_TEXT_MAX_CHARS` (defecto 4.000) es el largo máximo del texto de perfil de una empresa y tiene que coincidir con `AI_PROFILE_TEXT_MAX_CHARS` del backend: un texto más largo recibe 422.

`INCLUDE_DEV_METRICS` solo agrega `dev_metrics`, con el tiempo de respuesta. La versión del clasificador y el uso de tokens viajan siempre, en `classifier_version` y `usage`.

## Ejecución local

Con el Postgres del backend ya disponible:

```bash
uv sync
uv run ia-cumplify
```

El comando levanta Uvicorn con recarga en `http://0.0.0.0:8000`.

- Salud: `GET http://localhost:8000/health`
- OpenAPI: `http://localhost:8000/docs`

## Endpoints

| Método | Ruta | Uso |
|---|---|---|
| GET | `/health` | Responde `{"status": "ok"}`. No exige `X-API-Key` |
| POST | `/api/v1/legal-bodies/classify` | Clasifica los artículos de un cuerpo legal ya hidratado |
| POST | `/api/v1/company-profiles/classify` | Clasifica el texto con que una empresa se describe, en las mismas seis dimensiones; no usa la base |
| POST | `/api/v1/embeddings` | Devuelve un vector por cada texto, en el mismo orden |

El contrato, las reglas y los casos de prueba están en `docs/`.

## Estructura

```text
ia-cumplify/
├── src/ia_cumplify/
│   ├── domain/                  # Artículo, clasificación, perfil de empresa, embedding
│   ├── application/             # Casos de uso y puertos
│   ├── adapters/
│   │   ├── inbound/http/        # FastAPI: routers, esquemas, dependencias
│   │   └── outbound/            # OpenAI y PostgreSQL
│   ├── config/
│   └── cli.py
├── docs/
├── pyproject.toml
├── .env.example
└── README.md
```
