# ia-cumplify

Servicio HTTP de inteligencia artificial de Cumplify. Clasifica los artículos de un cuerpo legal y el texto con que una empresa se describe, genera embeddings de texto y responde las preguntas del chat normativo en streaming, citando los pasajes que le envía el backend. No reemplaza al backend: lee la misma base PostgreSQL que escribe `backend-cumplify` y expone endpoints para que ese backend los consuma.

## Tecnologías

- Python 3.12
- FastAPI (0.140.13 o posterior, por los Server-Sent Events de `fastapi.sse`) y Uvicorn
- OpenAI (clasificación con el modelo configurado en `OPENAI_MODEL`, chat con `CHAT_MODEL` o, si está vacía, `OPENAI_MODEL`, y embeddings con `OPENAI_EMBEDDING_MODEL`)
- PostgreSQL vía psycopg (misma base que el backend)
- uv
- pytest, en el grupo de desarrollo `dev`

## Relación con los otros repositorios

| Proceso | Puerto | Rol |
|---|---|---|
| PostgreSQL | 5432 | Lo levanta `backend-cumplify` |
| backend-cumplify | 8080 | Crea e hidrata cuerpos legales. Envía textos a embeber |
| frontend-cumplify | 3000 | Habla solo con el backend |
| ia-cumplify | 8000 | Clasifica cuerpos legales y perfiles de empresa, embebe textos y responde el chat |

El backend crea el cuerpo legal y, si viene de la BCN, lo hidrata antes de clasificar. Este servicio no crea ni hidrata normas, y no guarda la clasificación ni los vectores. La persistencia queda en el backend.

El chat tampoco lee la base ni guarda estado: cada pedido trae la pregunta, los mensajes recientes y los pasajes que el backend recuperó para ese turno. Recuperar los pasajes, guardar la conversación y reenviar el stream al navegador le corresponde al backend (tareas 4.5, 4.4 y 4.7 del plan del equipo).

`DATABASE_URL` debe apuntar a la misma base, usando `localhost` cuando este proceso se ejecuta en el host y Postgres está publicado por Docker:

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

`INCLUDE_DEV_METRICS` solo agrega `dev_metrics`, con el tiempo de respuesta; en el chat, `elapsed_ms` y `first_delta_ms` en el evento `done`. En la clasificación y el perfil, la versión del clasificador y el uso de tokens viajan siempre, en `classifier_version` y `usage`. En el chat, la versión va en `chat_version`, y `usage` vale `null` si el proveedor no llegó a informarlo. En los motivos de aplicabilidad viajan `reason_version` y `usage`.

`APPLICABILITY_REASONS_TIMEOUT_SECONDS` (25) y `APPLICABILITY_REASONS_MAX_RETRIES` (0) son del `POST /api/v1/applicability-reasons`. Quedan por debajo de los 30 s que espera el backend (`AI_APPLICABILITY_REASONS_TIMEOUT_SECONDS`), para que un modelo lento falle aquí y el backend deje la plantilla.

### Chat

El chat (`POST /api/v1/chat`) tiene su propia configuración: no usa `OPENAI_TIMEOUT_SECONDS` (180) ni `OPENAI_MAX_RETRIES` (2), que en el peor caso suman minutos sin respuesta. Todas las variables son opcionales; los valores por defecto son los del contrato (`docs/Chat/api.md`).

| Variable | Defecto | Uso | Espejo en el backend |
|---|---|---|---|
| `CHAT_MODEL` | vacía (usa `OPENAI_MODEL`) | Modelo del chat | |
| `CHAT_REASONING_EFFORT` | vacía (usa `OPENAI_REASONING_EFFORT`) | Esfuerzo de razonamiento del chat | |
| `CHAT_MAX_COMPLETION_TOKENS` | 4.000 | Tope de salida por respuesta, razonamiento incluido | |
| `CHAT_TIMEOUT_SECONDS` | 60 | Lectura, por intento: lo máximo que se esperan las cabeceras de OpenAI o su siguiente fragmento. La conexión tiene 5 s | |
| `CHAT_MAX_RETRIES` | 0 | Reintentos del SDK, solo antes de que OpenAI responda 2xx. Con 0, el backend decide si reintenta | |
| `CHAT_QUESTION_MAX_CHARS` | 2.000 | Largo máximo de la pregunta, recortada | `AI_CHAT_QUESTION_MAX_CHARS` |
| `CHAT_HISTORY_MAX_MESSAGES` | 6 | Mensajes máximos del historial, sin contar los vacíos | `AI_CHAT_HISTORY_MAX_MESSAGES` |
| `CHAT_HISTORY_MAX_CHARS` | 8.000 | Caracteres máximos del historial, sumados | `AI_CHAT_HISTORY_MAX_CHARS` |
| `CHAT_MAX_PASSAGES` | 12 | Pasajes máximos por pedido | `AI_CHAT_MAX_PASSAGES` |
| `CHAT_PASSAGE_MAX_CHARS` | 6.000 | Largo máximo del texto de un pasaje, recortado | `AI_CHAT_PASSAGE_MAX_CHARS` |
| `CHAT_PASSAGES_MAX_TOTAL_CHARS` | 48.000 | Suma máxima de los textos de los pasajes | `AI_CHAT_PASSAGES_MAX_TOTAL_CHARS` |
| `CHAT_FAKE_RESPONDER` | `false` | Respondedor falso: un texto fijo que cita el primer pasaje, sin llamar a OpenAI ni exigir `OPENAI_API_KEY`, con `chat_version` `fake-v1@fake`. Solo para integrar sin gastar tokens; si arranca activo, el log lo avisa | |

- Los topes se espejan en el backend, como `PROFILE_TEXT_MAX_CHARS` con `AI_PROFILE_TEXT_MAX_CHARS`: si el backend sube un tope y este servicio no, un pedido válido para el backend recibe 422. Los nombres `AI_CHAT_*` son una propuesta: el backend todavía no los define.
- Con el respondedor real y sin `OPENAI_API_KEY`, el chat responde 503 antes de validar el esquema y los largos, aunque el pedido venga sin pasajes.

## Ejecución local

Con el Postgres del backend ya disponible:

```bash
uv sync
uv run ia-cumplify
```

El comando levanta Uvicorn con recarga en `http://0.0.0.0:8000`. El log del servicio sale por stderr en nivel INFO, con el formato de Uvicorn; un lanzamiento directo con `uvicorn ia_cumplify.adapters.inbound.http.app:app` no pasa por `cli.py` y necesita un `--log-config` equivalente.

- Salud: `GET http://localhost:8000/health`
- OpenAPI: `http://localhost:8000/docs`

## Pruebas

Las pruebas usan pytest y httpx2, del grupo de desarrollo `dev`. `uv sync` ya instala ese grupo, salvo con `--no-dev`; para pedirlo de forma explícita:

```bash
uv sync --group dev
uv run pytest
```

- Ninguna prueba lee `.env` ni llama a OpenAI. Usan un respondedor falso o un OpenAI simulado en 127.0.0.1, y `tests/conftest.py` borra las variables de `Settings` antes de cada prueba. Tampoco hace falta Postgres.
- Las pruebas de desconexión del chat (marcador `disconnect`) levantan uvicorn y el OpenAI simulado como procesos propios en 127.0.0.1. Para omitirlas: `uv run pytest -m "not disconnect"`.
- Los casos de prueba de cada módulo están en `docs/<Módulo>/test.csv`; los que cubre una prueba automática la nombran en `Resultado Obtenido`.

### Set de evaluación del chat

`scripts/chat_eval.py` ejecuta a mano el set fijo de evaluación del prompt del chat (`scripts/chat_eval_set.json`, 26 preguntas) y cubre CHT-041 a CHT-046 de `docs/Chat/test.csv`. No es parte de pytest, que solo recoge `tests/`, y **gasta tokens reales**: le pide cada respuesta a un ia-cumplify ya en marcha, que llama a OpenAI. Una ejecución completa costó de 50.000 a 53.000 tokens con `chat-v2` y `chat-v3` ("Costo por pregunta" en `docs/Chat/requirements.md`), y el script informa el gasto real.

```bash
export SERVICE_API_KEY=...        # la misma del servicio
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/cumplify_db
uv run python scripts/chat_eval.py --base-url http://127.0.0.1:8000
```

- Saca los pasajes de la base local con una conexión propia de solo lectura. No lee `.env`: las variables van exportadas o como argumentos (`--api-key`, `--database-url`).
- Por consola muestra solo métricas: tokens (incluidos `cached_tokens` y `reasoning_tokens`), latencia del primer `delta` y total, cobertura y citas descartadas por caso, y los casos cuya cobertura no es la esperada. Las respuestas completas y los pasajes, que traen datos de la empresa, van a `--out` (por defecto, un archivo del directorio temporal), siempre fuera del repositorio.
- `--case E01` ejecuta solo ese caso (se puede repetir). `--repeat-case E01` repite ese pedido idéntico al final y compara su `cached_tokens` con el límite de "Privacidad" de `docs/Chat/api.md`; solo sirve si se acuerda un modo explícito de caché.
- Para probar el script sin gastar tokens, el servicio va con `CHAT_FAKE_RESPONDER=true` y sin `OPENAI_API_KEY`; sin base, `--without-db` usa pasajes simulados. El informe marca la ejecución como simulada (`chat_version` `fake-v1@fake`, `usage` en cero).

### Evaluación de recuperación de los embeddings

`scripts/retrieval_eval.py` compara a mano las recetas de embeddings de artículos que `backend-cumplify` guarda en `ai_embeddings` (por defecto `text-embedding-3-large@1024#art-v1` y `#art-v2`) con un conjunto fijo de preguntas (`scripts/retrieval_eval_set.json`), y mide recall@k y MRR por receta y por tipo de pregunta. Es el script del caso AI-044 del backend y cubre EMB-007 de `docs/Embeddings/test.csv`. No es parte de pytest y gasta pocos tokens: solo los de las preguntas, que embebe un ia-cumplify ya en marcha; los vectores de los artículos ya están guardados.

```bash
export SERVICE_API_KEY=...        # la misma del servicio
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/cumplify_db
uv run python scripts/retrieval_eval.py --base-url http://127.0.0.1:8000
```

- Lee la base local con una conexión propia de solo lectura. No lee `.env`: las variables van exportadas o como argumentos (`--api-key`, `--database-url`).
- Por consola muestra solo métricas y los tokens que informa el endpoint. El detalle por pregunta va a `--out` (por defecto, un archivo del directorio temporal), siempre fuera del repositorio.
- Qué mide, el formato del conjunto y las opciones están en "Evaluación de recuperación" de `docs/Embeddings/requirements.md`. `--self-test` comprueba el cálculo de las métricas con datos inventados, sin servicio ni base.

## Endpoints

| Método | Ruta | Uso |
|---|---|---|
| GET | `/health` | Responde `{"status": "ok"}`. No exige `X-API-Key` |
| POST | `/api/v1/legal-bodies/classify` | Clasifica los artículos de un cuerpo legal ya hidratado |
| POST | `/api/v1/applicability-reasons` | Un motivo en español por artículo sugerido; lee la norma y solo esos artículos; nunca el texto del perfil |
| POST | `/api/v1/company-profiles/classify` | Clasifica el texto con que una empresa se describe, en las mismas seis dimensiones; no usa la base |
| POST | `/api/v1/embeddings` | Devuelve un vector por cada texto, en el mismo orden |
| POST | `/api/v1/chat` | Responde una pregunta del chat en streaming (Server-Sent Events), solo con los pasajes del pedido y citando cada afirmación; no usa la base |

El contrato, las reglas y los casos de prueba están en `docs/`.

## Estructura

```text
ia-cumplify/
├── src/ia_cumplify/
│   ├── domain/                  # Artículo, clasificación, perfil, motivo de aplicabilidad, embedding, chat
│   ├── application/             # Casos de uso y puertos
│   ├── adapters/
│   │   ├── inbound/http/        # FastAPI: routers, esquemas, dependencias
│   │   └── outbound/            # OpenAI, PostgreSQL y el respondedor falso del chat
│   ├── config/
│   └── cli.py
├── tests/                       # pytest, con un OpenAI simulado en 127.0.0.1
├── scripts/                     # Evaluaciones manuales con tokens reales: el chat y la recuperación de embeddings
├── docs/
├── pyproject.toml
├── .env.example
└── README.md
```
