# Módulo: Motivos de aplicabilidad

> Escribe un motivo en español por cada artículo que el backend va a poner en una alerta, a partir de la norma, los valores coincidentes y las acciones del artículo, y dice qué exige ese artículo a la empresa. Nunca cita el texto con que la empresa se describe.

---

## Objetivo

Dado un cuerpo legal y un puñado de artículos ya elegidos para una alerta (como máximo los de `AI_SCORE_TOP_ARTICLES` en el backend, 3 por defecto), devolver un texto corto que explique la coincidencia. El backend ya calculó qué valores del perfil coinciden y qué acciones extrae `activity_action`; este servicio no vuelve a clasificar ni a puntuar.

El pedido llega desde `backend-cumplify` al armar el borrador de la alerta (`SuggestionAlertDraftLoader`, con `AI_APPLICABILITY_REASONS_ENABLED=true`). Si la llamada falla, se vence o un artículo no trae motivo, ese artículo conserva la plantilla de §11.4. Este módulo no escribe en la base.

Desde `applicability-v2` (paso 0.16 del plan del sprint 3, C9), cada artículo trae además su análisis: la naturaleza, las obligaciones explícitas que impone a la empresa y la norma a la que remite. Las obligaciones sugeridas de una alerta dejan de salir de las frases de `activity_action`, que describen actividades y aparecen también en artículos informativos o que modifican otra ley.

---

## Requerimientos

### APR-001 — Un motivo por artículo pedido

| Campo | Detalle |
|---|---|
| **ID** | APR-001 |
| **Rol** | Servicio interno. Exige `X-API-Key`. |

**Descripción:**

El sistema debe devolver un motivo en español por cada artículo del pedido que exista en esa norma, salvo los que llegan con `write_reason` en falso (APR-008). Una sola llamada al modelo por pedido, que trae también el análisis (APR-006 y APR-007).

**Validaciones:**

- `legal_body_id` es un UUID y la fila existe en `legal_bodies`.
- `articles` tiene entre 1 y 10 elementos; `article_id` no se repite.
- Cada artículo que pide motivo trae al menos un `matched_values`. Uno con `write_reason` en falso puede no traer ninguno (APR-008).
- La respuesta incluye `reasons`, `analyses`, `reason_version` y `usage`.

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

- `reason_version` es `<APPLICABILITY_PROMPT_VERSION>@<OPENAI_MODEL>`, por ejemplo `applicability-v2@gpt-5.6-luna`.
- Todo cambio del prompt o del armado del mensaje de usuario sube `APPLICABILITY_PROMPT_VERSION`. La limpieza de los datos que llegan al mensaje no cuenta como cambio del armado: por ejemplo, reemplazar un adjunto por `[archivo omitido]` o leer una `section` nula como texto vacío, que cambiaron el 2026-10-08 sin subirla.
- `usage.llm_calls` vale 1 cuando se llamó al modelo, y 0 cuando `reasons` queda vacío sin llamarlo.
- Cada campo del `usage` del proveedor se lee por separado: uno que falta o no es un entero positivo cuenta 0, sin hacer fallar la respuesta (caso APR-017 de `test.csv`).
- `dev_metrics`, cuando viaja, repite los tokens de `usage`, sin `cached_tokens`, y agrega el tiempo.

---

### APR-006 — Decidir la naturaleza del artículo

| Campo | Detalle |
|---|---|
| **ID** | APR-006 |
| **Rol** | Servicio interno |

**Descripción:**

Cada artículo vuelve con su naturaleza, que sale solo de su texto, nunca de los valores coincidentes ni de las acciones. Las reglas son las de C9 (decisión 7 del plan del sprint 3), las mismas de la revisión de respaldos:
- `explicit_obligations`: impone obligaciones a la empresa, o al tipo de empresa al que se dirige. Una prohibición también cuenta, aunque el artículo conceda un beneficio del Estado: la Ley 21.840, art. 1, concede un bono y prohíbe a las instituciones públicas o privadas retenerlo o compensarlo (APR-025 de `test.csv`).
- `informative`: no impone obligaciones a la empresa. Un artículo que solo obliga o faculta al Estado es informativo.
- `referral`: modifica otra ley, aunque el texto agregado imponga una obligación. Trae la norma a la que remite si el texto la nombra.

**Validaciones:**

- Un artículo informativo o de remisión vuelve sin obligaciones, aunque el modelo las haya escrito.
- Solo una remisión conserva `referred_legal_body`, en una línea y con hasta 300 caracteres; si no, vuelve `null`.
- Un artículo que no aparece en `analyses` quedó sin evaluar. El análisis no depende del motivo: un motivo descartado no se lleva el análisis.

---

### APR-007 — Sacar las obligaciones solo del texto del artículo

| Campo | Detalle |
|---|---|
| **ID** | APR-007 |
| **Rol** | Servicio interno |

**Descripción:**

Con `explicit_obligations`, el artículo trae cada obligación que impone a la empresa, con un título y un resumen de una o dos oraciones. Una acción de `activity_action` nunca se convierte en obligación, y una obligación que recae solo en el Estado no cuenta. La regla de no dictaminar de APR-004 vale solo para el motivo: el resumen de una obligación puede decir lo que la empresa debe hacer, porque repite el artículo.

**Validaciones:**

- Se descartan las obligaciones sin título o sin resumen, las de más de 200 caracteres de título o 1000 de resumen, y las repetidas. Quedan como máximo 10, en una línea cada texto.
- Si no queda ninguna, el artículo no trae análisis y el backend lo muestra sin evaluar, en vez de una lista vacía.

---

### APR-008 — Pedir solo el análisis

| Campo | Detalle |
|---|---|
| **ID** | APR-008 |
| **Rol** | Servicio interno |

**Descripción:**

Un artículo con `write_reason` en falso recibe su análisis y ningún motivo. Así lo pide el backend cuando conserva su propio texto: un artículo con un par difuso del nivel 2 (paso 0.18) o una alerta ya emitida que se completa una sola vez (paso 0.20).

**Validaciones:**

- Sin `write_reason`, el artículo recibe su motivo, como con `applicability-v1`.
- Con `write_reason` en falso, `matched_values` puede ir vacía o faltar. El análisis no depende de los valores, y el 2026-10-14, 80 de las 111 sugerencias de las alertas de la IA de la base local no tenían sus valores coincidentes guardados.
- Si el modelo escribe igual un motivo para un artículo con `write_reason` en falso, se descarta.

---

## Dentro de alcance

- Leer el cuerpo legal y los artículos pedidos.
- Escribir un motivo por artículo con el modelo y el esfuerzo de razonamiento configurados (`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`).
- Decidir en la misma llamada la naturaleza de cada artículo y sus obligaciones explícitas, y cerrar en código las reglas de C9.
- Usar timeout y reintentos propios (`APPLICABILITY_REASONS_TIMEOUT_SECONDS`, `APPLICABILITY_REASONS_MAX_RETRIES`), más cortos que los de clasificar, para caber en la espera del backend.
- Quitar del texto que se envía al modelo las imágenes y los demás archivos embebidos como data URI.
- Informar siempre la versión del prompt y el uso de tokens.

## Fuera de alcance

- Armar `matched_values` y `actions`, emitir la alerta y caer a la plantilla: eso vive en `backend-cumplify` (`SuggestionAlertDraftLoader`, `AlertDraftBuilder`).
- Clasificar artículos, puntuar sugerencias o leer el perfil de la empresa.
- Guardar el motivo y el análisis. Los persiste el backend en la alerta, si la emite (pasos 0.18 a 0.20 del plan del sprint 3).

## Deuda técnica conocida

- `APPLICABILITY_PROMPT_VERSION` se sube a mano. Si un cambio del prompt no la sube, un llamador no distingue los motivos nuevos de los anteriores.
- El tope de 10 artículos y de 2000 caracteres del motivo son constantes de este servicio. El backend envía como máximo 3 artículos y descarta un motivo de más de 2000; si alguno de los dos lados cambia su tope y el otro no, el pedido válido para uno falla o se ignora en el otro.
- Las pruebas automáticas (`tests/`) cubren el caso de uso, el contrato HTTP y que el prompt no lleva artículos no pedidos ni un bloque de descripción de empresa. No llaman a OpenAI: el tono del motivo (no dictaminar, no citar un perfil) se calibra a mano contra el servicio (APR-011 a APR-015 en `test.csv`).
- La naturaleza y las obligaciones dependen de que el modelo siga el prompt. Las pruebas automáticas fijan el texto del prompt y el cierre en código, y la evaluación con artículos ya alertados (paso 0.17: `scripts/applicability_eval.py`, casos APR-023 a APR-025 de `test.csv`) lo comprobó el 2026-10-14 con 9 pedidos y 10 artículos. Acertó en todos, después de corregir la expectativa de la Ley 21.840, art. 1. El pedido más lento tardó 6,9 s de los 25 s de plazo y la ejecución costó US$0,0050. Un cambio del prompt pide volver a ejecutarla.
