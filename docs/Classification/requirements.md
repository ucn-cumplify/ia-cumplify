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
| **Rol** | Servicio interno. El endpoint no exige autenticación. |

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

Los artículos clasificables se envían al modelo en lotes de `CLASSIFY_BATCH_SIZE` (defecto 25). Cada lote recibe el cuerpo legal completo como contexto. Los resultados se concatenan en el orden de los artículos. Si un lote falla, la petición sigue con los lotes restantes: la respuesta 200 trae lo clasificado y lista en `failed_article_ids` los artículos no clasificados. Si ningún artículo se clasifica, la petición falla.

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
- `usage` suma `prompt_tokens`, `completion_tokens` y `total_tokens` de todas las llamadas, y `llm_calls` cuenta una por lote (incluidos los fallidos).
- Sin artículos clasificables, todos los campos de `usage` valen 0.
- `dev_metrics`, cuando viaja, repite los tokens de `usage`.

---

## Dentro de alcance

- Leer `legal_bodies` (`id`, `title`, `summary`, `type`) y `articles` (`id`, `legal_body_id`, `number`, `section`, `text`, `order`) de la base del backend.
- Clasificar con el modelo, el esfuerzo de razonamiento y el tamaño de lote configurados.
- Usar timeout y reintentos explícitos del cliente OpenAI (`OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`).
- Quitar imágenes embebidas en base64 del texto que se envía al modelo.
- Informar siempre la versión del clasificador y el uso de tokens.
- Adjuntar `dev_metrics` (tiempo y tokens) cuando `INCLUDE_DEV_METRICS` es verdadero.

## Fuera de alcance

- Crear, hidratar o editar cuerpos legales. Eso vive en `backend-cumplify`.
- Persistir la clasificación.
- Autenticación, autorización y filtro por empresa. Cualquier id presente en la base se puede clasificar.
- Elegir los candidatos. El backend los arma desde su taxonomía y los envía en el request.

## Deuda técnica conocida

- `articles.number` o `articles.section` en null hace fallar la petición, porque el clasificador trata esos campos como texto.
- `INCLUDE_DEV_METRICS` arranca en verdadero. En un entorno compartido conviene apagarlo; el uso de tokens igual viaja en `usage`.
- `PROMPT_VERSION` se sube a mano. Si un cambio del prompt no la sube, el backend no distingue las respuestas nuevas de las anteriores.
- Un lote fallido no aborta el resto. `failed_article_ids` lista lo que faltó; si el proveedor no informa tokens de ese lote, `llm_calls` igual suma 1 y esos tokens pueden quedar en 0.
- El cliente OpenAI reintenta 408/429/5xx según `OPENAI_MAX_RETRIES`. No se vuelve a encolar un lote ya fallido después de esos reintentos.
- Las reglas de longitud de etiqueta (4 palabras, 3 palabras, etc.) viven en el prompt. El esquema solo exige listas no vacías, así que una etiqueta más larga igual puede volver en la respuesta.
