# API — Módulo: Clasificación de cuerpos legales

> Base path: `/api/v1/legal-bodies`

Exige el header `X-API-Key` con el valor de `SERVICE_API_KEY`. Comparte la base PostgreSQL de `backend-cumplify` mediante `DATABASE_URL`.

---

## Clasificar un cuerpo legal

```text
POST /api/v1/legal-bodies/classify
```

**Descripción:** Lee el cuerpo legal y sus artículos, descarta encabezado, promulgación y títulos, y devuelve las seis dimensiones de cada artículo clasificable. No escribe en la base.

**Requerimiento relacionado:** CLS-001, CLS-002, CLS-003, CLS-004, CLS-005

**Request:**

```json
{
  "legal_body_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "candidate_values": {
    "scope": ["Medio Ambiente", "Laboral"],
    "productive_sector": ["Minería", "Energía"],
    "territorial_coverage": ["Nacional", "Región de Antofagasta"]
  }
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| legal_body_id | UUID | Sí | Id de la fila en `legal_bodies` |
| candidate_values | objeto | No | Etiquetas ya usadas en otros cuerpos, para que el modelo las reutilice |
| candidate_values.scope | lista de string | No | Máximo 60. Defecto: lista vacía |
| candidate_values.productive_sector | lista de string | No | Máximo 60. Defecto: lista vacía |
| candidate_values.territorial_coverage | lista de string | No | Máximo 60. Defecto: lista vacía |

Cada etiqueta se recorta, debe tener entre 1 y 100 caracteres, y no puede contener `|` ni saltos de línea. Campos extra dentro de `candidate_values` se rechazan. Los duplicados se eliminan conservando el orden.

**Response `200 OK`:**

```json
{
  "legal_body_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "title": "Ley 16744",
  "results": [
    {
      "article_id": "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c",
      "number": "Artículo 1",
      "classification": {
        "scope": ["Seguridad y Salud Ocupacional"],
        "productive_sector": ["No especificado"],
        "territorial_coverage": ["Nacional"],
        "activity_action": ["Protección de la vida y salud de los trabajadores"],
        "facility_installation_equipment": ["No especificado"],
        "others": ["No especificado"]
      }
    }
  ],
  "classifier_version": "classify-v2@gpt-5.6-luna",
  "usage": {
    "prompt_tokens": 1800,
    "completion_tokens": 400,
    "total_tokens": 2200,
    "cached_tokens": 0,
    "llm_calls": 1
  },
  "failed_article_ids": [],
  "dev_metrics": {
    "elapsed_ms": 4200.5,
    "prompt_tokens": 1800,
    "completion_tokens": 400,
    "total_tokens": 2200,
    "llm_calls": 1
  }
}
```

`classifier_version` y `usage` viajan siempre:

- `classifier_version` es `<versión del prompt>@<modelo>`. La versión del prompt es `PROMPT_VERSION` y sube con cada cambio del prompt; el modelo es `OPENAI_MODEL`. El backend la guarda con cada respuesta para encontrar los cuerpos clasificados con un prompt o un modelo anterior.
- `usage` suma los tokens de los lotes que respondieron, y también los de un lote fallido cuyo uso informó el proveedor: una negativa o una respuesta cortada por largo o por el filtro de contenido. Un lote fallido sin una respuesta legible (error de conexión, timeout, error HTTP, o una salida que no cumple el esquema o no es JSON) suma 1 en `llm_calls`, pero sus tokens no se conocen y no se cuentan, aunque el proveedor los haya cobrado. Sin artículos clasificables todo vale 0.
- `cached_tokens` es la parte de `prompt_tokens` que el proveedor sirvió desde su caché (`usage.prompt_tokens_details.cached_tokens`, como en el chat), o 0 si no la informa. Como cada lote repite el cuerpo completo, mide cuánto de esa repetición se cobra a precio de caché. El backend no lo lee, pero lo conserva en la respuesta cruda que guarda.

`dev_metrics` solo trae datos cuando `INCLUDE_DEV_METRICS` es verdadero: agrega el tiempo y repite `prompt_tokens`, `completion_tokens`, `total_tokens` y `llm_calls` de `usage` (no `cached_tokens`). Si no, viene en `null`. Cada lista de `classification` tiene al menos un elemento.

**Reglas:**

- Se clasifica todo artículo cuyo `number` no sea `encabezado` ni `promulgación`, y que no empiece por `título`, comparando en minúsculas y sin espacios al borde.
- Esas piezas igual se envían al modelo como contexto del cuerpo completo.
- Sin artículos clasificables la respuesta trae `results: []` y no llama al modelo.
- Más de `CLASSIFY_BATCH_SIZE` artículos (defecto 25) generan varias llamadas, en serie. El cuerpo completo va en cada una: no se acota por ahora y se mide con `cached_tokens` (decisión del 2026-10-08, ver la deuda técnica de `requirements.md`). Si un lote falla, los demás se conservan: `results` trae lo clasificado y `failed_article_ids` los artículos de los lotes fallidos o omitidos. Una salida del modelo que no cumple el esquema o no es JSON también hace fallar solo su lote. Solo si no se clasifica ningún artículo la petición es 502.
- Cada llamada HTTP a OpenAI usa `OPENAI_TIMEOUT_SECONDS` (defecto 180) y `OPENAI_MAX_RETRIES` (defecto 2). El SDK reintenta 408, 409, 429, 5xx, timeouts y errores de conexión.
- Antes de armar el prompt se quitan del resumen y del texto de los artículos los data URI embebidos (`data:<tipo>/<subtipo>...`), en una imagen Markdown, en un `<img>` o sueltos en base64. Una imagen se reemplaza por `[imagen omitida]`, y cualquier otro tipo, como un PDF que la BCN adjunta a un artículo, por `[archivo omitido]`. La base no se modifica.
- Un `number` o una `section` en null se leen como texto vacío: el artículo se clasifica y su `number` vuelve vacío.
- Las dimensiones 1 a 4 y 6 salen del texto. Si no hay respaldo, el valor es `No especificado`. `facility_installation_equipment` puede inferir una instalación típica; si no puede, también usa `No especificado`.
- Si hay candidatos y uno encaja, se copia tal cual. `activity_action`, `facility_installation_equipment` y `others` no tienen candidatos.
- El orden de `results` sigue `articles.order`, no el orden en que respondió el modelo.
- Ids de más que devuelva el modelo se ignoran. Si falta el id de un artículo pedido, ese id va a `failed_article_ids` y el resto del lote se conserva.

**Errores:**

| Código | Cuándo |
|---|---|
| 401 | Falta `X-API-Key` o no coincide con `SERVICE_API_KEY` |
| 404 | No existe la fila en `legal_bodies` |
| 422 | `legal_body_id` no es UUID, o `candidate_values` no cumple las reglas de etiqueta. Solo por el pedido: una falla del modelo nunca es 422 |
| 502 | Ningún artículo se pudo clasificar: todos los lotes fallaron (error del proveedor, timeout, negativa, respuesta cortada, o una salida que no cumple el esquema o no es JSON). El detalle dice cuántas llamadas y tokens se contaron. También ante una falla inesperada del servicio. El backend lo trata como transitorio |
| 503 | Falta `SERVICE_API_KEY`, `OPENAI_API_KEY` o `DATABASE_URL` |

**Limitaciones:**

- No persiste la clasificación.
- No comprueba si el cuerpo fue hidratado.
- No filtra por empresa.
