# Módulo: Motivos de aplicabilidad

> Escribe un motivo en español por cada artículo que el backend va a poner en una alerta, a partir de la norma, los valores coincidentes y las acciones del artículo. Nunca cita el texto con que la empresa se describe.

---

## Objetivo

Dado un cuerpo legal y un puñado de artículos ya elegidos para una alerta (como máximo los de `AI_SCORE_TOP_ARTICLES` en el backend, 3 por defecto), devolver un texto corto que explique la coincidencia. El backend ya calculó qué valores del perfil coinciden y qué acciones extrae `activity_action`; este servicio no vuelve a clasificar ni a puntuar.

El pedido llega desde `backend-cumplify` al armar el borrador de la alerta (`SuggestionAlertDraftLoader`, con `AI_APPLICABILITY_REASONS_ENABLED=true`). Si la llamada falla, se vence o un artículo no trae motivo, ese artículo conserva la plantilla de §11.4. Este módulo no escribe en la base.

---

## Requerimientos

### APR-001 — Un motivo por artículo pedido

| Campo | Detalle |
|---|---|
| **ID** | APR-001 |
| **Rol** | Servicio interno. Exige `X-API-Key`. |

**Descripción:**

El sistema debe devolver un motivo en español por cada artículo del pedido que exista en esa norma. Una sola llamada al modelo por pedido.

**Validaciones:**

- `legal_body_id` es un UUID y la fila existe en `legal_bodies`.
- `articles` tiene entre 1 y 10 elementos; `article_id` no se repite.
- Cada artículo trae al menos un `matched_values`.
- La respuesta incluye `reasons`, `reason_version` y `usage`.

---

### APR-002 — Leer solo los artículos pedidos

| Campo | Detalle |
|---|---|
| **ID** | APR-002 |
| **Rol** | Servicio interno |

**Descripción:**

El modelo recibe el título, el tipo y el resumen de la norma, y el texto de los artículos cuyo id vino en el pedido. El resto de los artículos de la norma no se envían. Un id que no está en esa norma se omite de `reasons` y no aborta el pedido.

**Validaciones:**

- Si ningún artículo del pedido está en la norma, `reasons` es `[]` y no se llama al modelo.
- Si la norma no existe, `404`.

---

### APR-003 — Nunca citar el texto del perfil

| Campo | Detalle |
|---|---|
| **ID** | APR-003 |
| **Rol** | Servicio interno |

**Descripción:**

El pedido no trae el texto con que la empresa se describe. El prompt prohíbe citarlo, parafrasearlo o inventarlo. La empresa solo aparece a través de `matched_values` y `actions`. `legal_requirement_id` nombra la app para el backend; este servicio no lo envía al modelo ni lee esa tabla.

**Validaciones:**

- El esquema no admite un campo de texto de empresa.
- El log no registra etiquetas, acciones ni el texto del artículo.

---

### APR-004 — Explicar la coincidencia, no dictaminar

| Campo | Detalle |
|---|---|
| **ID** | APR-004 |
| **Rol** | Servicio interno |

**Descripción:**

El motivo nombra los valores coincidentes entre comillas («…») y, si ayudan, las acciones. No dice que el artículo aplica, ni que la empresa debe cumplirlo. No menciona artículos que no vinieron en el pedido, ni ids internos, ni que el texto lo escribió una IA.

**Validaciones:**

- Un motivo vacío o de más de 2000 caracteres no entra en `reasons`. El backend deja la plantilla.

---

### APR-005 — Informar la versión del prompt y el uso de tokens

| Campo | Detalle |
|---|---|
| **ID** | APR-005 |
| **Rol** | Servicio interno |

**Descripción:**

Cada respuesta informa qué versión del prompt la produjo y cuántos tokens costó, como la clasificación de cuerpos legales.

**Validaciones:**

- `reason_version` es `<APPLICABILITY_PROMPT_VERSION>@<OPENAI_MODEL>`, por ejemplo `applicability-v1@gpt-5.6-luna`.
- Todo cambio del prompt o del armado del mensaje de usuario sube `APPLICABILITY_PROMPT_VERSION`.
- `usage.llm_calls` vale 1 cuando se llamó al modelo, y 0 cuando `reasons` queda vacío sin llamarlo.
- `dev_metrics`, cuando viaja, repite los tokens de `usage` y agrega el tiempo.

---

## Dentro de alcance

- Leer el cuerpo legal y los artículos pedidos.
- Escribir un motivo por artículo con el modelo y el esfuerzo de razonamiento configurados (`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`).
- Usar timeout y reintentos propios (`APPLICABILITY_REASONS_TIMEOUT_SECONDS`, `APPLICABILITY_REASONS_MAX_RETRIES`), más cortos que los de clasificar, para caber en la espera del backend.
- Quitar imágenes embebidas en base64 del texto que se envía al modelo.
- Informar siempre la versión del prompt y el uso de tokens.

## Fuera de alcance

- Armar `matched_values` y `actions`, emitir la alerta y caer a la plantilla: eso vive en `backend-cumplify` (`SuggestionAlertDraftLoader`, `AlertDraftBuilder`).
- Clasificar artículos, puntuar sugerencias o leer el perfil de la empresa.
- Guardar el motivo. Lo persiste el backend en la alerta, si la emite.

## Deuda técnica conocida

- `APPLICABILITY_PROMPT_VERSION` se sube a mano. Si un cambio del prompt no la sube, un llamador no distingue los motivos nuevos de los anteriores.
- El tope de 10 artículos y de 2000 caracteres del motivo son constantes de este servicio. El backend envía como máximo 3 artículos y descarta un motivo de más de 2000; si alguno de los dos lados cambia su tope y el otro no, el pedido válido para uno falla o se ignora en el otro.
- Las pruebas automáticas (`tests/`) cubren el caso de uso, el contrato HTTP y que el prompt no lleva artículos no pedidos ni un bloque de descripción de empresa. No llaman a OpenAI: el tono del motivo (no dictaminar, no citar un perfil) se calibra a mano contra el servicio (APR-011 a APR-015 en `test.csv`).
