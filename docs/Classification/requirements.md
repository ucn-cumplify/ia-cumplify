# Módulo: Clasificación de cuerpos legales

> Clasifica los artículos de un cuerpo legal ya persistido por `backend-cumplify`, en seis dimensiones de cumplimiento, usando el modelo configurado en `OPENAI_MODEL`.

---

## Objetivo

Dado el id de una fila de `legal_bodies`, devolver una etiqueta por dimensión para cada artículo clasificable. El cuerpo legal y sus artículos los crea e hidrata el backend. Este módulo solo lee esas tablas y responde. No escribe la clasificación.

---

## Requerimientos

### CLS-001 — Clasificar un cuerpo legal hidratado

| Campo | Detalle |
|---|---|
| **ID** | CLS-001 |
| **Rol** | Servicio interno. Exige `X-API-Key`. |

**Descripción:**

El sistema debe clasificar los artículos de un cuerpo legal existente. La respuesta trae un resultado por artículo clasificable, en el orden de `articles.order`, con las seis dimensiones.

**Validaciones:**

- `legal_body_id` es un UUID y la fila existe en `legal_bodies`.
- Cada dimensión de cada artículo trae al menos un valor.
- La respuesta incluye `legal_body_id`, `title` y `results`.

---

### CLS-002 — Omitir piezas estructurales

| Campo | Detalle |
|---|---|
| **ID** | CLS-002 |
| **Rol** | Servicio interno |

**Descripción:**

No se clasifican los artículos cuyo `number`, luego de recortar espacios y pasar a minúsculas, es exactamente `encabezado` o `promulgación`, ni los que empiezan por `título`. Esas piezas siguen en el contexto que recibe el modelo.

**Validaciones:**

- La comparación ignora mayúsculas y espacios al borde.
- `titulo` o `promulgacion` sin acento no entran en la exclusión.
- Artículo, párrafo, capítulo y sección sí se clasifican.
- Un `number` o una `section` en null se leen como texto vacío. Un `number` vacío no es una pieza estructural: el artículo se clasifica y la respuesta trae su `number` vacío.

---

### CLS-003 — Reutilizar etiquetas existentes

| Campo | Detalle |
|---|---|
| **ID** | CLS-003 |
| **Rol** | Servicio interno |

**Descripción:**

El llamador puede enviar etiquetas ya usadas en otros cuerpos legales para `scope`, `productive_sector` y `territorial_coverage`. Si una encaja, el modelo debe copiarla tal cual. Una etiqueta nueva solo aparece cuando ninguna de las enviadas encaja.

`activity_action`, `facility_installation_equipment` y `others` no reciben candidatos: su cardinalidad es alta y la elige el modelo con las reglas del prompt.

**Validaciones:**

- Como máximo 60 etiquetas por dimensión.
- Cada etiqueta tiene entre 1 y 100 caracteres, sin `|` ni saltos de línea.
- Los duplicados se colapsan conservando el orden recibido.
- Si `candidate_values` se omite, o las tres listas quedan vacías, el prompt no incluye el bloque de etiquetas existentes.

---

### CLS-004 — Cuerpo sin artículos clasificables

| Campo | Detalle |
|---|---|
| **ID** | CLS-004 |
| **Rol** | Servicio interno |

**Descripción:**

Si el cuerpo no tiene artículos, o solo tiene piezas estructurales, la respuesta es 200 con `results` vacío. No se llama al modelo. El servicio no consulta `is_content_hydrated`: un cuerpo de la BCN aún no hidratado se ve igual que un cuerpo sin artículos.

---

### CLS-005 — Lotes

| Campo | Detalle |
|---|---|
| **ID** | CLS-005 |
| **Rol** | Servicio interno |

**Descripción:**

Los artículos clasificables se envían al modelo en lotes de `CLASSIFY_BATCH_SIZE` (defecto 25). Cada lote recibe el cuerpo legal completo como contexto (ver la deuda técnica). Los resultados se concatenan en el orden de los artículos. Si un lote falla, la petición sigue con los lotes restantes: la respuesta 200 trae lo clasificado y lista en `failed_article_ids` los artículos no clasificados. Falla el lote ante un error del proveedor, un timeout, una negativa, una respuesta cortada, una salida que no cumple el esquema o no es JSON, o cualquier otra respuesta 200 que no es una clasificación (un cuerpo que no es JSON o no es UTF-8, sin `choices` o con `message` nulo). Si ningún artículo se clasifica, la petición falla con `502`. Ni el detalle del error ni el log repiten la salida del modelo o el texto de la negativa.

---

### CLS-006 — Informar la versión del clasificador y el uso de tokens

| Campo | Detalle |
|---|---|
| **ID** | CLS-006 |
| **Rol** | Servicio interno |

**Descripción:**

Cada respuesta informa qué versión del clasificador la produjo y cuántos tokens costó, aunque las métricas de desarrollo estén apagadas. El backend usa la versión para decidir qué cuerpos volver a clasificar cuando cambia el prompt o el modelo, y el uso para controlar el costo de esas recategorizaciones.

**Validaciones:**

- `classifier_version` es `<PROMPT_VERSION>@<OPENAI_MODEL>`.
- Todo cambio de `SYSTEM_PROMPT` o del bloque de etiquetas existentes sube `PROMPT_VERSION`.
- `usage` suma `prompt_tokens`, `completion_tokens`, `total_tokens` y `cached_tokens` de los lotes que respondieron y de los lotes fallidos cuya respuesta 200 informa su uso (una negativa, una respuesta cortada por largo o por el filtro de contenido, o una salida que no cumple el esquema o no es JSON). `llm_calls` cuenta uno por lote, incluidos los fallidos.
- `cached_tokens` es la parte de `prompt_tokens` que el proveedor sirvió desde su caché (`usage.prompt_tokens_details.cached_tokens`, como en el chat), o 0 si no la informa.
- Sin artículos clasificables, todos los campos de `usage` valen 0.
- `dev_metrics`, cuando viaja, repite `prompt_tokens`, `completion_tokens`, `total_tokens` y `llm_calls` de `usage`. No trae `cached_tokens`.

---

## Dentro de alcance

- Leer `legal_bodies` (`id`, `title`, `summary`, `type`) y `articles` (`id`, `legal_body_id`, `number`, `section`, `text`, `order`) de la base del backend.
- Clasificar con el modelo, el esfuerzo de razonamiento y el tamaño de lote configurados.
- Usar timeout y reintentos explícitos del cliente OpenAI (`OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`).
- Exigir `X-API-Key` con el valor de `SERVICE_API_KEY`.
- Quitar del texto que se envía al modelo las imágenes y los demás archivos embebidos como data URI.
- Informar siempre la versión del clasificador y el uso de tokens.
- Adjuntar `dev_metrics` (tiempo y tokens) cuando `INCLUDE_DEV_METRICS` es verdadero.

## Fuera de alcance

- Crear, hidratar o editar cuerpos legales. Eso vive en `backend-cumplify`.
- Persistir la clasificación.
- Autorización por usuario y filtro por empresa: con la clave del servicio, cualquier id presente en la base se puede clasificar.
- Elegir los candidatos. El backend los arma desde su taxonomía y los envía en el request.

## Deuda técnica conocida

- `INCLUDE_DEV_METRICS` arranca en verdadero. En un entorno compartido conviene apagarlo; el uso de tokens igual viaja en `usage`.
- `PROMPT_VERSION` se sube a mano. Si un cambio del prompt no la sube, el backend no distingue las respuestas nuevas de las anteriores. `tests/test_profile_prompts.py` falla si cambian `SYSTEM_PROMPT` o el bloque de etiquetas existentes sin subirla.
- **Cuerpo completo en cada lote.** Cada lote repite el cuerpo legal completo (CLS-005). En la base local, de las 46 clasificaciones vigentes del 2026-10-02, las 7 normas de varios lotes sumaron el 71 % de los tokens de entrada, con unos 469.000 tokens de cuerpo repetido (estimación). Se decidió el 2026-10-08 no acotarlo por ahora y medir con `usage.cached_tokens` cuánto de esa repetición se cobra a precio de caché; el backend guarda la respuesta cruda con su `usage`. Una norma cuyo cuerpo no cabe en la ventana de contexto del modelo falla en todos sus lotes.
- Un lote fallido no aborta el resto. `failed_article_ids` lista lo que faltó y el lote suma 1 en `llm_calls`. Si el proveedor respondió 200 y cobró sin entregar una clasificación (una negativa, una respuesta cortada por largo o por el filtro de contenido, o una salida que no cumple el esquema o no es JSON), se suman a `usage` los tokens que informa el bloque `usage` de esa respuesta. Si no hubo respuesta (error de conexión, timeout o error HTTP) o el cuerpo no se pudo leer como JSON, sus tokens no se conocen y no se cuentan, aunque el proveedor pueda haberlos cobrado: el uso informado puede quedar por debajo del real. Los artículos que faltaron no se vuelven a pedir: recuperarlos le toca al backend.
- El cliente OpenAI reintenta 408, 409, 429, 5xx, timeouts y errores de conexión según `OPENAI_MAX_RETRIES`. En el peor caso un lote tarda (1 + `OPENAI_MAX_RETRIES`) × `OPENAI_TIMEOUT_SECONDS`, unos 9 minutos con los valores por defecto. No se vuelve a encolar un lote ya fallido después de esos reintentos.
- Los lotes van en serie, así que el peor caso de una norma es lotes × (1 + `OPENAI_MAX_RETRIES`) × `OPENAI_TIMEOUT_SECONDS`: con los valores por defecto, unos 9 minutos por lote, 27 con 3 lotes. El backend espera `AI_REQUEST_TIMEOUT_MINUTES` (defecto 15) por el pedido completo: desde 2 lotes, el peor caso lo supera, el backend corta, este servicio sigue llamando al modelo hasta terminar y el reintento del backend vuelve a pagar la norma completa. En condiciones normales queda lejos: el 2026-10-02, la norma de 128 artículos (6 lotes) de la base local tardó 106 s.
- Las reglas de longitud de etiqueta (4 palabras, 3 palabras, etc.) viven en el prompt. El esquema solo exige listas no vacías, así que una etiqueta más larga igual puede volver en la respuesta.
