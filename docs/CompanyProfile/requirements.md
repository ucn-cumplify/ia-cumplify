# Módulo: Perfil de empresa

> Clasifica el texto con que una empresa se describe en las seis dimensiones de cumplimiento, para que `backend-cumplify` arme su perfil declarado (`declared`) y lo cruce con las normas.

---

## Objetivo

Dado el texto que escribe una empresa sobre a qué se dedica, dónde opera, qué hace y qué no hace, devolver sus valores en las mismas seis dimensiones que recibe un artículo. El backend los compara por texto exacto, y por familia, con las etiquetas de los artículos, así que las dos clasificaciones tienen que hablar el mismo vocabulario. Por eso el llamador envía las etiquetas ya usadas y el modelo las reutiliza.

El texto llega desde `backend-cumplify`, que lo guarda en `company_profile_texts` y lo analiza en segundo plano (diseño en su `docs/AI/plan-sprint-1-categorizacion.md`, secciones 9.3 y 9.6). Este módulo no lee la base.

---

## Requerimientos

### PRF-001 — Clasificar el texto de una empresa

| Campo | Detalle |
|---|---|
| **ID** | PRF-001 |
| **Rol** | Servicio interno. Exige `X-API-Key`. |

**Descripción:**

El sistema debe devolver las seis dimensiones de la empresa a partir de su texto: `scope`, `productive_sector`, `territorial_coverage`, `activity_action`, `facility_installation_equipment` y `others`. Una sola llamada al modelo por texto.

**Validaciones:**

- `text` se recorta y debe quedar entre 1 y `PROFILE_TEXT_MAX_CHARS` caracteres (defecto 4.000), sin surrogates sueltos (un escape JSON como `\ud800` no es Unicode válido). Si no, `422`.
- Cada dimensión trae al menos un valor; sin respaldo en el texto, las dimensiones 1 a 4 y 6 valen `No especificado`.
- La respuesta incluye `classification`, `classifier_version` y `usage`.

---

### PRF-002 — Reutilizar etiquetas existentes

| Campo | Detalle |
|---|---|
| **ID** | PRF-002 |
| **Rol** | Servicio interno |

**Descripción:**

El llamador puede enviar etiquetas ya usadas en los artículos para `scope`, `productive_sector` y `territorial_coverage`. Si una encaja, el modelo la copia tal cual. Sin ellas, el texto de una empresa produce etiquetas en otro registro que las normas ("Extracción y procesamiento de minerales" en vez de "Minería") y el cruce exacto no las encuentra (plan del backend, 9.3).

**Validaciones:**

- Mismas reglas que en la clasificación de cuerpos legales: como máximo 60 etiquetas por dimensión, cada una de 1 a 100 caracteres, sin `|` ni saltos de línea, sin campos extra; los duplicados se colapsan conservando el orden.
- Si `candidate_values` se omite, o las tres listas quedan vacías, el prompt no incluye el bloque de etiquetas existentes.

---

### PRF-003 — No clasificar lo que el texto niega

| Campo | Detalle |
|---|---|
| **ID** | PRF-003 |
| **Rol** | Servicio interno |

**Descripción:**

Las empresas suelen escribir lo que no hacen ("No realizamos faenas mineras, no operamos calderas"), y la página de perfil del frontend se los pide. Esas frases descartan, no describen: el modelo no debe devolver un valor para algo que el texto niega, en ninguna dimensión. Lo que el texto afirma con una negación interna, como "residuos no peligrosos", sí se clasifica.

---

### PRF-004 — Describir a la empresa, no un artículo

| Campo | Detalle |
|---|---|
| **ID** | PRF-004 |
| **Rol** | Servicio interno |

**Descripción:**

- `territorial_coverage`: los lugares donde la empresa opera, con su nombre oficial y nivel. `Nacional` solo cuando dice que opera en todo el país; una planta u oficina se convierte en su comuna o región.
- `activity_action`: las actividades que la empresa realiza.
- `facility_installation_equipment`: las instalaciones y equipos que tiene o usa; puede inferir los que sus actividades implican claramente, nunca uno que el texto niega.
- `others`: el tamaño y los umbrales de la empresa (trabajadores, flota, capacidades, volúmenes, turnos), con números y unidades como vienen.

---

### PRF-005 — Informar la versión del clasificador y el uso de tokens

| Campo | Detalle |
|---|---|
| **ID** | PRF-005 |
| **Rol** | Servicio interno |

**Descripción:**

Cada respuesta informa qué versión del clasificador la produjo y cuántos tokens costó, como la clasificación de cuerpos legales.

**Validaciones:**

- `classifier_version` es `<PROFILE_PROMPT_VERSION>@<OPENAI_MODEL>`, por ejemplo `profile-v1@gpt-5.6-luna`.
- Todo cambio del prompt del perfil o de su bloque de etiquetas sube `PROFILE_PROMPT_VERSION`.
- `usage` trae los tokens de la llamada, con `cached_tokens` (la parte de `prompt_tokens` servida desde la caché del proveedor, 0 si no la informa), y `llm_calls` vale 1.
- `dev_metrics`, cuando viaja, repite los tokens de `usage`, sin `cached_tokens`, y agrega el tiempo.

---

### PRF-006 — Proteger el texto de la empresa

| Campo | Detalle |
|---|---|
| **ID** | PRF-006 |
| **Rol** | Servicio interno |

**Descripción:**

El texto es información de la empresa. No se registra en el log ni aparece en el cuerpo de un error: los errores dicen el tipo de falla, y el log, el largo del texto. Tampoco se copia la respuesta de rechazo del modelo, que podría citarlo, ni una salida que no cumple el esquema o no es JSON: esa falla responde `502` con un texto fijo. El texto es dato, no instrucciones: el prompt pide ignorar las instrucciones escritas dentro de él.

---

## Dentro de alcance

- Clasificar un texto con el modelo y el esfuerzo de razonamiento configurados (`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`).
- Reutilizar las etiquetas existentes que envía el llamador.
- Usar timeout y reintentos explícitos del cliente OpenAI (`OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`).
- Quitar del texto que se envía al modelo las imágenes y los demás archivos embebidos como data URI.
- Informar siempre la versión del clasificador y el uso de tokens.

## Fuera de alcance

- Guardar el texto o su clasificación, el enfriamiento entre análisis y quién puede escribirlo: eso vive en `backend-cumplify` (`/ai/profile/text` y `AiProfileTextWorker`).
- Elegir las etiquetas candidatas. El backend las arma desde su taxonomía.
- Leer la base de datos o recibir el id de la empresa.

## Deuda técnica conocida

- **Las exclusiones del texto no se devuelven.** El modelo evita los valores que el texto niega, pero no los entrega como exclusiones, así que no se aplican al perfil: una norma de un tema negado igual puede sugerirse si coincide con el perfil derivado. Devolverlas sería un campo adicional, compatible con el contrato actual. Se decidió el 2026-10-08 dejarlo fuera de las fases 0 y 1; hoy las exclusiones se registran a mano con `/ai/profile/exclusions` del backend.
- `PROFILE_TEXT_MAX_CHARS` tiene que coincidir con `AI_PROFILE_TEXT_MAX_CHARS` del backend: si el backend sube su tope y este no, un texto válido para el backend recibe `422`.
- `PROFILE_PROMPT_VERSION` se sube a mano. Si un cambio del prompt no la sube, el backend no distingue los perfiles nuevos de los anteriores. `tests/test_profile_prompts.py` falla si el prompt o su bloque de etiquetas cambian sin subirla, y también si las reglas de etiqueta o el formato del bloque se separan de los del prompt de artículos.
- Las reglas de longitud de etiqueta viven en el prompt. El esquema solo exige listas no vacías, así que una etiqueta más larga igual puede volver en la respuesta.
- El prompt (PRF-001 a PRF-007) se probó a mano con OpenAI y no tiene prueba automática, porque necesita el modelo real. El contrato sí la tiene: PRF-008 a PRF-012 y PRF-014 se ejecutan con pytest en `tests/test_company_profile_http.py`, con el adaptador real sobre un transporte falso de OpenAI, junto con dónde va el bloque de etiquetas existentes y que se quiten las imágenes y los adjuntos; PRF-013, en `tests/test_validation_errors.py`.
