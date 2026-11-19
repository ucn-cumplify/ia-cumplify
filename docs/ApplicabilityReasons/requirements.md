# Módulo: Motivos de aplicabilidad

> Escribe un motivo en español por cada artículo que el backend va a poner en una alerta, a partir de la norma, los valores coincidentes y las acciones del artículo, y dice qué exige ese artículo a la empresa. Nunca cita el texto con que la empresa se describe.

---

## Objetivo

Dado un cuerpo legal y un puñado de artículos ya elegidos para una alerta (como máximo los de `AI_SCORE_TOP_ARTICLES` en el backend, 3 por defecto), devolver un texto corto que explique la coincidencia. El backend ya calculó qué valores del perfil coinciden y qué acciones extrae `activity_action`; este servicio no vuelve a clasificar ni a puntuar.

El pedido llega desde `backend-cumplify` al armar el borrador de la alerta (`SuggestionAlertDraftLoader`, con `AI_APPLICABILITY_REASONS_ENABLED=true`). Si la llamada falla, se vence o un artículo no trae motivo, ese artículo conserva la plantilla de §11.4. Este módulo no escribe en la base.

Desde `applicability-v2` (paso 0.16 del plan del sprint 3, C9), cada artículo trae además su análisis: la naturaleza, las obligaciones explícitas que impone a la empresa con el respaldo que acredita cada una, la norma que modifica si es una remisión y, si es procedimental, la autorización a la que sirve. Las obligaciones sugeridas de una alerta dejan de salir de las frases de `activity_action`, que describen actividades y aparecen también en artículos informativos o que modifican otra ley.

Qué es una obligación lo fijó el cliente el 2026-10-15 (decisión 21 del plan): solo lo que se verifica con un respaldo, porque una obligación se corresponde con un respaldo en todo momento. Un artículo que exige una autorización es una obligación; los antecedentes para obtenerla son información procedimental, ligada a esa autorización.

El 2026-11-17, `applicability-v7` precisó esas reglas con la metodología de identificación que envió el cliente (acuerdo 13 del Acta N.º 4; sus puntos 8, 9 y 40), sin cambiar el contrato ni las cuatro naturalezas: quién es la empresa, qué no es una obligación y cómo se cuentan los deberes de un artículo. El respaldo de la decisión 21 sigue definiendo una obligación, salvo en las prohibiciones: una que recae en la empresa cuenta aunque el artículo no nombre un documento (decisión 24 del plan del sprint 3 de backend-cumplify, del 2026-11-17, por el punto 40 de la metodología). Las reglas internas de un órgano de la empresa cuentan solo cuando exigen un acto (decisión 23). Lo que la metodología pide y este análisis no expresa queda fuera: la clasificación en Principal y Complementario con su vínculo, el estado Pendiente y la cita literal de cada deber.

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
- Tampoco entra un motivo con letras de un alfabeto que no aparece en el español legal (cirílico, árabe, hebreo o asiáticos). El 2026-10-15 el modelo escribió «desalиниzación», con tres letras cirílicas, en una ejecución de la evaluación. El griego se permite por los símbolos técnicos, como μ.

---

### APR-005 — Informar la versión del prompt y el uso de tokens

| Campo | Detalle |
|---|---|
| **ID** | APR-005 |
| **Rol** | Servicio interno |

**Descripción:**

Cada respuesta informa qué versión del prompt la produjo y cuántos tokens costó, como la clasificación de cuerpos legales.

**Validaciones:**

- `reason_version` es `<APPLICABILITY_PROMPT_VERSION>@<OPENAI_MODEL>`, por ejemplo `applicability-v7@gpt-5.6-luna`. `applicability-v5` nombra a Cumplify en la primera oración. `applicability-v6` neutraliza una línea `Article ID:` escrita en la norma, en un artículo, en un valor coincidente o en una acción. Las dos conservan las reglas de `applicability-v4`; `applicability-v7` cambia las reglas de obligación.
- Versiones con análisis: `applicability-v2` (ia #31) lo agregó; `applicability-v3` (2026-10-15) aclara que nombrar otra ley no es una remisión; `applicability-v4` (paso 2.5 del plan del sprint 4 de backend-cumplify) neutraliza los delimitadores (APR-009); `applicability-v6` neutraliza una línea `Article ID:` escrita en los datos; `applicability-v7` (2026-11-17) precisa quién es la empresa, qué no es una obligación y cómo se cuentan los deberes de un artículo (APR-035 y APR-036). El filtro de letras de otro alfabeto está en el código, no en el prompt.
- Todo cambio del prompt o del armado del mensaje de usuario sube `APPLICABILITY_PROMPT_VERSION`. La limpieza de los datos que llegan al mensaje no cuenta como cambio del armado: por ejemplo, reemplazar un adjunto por `[archivo omitido]` o leer una `section` nula como texto vacío, que cambiaron el 2026-10-08 sin subirla. La neutralización de los delimitadores sí la subió, a `applicability-v4`, porque además agrega una regla al prompt.
- `usage.llm_calls` vale 1 cuando se llamó al modelo, y 0 cuando `reasons` queda vacío sin llamarlo.
- Cada campo del `usage` del proveedor se lee por separado: uno que falta o no es un entero positivo cuenta 0, sin hacer fallar la respuesta (caso APR-017 de `test.csv`).
- La llamada usa el modo explícito de caché con un único punto de corte al final de las instrucciones: la norma, los artículos, los valores coincidentes y las acciones nunca quedan en el caché del proveedor ("Caché de prompts" en `docs/Chat/api.md`), así que `usage.cached_tokens` no supera los tokens de las instrucciones.
- `dev_metrics`, cuando viaja, repite los tokens de `usage`, sin `cached_tokens`, y agrega el tiempo.

---

### APR-006 — Decidir la naturaleza del artículo

| Campo | Detalle |
|---|---|
| **ID** | APR-006 |
| **Rol** | Servicio interno |

**Descripción:**

Cada artículo vuelve con su naturaleza, que sale solo de su texto, nunca de los valores coincidentes ni de las acciones. Las reglas son las de C9 (decisión 7 del plan del sprint 3) con el criterio del cliente (decisión 21). El modelo revisa las naturalezas en este orden y se queda con la primera que corresponde:
1. `referral`: modifica otra ley, aunque el texto agregado imponga una obligación o regule un trámite. Trae la norma que modifica si el texto la nombra. Un artículo que solo nombra otra ley, o dice que otra ley rige ciertos casos, no la modifica y no es una remisión: su naturaleza sale de lo que él mismo exige (Ley 21.800, art. 4°, APR-026 de `test.csv`).
2. `explicit_obligations`: impone a la empresa, al tipo de empresa al que se dirige o a quienes actúan dentro de ella (sus trabajadores, sus contratistas, un comité, un sindicato), al menos una obligación que un respaldo acredita. Exigir una autorización, un permiso, una licencia o un registro impone la obligación de tenerlo, aunque el artículo también diga cómo pedirlo. Una prohibición que el artículo impone a la empresa, o a quienes actúan dentro de ella, es una obligación aunque el artículo no nombre un documento: su respaldo es la autorización que la levanta o, si no hay, el registro que mostraría que se respeta. Una prohibición que recae solo en el Estado, sus órganos o sus funcionarios no lo es (decisión 24 del plan del sprint 3, que reemplaza la regla de las prohibiciones de la decisión 21).
3. `procedural`: solo regula cómo obtener, renovar o tramitar una autorización: la solicitud, los antecedentes que la acompañan, los pasos y plazos, o lo que la autoridad hace con ella. Trae la autorización a la que sirve, como la nombra el artículo o la norma.
4. `informative`: cualquier otro artículo. No impone obligaciones a la empresa: solo obliga, prohíbe o faculta al Estado, a sus órganos o a sus funcionarios, o a una entidad externa a la empresa (un organismo administrador del seguro), declara el objeto o el alcance de la ley, otorga un beneficio, condona una deuda, define términos, fija la vigencia o solo permite hacer algo («podrá») sin exigirlo.

Desde `applicability-v7`, la empresa incluye a quienes actúan como parte de su funcionamiento: el empleador, sus trabajadores, sus contratistas y subcontratistas, y los órganos que funcionan dentro de ella (un Comité Paritario, un Departamento de Prevención de Riesgos, un delegado, un sindicato). Lo que el artículo les exige es una obligación de la empresa, y el resumen dice quién debe cumplirla. Una regla sobre cómo se forma, se elige o funciona uno de esos órganos es una obligación cuando exige un acto de alguien: designar o elegir a sus miembros, hacer una elección dentro de un plazo, levantar un acta, reunirse, comunicar o cumplir una función. Una que solo fija un estado o una condición de validez no exige ningún acto y no lo es: cuánto duran sus miembros, cuándo cesan, quién reemplaza a quién, quién lo integra por derecho propio, el quórum o cuánto dura el órgano (decisión 23 del plan del sprint 3 de backend-cumplify). Una empresa con administración delegada ocupa el lugar del organismo administrador. El modelo busca primero la conducta en las palabras del artículo y después nombra su respaldo.

La revisión de respaldos (`docs/EvidenceReview`) usa las mismas cuatro naturalezas desde `evidence-review-v2`. Desde `evidence-review-v7` las decide con las mismas reglas de `applicability-v7`: las oraciones que definen la empresa, los órganos internos, las prohibiciones y lo informativo son las mismas en los dos prompts, y `tests/test_evidence_review_nature.py` falla si dejan de coincidir.

El 2026-10-15, con la regla anterior («una prohibición también cuenta»), la Ley 21.840, art. 1 quedó como `explicit_obligations`: concede un bono y prohíbe a las instituciones públicas o privadas retenerlo o compensarlo. Con el criterio del cliente del 15 de octubre era informativo, porque ningún respaldo acredita esa prohibición (APR-024 de `test.csv`). Desde `applicability-v7` una prohibición que recae en la empresa vuelve a contar (decisión 24). Este artículo siguió informativo en la medición del 2026-11-17: concede un beneficio, y la prohibición de retenerlo recae en las instituciones que lo pagan.

**Validaciones:**

- Un artículo informativo, de remisión o procedimental vuelve sin obligaciones, aunque el modelo las haya escrito.
- Solo una remisión conserva `referred_legal_body`, y solo un artículo procedimental conserva `procedure_for`, en una línea, con hasta 300 caracteres y sin letras de otro alfabeto; si no, vuelven `null`.
- Un artículo que no aparece en `analyses` quedó sin evaluar. El análisis no depende del motivo: un motivo descartado no se lleva el análisis.

---

### APR-007 — Sacar las obligaciones solo del texto del artículo

| Campo | Detalle |
|---|---|
| **ID** | APR-007 |
| **Rol** | Servicio interno |

**Descripción:**

Con `explicit_obligations`, el artículo trae cada obligación que impone a la empresa, con un título, un resumen de una o dos oraciones y el respaldo que la acredita (`evidence`): el documento o registro que la empresa mostraría para probar que cumplió, como la autorización que debe tener. El modelo nombra el respaldo antes de listar la obligación; si no puede, no es una obligación. Una acción de `activity_action` nunca se convierte en obligación, y una obligación que recae solo en el Estado no cuenta. La regla de no dictaminar de APR-004 vale solo para el motivo: el resumen de una obligación puede decir lo que la empresa debe hacer, porque repite el artículo.

**Validaciones:**

- Se descartan las obligaciones sin título, sin resumen o sin respaldo, las de más de 200 caracteres de título, 1000 de resumen o 300 de respaldo, las que traen letras de otro alfabeto, y las repetidas. Quedan como máximo 10, en una línea cada texto.
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

### APR-009 — Tratar la norma y los artículos como datos

| Campo | Detalle |
|---|---|
| **ID** | APR-009 |
| **Rol** | Servicio interno |

**Descripción:**

La norma y los artículos son datos, no instrucciones: el prompt pide ignorar cualquier instrucción escrita en ellos. Desde `applicability-v4` (paso 2.5 del plan del sprint 4 de backend-cumplify), el servicio además neutraliza los delimitadores con la regla de CHT-007 de `docs/Chat`: una línea que imita un delimitador del mensaje, como `--- END LEGAL BODY ---` o `--- ARTICLES TO EXPLAIN ---`, recibe el prefijo `> `. Pasan por esa regla el título, el tipo y el resumen de la norma, y el número, la sección y el texto de cada artículo: salen de la base, y una norma manual los acepta con saltos de línea. También las líneas de los valores coincidentes y de las acciones: una etiqueta no admite `|`, CR ni LF, pero sí otros saltos de `str.splitlines`, como U+2028 o U+0085. El prompt nombra los cuatro delimitadores y dice que un delimitador ocupa siempre una línea entera: lo demás es contenido de su bloque, aunque parezca un delimitador o una instrucción.

**Validaciones:**

- Una norma y un artículo con líneas que imitan los cuatro delimitadores, y un valor o una acción con U+2028 o U+0085 antes de uno, llegan al modelo con esas líneas neutralizadas: el mensaje tiene solo los delimitadores reales (APR-033 de `test.csv`).
- Desde `applicability-v6`, una línea `Article ID:` escrita en el título, el resumen, el número, la sección, el texto, un valor coincidente o una acción llega con el prefijo `> `. El encabezado de un artículo es solo la línea `Article ID:` que escribe este pedido. Sigue valiendo que solo vuelven los ids pedidos (APR-002).

---

## Dentro de alcance

- Leer el cuerpo legal y los artículos pedidos.
- Escribir un motivo por artículo con el modelo y el esfuerzo de razonamiento configurados (`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`).
- Decidir en la misma llamada la naturaleza de cada artículo y sus obligaciones explícitas con su respaldo, y cerrar en código las reglas de C9 y el criterio del cliente.
- Usar timeout y reintentos propios (`APPLICABILITY_REASONS_TIMEOUT_SECONDS`, `APPLICABILITY_REASONS_MAX_RETRIES`), más cortos que los de clasificar, para caber en la espera del backend.
- Quitar del texto que se envía al modelo las imágenes y los demás archivos embebidos como data URI.
- Neutralizar las líneas de la norma, de los artículos, de los valores y de las acciones que imitan un delimitador del mensaje o un encabezado `Article ID:` (APR-009).
- Informar siempre la versión del prompt y el uso de tokens.

## Fuera de alcance

- Armar `matched_values` y `actions`, emitir la alerta y caer a la plantilla: eso vive en `backend-cumplify` (`SuggestionAlertDraftLoader`, `AlertDraftBuilder`).
- Clasificar artículos, puntuar sugerencias o leer el perfil de la empresa.
- Guardar el motivo y el análisis. Los persiste el backend en la alerta, si la emite (pasos 0.18 a 0.20 del plan del sprint 3).

## Deuda técnica conocida

- `APPLICABILITY_PROMPT_VERSION` se sube a mano. Si un cambio del prompt no la sube, un llamador no distingue los motivos nuevos de los anteriores.
- El tope de 10 artículos y de 2000 caracteres del motivo son constantes de este servicio. El backend admite hasta 10 artículos por pedido (`IaCumplifyClient.MaxApplicabilityArticles`), aunque una alerta trae de 1 a 3, y descarta un motivo de más de 2000; si alguno de los dos lados cambia su tope y el otro no, el pedido válido para uno falla o se ignora en el otro.
- Las pruebas automáticas (`tests/`) cubren el caso de uso, el contrato HTTP y que el prompt no lleva artículos no pedidos ni un bloque de descripción de empresa. No llaman a OpenAI: el tono del motivo (no dictaminar, no citar un perfil) se calibra a mano contra el servicio (APR-011 a APR-015 en `test.csv`).
- La naturaleza, las obligaciones y sus respaldos dependen de que el modelo siga el prompt. Las pruebas automáticas fijan el texto del prompt y el cierre en código. La evaluación con artículos reales (paso 0.17: `scripts/applicability_eval.py`, casos APR-023 a APR-027 de `test.csv`) se ejecutó tres veces:
  - el 2026-10-14, con las reglas anteriores;
  - el 2026-10-15 a las 10:27, con el criterio del cliente: 14 de 15 casos. Falló la Ley 21.800, art. 4°, que salió como remisión porque nombra otro decreto, y se corrigió la regla de la remisión;
  - el 2026-10-15 a las 10:59: 15 de 15 casos (16 artículos). El pedido más lento tardó 4,7 s de los 25 s de plazo, y la ejecución costó US$0,0093.

  Un cambio del prompt pide volver a ejecutarla. El 2026-11-17 se ejecutó con `applicability-v6` (el código de `dev`) y con `applicability-v7` (el de la rama), cada una en una copia aparte del servicio: 12 de 15 casos con las dos (APR-035 de `test.csv`). Dos pedidos no llegan al modelo con ninguna, porque la alerta de la base local guardó una frase de más de 100 caracteres y el pedido da 422 (APR-025-a y APR-026-a). La Ley 21.815, art. 20 salió informativo en lugar de procedimental con las dos; esa falla ya variaba con `applicability-v4`. La Ley 21.840, art. 1 siguió informativa con la regla nueva de las prohibiciones: concede un bono, y la prohibición de retenerlo recae en las instituciones que lo pagan.
- `applicability-v7` se midió además sobre una norma entera, el Decreto N 44 de 2024 (83 artículos, 8 por pedido), con tres ejecuciones de la v6 y seis de la v7, cada versión sola (APR-036 de `test.csv`). Las reglas se ajustaron mirando esta norma, así que no es una muestra independiente:
  - artículos con obligaciones: de 52 a 56 con la v6 y 62 o 63 con la v7. Seis las tienen siempre con la v7 y nunca con la v6: los deberes de las personas trabajadoras (art. 5), las funciones del Comité Paritario (art. 47) y cuatro reglas que exigen un acto (arts. 33, 35, 41 y 42: el acta de la elección, la constitución, la designación del presidente y el secretario, y los acuerdos). Otros tres, que la v6 trae solo a veces, salen siempre (arts. 28, 39 y 45). Ninguno de los que la v6 trae siempre los pierde;
  - las reglas que solo fijan un estado o una condición de validez salen informativas en las seis ejecuciones: la composición del comité (art. 25, que la v6 trae a veces), el experto que lo integra por derecho propio (art. 38), el quórum (art. 40), la duración y el cese de los cargos (arts. 43 y 44) y la permanencia del comité (art. 48);
  - los artículos del ámbito, las definiciones, los organismos administradores y la fiscalización siguen informativos;
  - obligaciones en total: de 113 a 125 con la v6 y de 151 a 167 con la v7;
  - la naturaleza cambió entre ejecuciones en 5 de los 83 artículos con la v6 (tres ejecuciones) y en 2 con la v7 (seis): las normas sobre la votación (art. 29, con obligaciones en cinco de seis) y los Comités Permanentes (art. 49, en cuatro de seis). Los 83 artículos volvieron con análisis en todas;
  - el respaldo no filtra los deberes de conducta: el modelo nombra un registro para casi cualquier deber (para «observar una conducta de cuidado», «Registros de instrucciones y observaciones preventivas»);
  - con 8 artículos largos por pedido, el más lento tardó de 18,2 a 20,7 s con la v6 y de 16,1 a 18,4 s con la v7, de los 25 s de plazo;
  - la regla de los órganos internos se eligió entre cuatro redacciones medidas ese día, seis ejecuciones cada una. Decir que toda regla de formación, elección y funcionamiento era una obligación dejó 8 artículos cambiando entre ejecuciones; no decir nada, 9; la regla del acto, 2; y agregarle a la lista de estados «quién compone el órgano» y «cómo se vota», 6, con un deber del empleador (art. 54) informativo en dos de seis;
  - el modelo varía de un día a otro con el mismo prompt: la v4 dio de 51 a 53 artículos con obligaciones el 16 y de 52 a 55 el 17.
- Y sobre los artículos de las alertas de la IA de otras 39 normas de la base local (90 artículos, de 1 a 3 por pedido, como los manda el backend), dos ejecuciones por versión (APR-037 de `test.csv`): 82 de 90 con la misma naturaleza en las cuatro ejecuciones. Los otros 8 cambian con la v6 y quedan estables con la v7: la Ley 21.802, art. 43, que solo obliga a la municipalidad, trae obligaciones las dos veces con la v6 y es informativa con la v7; la Ley 21.830, arts. 1 a 3, que fija el monto del ingreso mínimo mensual, sale como obligación de pago una vez de dos con la v6 y nunca con la v7; la Ley 21.792, artículo único, es remisión siempre con la v7; y los tres artículos de una norma de prueba de la base local dejan de traer obligaciones. Ningún artículo cambió entre ejecuciones con la v7 (4 con la v6). El pedido más lento tardó de 7,0 a 8,6 s con la v6 y 4,3 s con la v7. Con dos ejecuciones esta muestra no separa bien las versiones: la v4 del 16 y la del 17 coinciden entre sí en 82 de 90.
- La regla de las prohibiciones (decisión 24) casi no se pudo medir: la biblioteca local tiene pocas prohibiciones dirigidas a empresas. En los seis artículos con prohibiciones de otras normas, tres ejecuciones (APR-038 de `test.csv`): cuatro no cambian, y dos de la Ley 21.802, que prohíben actos a inspectores y a personal de seguridad municipal (arts. 18 y 62), salen con obligaciones en dos de tres, aunque la prohibición recae en personal de un órgano del Estado. Ya pasaba antes de la regla nueva (una de dos el 2026-11-16). En el Decreto N 44, los artículos con prohibiciones ya traían obligaciones con la v6.
- El modelo puede colar letras de otro alfabeto en una palabra («desalиниzación», 2026-10-15). El código descarta el texto, así que la falla se ve como un motivo de plantilla o una obligación menos, no como un texto defectuoso en la alerta.
