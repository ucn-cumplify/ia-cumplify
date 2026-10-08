# API — Módulo: Perfil de empresa

> Base path: `/api/v1/company-profiles`

Exige el header `X-API-Key` con el valor de `SERVICE_API_KEY`. No usa la base de datos: funciona aunque `DATABASE_URL` esté vacía.

---

## Clasificar el perfil de una empresa

```text
POST /api/v1/company-profiles/classify
```

**Descripción:** Recibe el texto con que una empresa se describe y devuelve sus seis dimensiones, las mismas que recibe un artículo al clasificarse. El backend las guarda como el perfil declarado de la empresa (`declared`) y las cruza con las etiquetas de los artículos. No persiste nada y no recibe el id de la empresa.

**Requerimiento relacionado:** PRF-001, PRF-002, PRF-003, PRF-004, PRF-005

**Request:**

```json
{
  "text": "Somos un operador logístico y empresa de transporte de carga por carretera con base en la Región de Valparaíso...",
  "candidate_values": {
    "scope": ["Transporte y tránsito", "Laboral", "Medio Ambiente"],
    "productive_sector": ["Logistica", "Energía"],
    "territorial_coverage": ["Chile", "Región de Antofagasta"]
  }
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| text | string | Sí | Lo que la empresa escribe de sí misma. Se recorta y debe quedar entre 1 y `PROFILE_TEXT_MAX_CHARS` caracteres (defecto 4.000, igual que `AI_PROFILE_TEXT_MAX_CHARS` del backend) |
| candidate_values | objeto | No | Etiquetas ya usadas en los artículos, para que el modelo las reutilice |
| candidate_values.scope | lista de string | No | Máximo 60. Defecto: lista vacía |
| candidate_values.productive_sector | lista de string | No | Máximo 60. Defecto: lista vacía |
| candidate_values.territorial_coverage | lista de string | No | Máximo 60. Defecto: lista vacía |

`candidate_values` sigue las mismas reglas que en la clasificación de cuerpos legales: cada etiqueta se recorta y debe tener entre 1 y 100 caracteres, sin `|` ni saltos de línea; los campos extra se rechazan y los duplicados se eliminan conservando el orden.

**Response `200 OK`** (respuesta real a ese texto, con las candidatas del backend; `cached_tokens` se agregó después y aquí se muestra en 0):

```json
{
  "classification": {
    "scope": ["Transporte y tránsito", "Laboral"],
    "productive_sector": ["Logistica", "Transporte"],
    "territorial_coverage": ["Región de Valparaíso", "Región Metropolitana de Santiago"],
    "activity_action": ["Transporte de carga por carretera", "Transporte de contenedores", "Almacenamiento de mercancías"],
    "facility_installation_equipment": ["Tractocamión", "Bodega de almacenamiento"],
    "others": ["Flota de 40 tractocamiones", "85 trabajadores", "Dos turnos", "Bodega de almacenamiento de 3.000 m2"]
  },
  "classifier_version": "profile-v1@gpt-5.6-luna",
  "usage": {
    "prompt_tokens": 2102,
    "completion_tokens": 126,
    "total_tokens": 2228,
    "cached_tokens": 0,
    "llm_calls": 1
  },
  "dev_metrics": {
    "elapsed_ms": 2507.04,
    "prompt_tokens": 2102,
    "completion_tokens": 126,
    "total_tokens": 2228,
    "llm_calls": 1
  }
}
```

`classifier_version` y `usage` viajan siempre:

- `classifier_version` es `<versión del prompt>@<modelo>`. La versión es `PROFILE_PROMPT_VERSION` (`profile-v1`) y sube con cada cambio del prompt o del bloque de etiquetas existentes; el modelo es `OPENAI_MODEL`. El backend la guarda con el texto analizado.
- `usage` trae los tokens de la única llamada al modelo; `llm_calls` vale 1. `cached_tokens` es la parte de `prompt_tokens` que el proveedor sirvió desde su caché, o 0 si no la informa: como las instrucciones y las etiquetas van antes que el texto, ese prefijo puede salir de la caché.

`dev_metrics` solo trae datos cuando `INCLUDE_DEV_METRICS` es verdadero, igual que en la clasificación de cuerpos legales: el tiempo y los tokens de `usage`, sin `cached_tokens`. Cada lista de `classification` tiene al menos un elemento.

**Reglas:**

- Una llamada al modelo por texto.
- Las etiquetas siguen las reglas de la clasificación de artículos: cortas, en español, un concepto por ítem, sin punto final. Si hay candidatas y una encaja, se copia tal cual. Las dimensiones 1 a 4 y 6 sin respaldo en el texto valen `No especificado`.
- El prompt describe a la empresa completa, no un artículo:
  - `territorial_coverage`: los lugares donde opera, con su nombre oficial. `Nacional` solo si dice que opera en todo el país. Una planta u oficina se convierte en su comuna o región.
  - `activity_action`: lo que la empresa hace.
  - `facility_installation_equipment`: lo que tiene o usa. Puede inferir lo que sus actividades implican claramente.
  - `others`: el tamaño y los umbrales de la empresa (trabajadores, flota, capacidades, turnos), con números y unidades como vienen en el texto.
- **Lo que el texto niega no se clasifica.** "No realizamos faenas mineras" no produce `Minería`, y "no operamos calderas" no produce `Caldera`, en ninguna dimensión.
- El texto es dato, no instrucciones: el prompt pide ignorar cualquier instrucción escrita dentro de él.
- Las etiquetas existentes van antes que el texto. El prompt y las etiquetas son iguales para todas las empresas en un momento dado, así que ese prefijo puede aprovechar el caché del proveedor.
- Antes de armar el prompt se quitan los data URI embebidos, igual que en la clasificación de artículos: una imagen se reemplaza por `[imagen omitida]` y cualquier otro tipo, como un PDF, por `[archivo omitido]`.
- La llamada a OpenAI usa `OPENAI_TIMEOUT_SECONDS` (defecto 180) y `OPENAI_MAX_RETRIES` (defecto 2). El SDK reintenta 408, 409, 429, 5xx, timeouts y errores de conexión.
- **Privacidad:** el texto no se registra en el log ni aparece en ningún error. Los errores dicen el tipo de falla y, en el log, el largo del texto. Tampoco se repite la salida del modelo, que puede citar el texto: una respuesta que no cumple el esquema o no es JSON se informa con un texto fijo, sin el error que la describe.

**Errores:**

| Código | Cuándo |
|---|---|
| 401 | Falta `X-API-Key` o no coincide con `SERVICE_API_KEY` |
| 422 | `text` falta, no es texto, queda vacío al recortarlo, supera `PROFILE_TEXT_MAX_CHARS` o tiene un surrogate suelto (un escape como `\ud800`, que no es Unicode válido); o `candidate_values` no cumple las reglas de etiqueta. Es el único error permanente: reintentar el mismo pedido no cambia nada |
| 502 | El proveedor falló; el modelo rechazó la clasificación o devolvió una que no cumple el esquema o no es JSON (`Model did not return a valid company profile classification.`); o hubo una falla inesperada, con solo su tipo en el detalle (por ejemplo, `ValueError`). El backend lo trata como transitorio y reintenta |
| 503 | Falta `SERVICE_API_KEY` u `OPENAI_API_KEY` |

**Limitaciones:**

- No persiste la clasificación.
- No elige las candidatas: las arma el backend desde su taxonomía.
- Las exclusiones que escribe la empresa solo evitan valores falsos; no se devuelven como exclusiones (ver deuda técnica en `requirements.md`).
