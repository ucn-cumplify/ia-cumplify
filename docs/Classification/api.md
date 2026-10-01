# API — Módulo: Clasificación de cuerpos legales

> Base path: `/api/v1/legal-bodies`

El servicio no exige `Authorization`. Comparte la base PostgreSQL de `backend-cumplify` mediante `DATABASE_URL`.

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
    "llm_calls": 1
  },
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
- `usage` suma los tokens de todas las llamadas al modelo para este cuerpo. `llm_calls` cuenta las llamadas, una por lote. Sin artículos clasificables todo vale 0.

`dev_metrics` solo trae datos cuando `INCLUDE_DEV_METRICS` es verdadero: agrega el tiempo y repite los tokens de `usage`, con la misma forma de siempre. Si no, viene en `null`. Cada lista de `classification` tiene al menos un elemento.

**Reglas:**

- Se clasifica todo artículo cuyo `number` no sea `encabezado` ni `promulgación`, y que no empiece por `título`, comparando en minúsculas y sin espacios al borde.
- Esas piezas igual se envían al modelo como contexto del cuerpo completo.
- Sin artículos clasificables la respuesta trae `results: []` y no llama al modelo.
- Más de `CLASSIFY_BATCH_SIZE` artículos (defecto 25) generan varias llamadas. El cuerpo completo va en cada una. Un fallo en cualquier lote responde error y no devuelve resultados parciales.
- Antes de armar el prompt se quitan imágenes embebidas `data:image/...;base64,...` del resumen y del texto de los artículos, y se reemplazan por `[imagen omitida]`. La base no se modifica.
- Las dimensiones 1 a 4 y 6 salen del texto. Si no hay respaldo, el valor es `No especificado`. `facility_installation_equipment` puede inferir una instalación típica; si no puede, también usa `No especificado`.
- Si hay candidatos y uno encaja, se copia tal cual. `activity_action`, `facility_installation_equipment` y `others` no tienen candidatos.
- El orden de `results` sigue `articles.order`, no el orden en que respondió el modelo.
- Ids de más que devuelva el modelo se ignoran. Si falta el id de un artículo pedido, la petición falla.

**Errores:**

| Código | Cuándo |
|---|---|
| 404 | No existe la fila en `legal_bodies` |
| 422 | `legal_body_id` no es UUID, o `candidate_values` no cumple las reglas de etiqueta |
| 502 | Error del modelo, respuesta inválida, artículo omitido, o fallo no previsto (incluye `number` o `section` nulos) |
| 503 | Falta `OPENAI_API_KEY` o `DATABASE_URL` |

**Limitaciones:**

- No persiste la clasificación.
- No comprueba si el cuerpo fue hidratado.
- No filtra por empresa.
