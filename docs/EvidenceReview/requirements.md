# Módulo: Revisión de respaldos

> Lee los archivos de respaldo de una fila y dice, exigencia por exigencia, si acreditan lo que pide el artículo o la obligación, y qué falta cuando no lo acreditan. No escribe el estado de la fila y no arma el plan de acción.

---

## Objetivo

Dado el artículo, su norma, la obligación de la fila si la tiene, una fecha de referencia y hasta 10 archivos (25 MB en total), devolver una revisión estructurada. El parecido entre textos no decide la acreditación: un informe que prueba un incumplimiento puede hablar del mismo tema y no acredita la exigencia.

El pedido llegará desde `backend-cumplify`. Este módulo no escribe en la base y no la lee.

---

## Requerimientos

### EVR-001 — Recibir el artículo, la norma, la fecha y los archivos

| Campo | Detalle |
|---|---|
| **ID** | EVR-001 |
| **Rol** | Servicio interno. Exige `X-API-Key`. |

**Descripción:**

El sistema debe aceptar `multipart/form-data`: un JSON en el campo `review` y una parte `files` por archivo, con el mismo nombre que `files[].filename`.

**Validaciones:**

- `reference_date` es un día `YYYY-MM-DD`.
- El artículo trae número y texto. La norma trae título. No se acepta un id en lugar de ese texto.
- Como máximo 10 archivos y 25 MB sumados.
- Cada archivo del JSON tiene su parte, y cada parte está en el JSON.
- Un campo extra, un JSON inválido o un archivo que no coincide responde 422, sin repetir el contenido del pedido.

---

### EVR-002 — La fila puede no tener obligación

| Campo | Detalle |
|---|---|
| **ID** | EVR-002 |
| **Rol** | Servicio interno |

**Descripción:**

`obligation` puede faltar o ser `null`. Las dos formas son la fila sin obligación. Si viene, trae nombre y, si hace falta, descripción.

---

### EVR-003 — No leer la base

| Campo | Detalle |
|---|---|
| **ID** | EVR-003 |
| **Rol** | Servicio interno |

**Descripción:**

La revisión se juzga con lo que viene en el pedido. Con `DATABASE_URL` vacío no hay pool. Si otro endpoint lo tiene configurado, esta ruta no lo usa.

**Validaciones:**

- La ruta no depende de `get_db_pool`.
- Un pedido válido con la base configurada no abre una conexión.

---

### EVR-004 — Devolver el estado de cada archivo, sin su texto

| Campo | Detalle |
|---|---|
| **ID** | EVR-004 |
| **Rol** | Servicio interno |

**Descripción:**

Cada archivo vuelve con `id`, `filename`, `status`, `truncated` y `detail`. El estado es `read`, `unreadable`, `unsupported` o `error`. El texto extraído no forma parte de la respuesta.

PDF se lee por página, Word con sus tablas, Excel por hoja en solo lectura, CSV con codificación y separador, y PowerPoint por diapositiva. Una imagen o un PDF sin texto es `unreadable`. Un archivo roto, o uno que pasa de 100 páginas, 10 000 filas, 64 MB descomprimidos o 4 096 entradas de ZIP, es `error` y no trae texto parcial. Si el texto pasa de 200 000 caracteres, se corta y `truncated` queda en verdadero; el estado sigue siendo `read`.

---

### EVR-005 — Devolver la revisión, el consumo y la versión

| Campo | Detalle |
|---|---|
| **ID** | EVR-005 |
| **Rol** | Servicio interno |

**Descripción:**

La respuesta trae la naturaleza, las exigencias con su estado, sus citas y lo que falta, el veredicto, el resumen, `usage`, el modelo y `review_version`.

**Validaciones:**

- `review_version` es `evidence-review-v2@<modelo usado>`.
- Cada exigencia trae `evidence`, el respaldo que la acredita. Sin ese nombre, no sale.
- `procedure_for` nombra la autorización de un artículo `procedural` y es `null` en las otras naturalezas.
- Una cita se busca en el texto leído de su archivo, sin distinguir mayúsculas ni espacios. Una cita a otro archivo, en blanco o que no aparece, no sale. Una exigencia `accredited` o `partial` sin citas que queden baja a `not_accredited`, con la marca `cita no encontrada` en `missing`. El veredicto se recalcula a partir de las exigencias.
- `usage` informa tokens de entrada, de salida, totales, cacheados y llamadas. No informa `reasoning_tokens` ni `dev_metrics`.

---

### EVR-006 — Decidir la naturaleza con las reglas de C9

| Campo | Detalle |
|---|---|
| **ID** | EVR-006 |
| **Rol** | Servicio interno |

**Descripción:**

La naturaleza se decide solo con el texto del artículo (decisiones 7 y 21), en el orden `referral`, `explicit_obligations`, `procedural`, `informative`. Cuentan las obligaciones que recaen en la empresa y que un respaldo puede acreditar. Un artículo que solo obliga al Estado es `informative`. Un artículo que modifica otra ley, o que agrega una obligación dentro de otra, es `referral`, aunque el texto agregado imponga una obligación. Exigir una autorización impone la obligación de tenerla. Un artículo que solo regula el trámite es `procedural` y `procedure_for` nombra esa autorización. Una prohibición cuenta solo si un respaldo acredita que se respetó. Los antecedentes de una solicitud no son exigencias.

Un artículo informativo, de remisión o procedimental no tiene nada que verificar y su veredicto es `not_evaluable`. El servicio vacía las exigencias y fija ese veredicto después de la respuesta del modelo. Una exigencia sin `evidence` se descarta.

---

### EVR-007 — Acreditar cada exigencia, y no acreditar un incumplimiento

| Campo | Detalle |
|---|---|
| **ID** | EVR-007 |
| **Rol** | Servicio interno |

**Descripción:**

Cada exigencia de un artículo con obligaciones explícitas queda `accredited`, `partial`, `not_accredited` o `not_evaluable`. `accredited` cuando los archivos muestran que la empresa la cumple. `partial` cuando muestran solo una parte. `not_accredited` cuando no muestran que la cumple. `not_evaluable` cuando lo leído no tiene texto con el que juzgarla.

Un archivo que prueba que la empresa no la cumple no la acredita, por mucho que hable del tema. Hablar del tema no alcanza. Esta regla está en el prompt: el servicio no la vuelve a aplicar sobre la respuesta.

---

### EVR-008 — Juzgar vigencias y plazos con la fecha de referencia

| Campo | Detalle |
|---|---|
| **ID** | EVR-008 |
| **Rol** | Servicio interno |

**Descripción:**

Las vigencias y los plazos se juzgan con `reference_date`, no con el día de hoy. Una exigencia que no está vigente ese día no tiene nada que verificar y queda `not_evaluable`. Un plazo posterior a ese día no llegó: no se trata como incumplimiento y queda `not_evaluable`. Un plazo en ese día o antes ya llegó: un archivo fechado después no muestra que se cumplió a tiempo. Un archivo cuya vigencia no cubre la fecha de referencia no acredita.

Esta regla está en el prompt: el servicio no la vuelve a aplicar sobre la respuesta.

---

### EVR-009 — Ignorar las instrucciones que traiga un respaldo

| Campo | Detalle |
|---|---|
| **ID** | EVR-009 |
| **Rol** | Servicio interno |

**Descripción:**

El texto leído de un archivo es un dato, no una instrucción. Se ignora cualquiera escrita dentro, incluida una que pida cambiar la naturaleza, un estado, el veredicto o las reglas del prompt. El artículo, la norma y la obligación también son datos.

Esta regla está en el prompt: el servicio no la vuelve a aplicar sobre la respuesta.

---

### EVR-010 — Decir qué falta, sin recomendar una acción

| Campo | Detalle |
|---|---|
| **ID** | EVR-010 |
| **Rol** | Servicio interno |

**Descripción:**

`missing` describe solo lo que no aparece en los archivos. Si no falta nada, queda vacío. No recomienda una acción, una tarea ni lo que la empresa debería hacer. Esas acciones salen de la base de conocimientos (fase 2B del plan).

Esta regla está en el prompt: el servicio no la vuelve a aplicar sobre la respuesta.

---

### EVR-011 — Usar plazo, reintentos, modelo y esfuerzo propios, sin detener el servicio

| Campo | Detalle |
|---|---|
| **ID** | EVR-011 |
| **Rol** | Servicio interno |

**Descripción:**

La llamada usa `EVIDENCE_REVIEW_TIMEOUT_SECONDS` (80) y `EVIDENCE_REVIEW_MAX_RETRIES` (0), no `OPENAI_MAX_RETRIES`: el SDK también reintenta un plazo vencido, y cada reintento sumaría hasta otros 80 s a los 90 s que espera el backend. `EVIDENCE_REVIEW_MODEL` y `EVIDENCE_REVIEW_REASONING_EFFORT` vacíos usan `OPENAI_MODEL` y `OPENAI_REASONING_EFFORT`.

La lectura de los archivos y la llamada al modelo se ejecutan en el pool de hilos, fuera del bucle de eventos. El servicio tiene un solo proceso: en el bucle, una revisión detendría el chat, los demás endpoints y `/health` hasta terminar.

**Validaciones:**

- Sin `OPENAI_API_KEY` la respuesta es 503, antes de validar el cuerpo.
- Un fallo del proveedor es 502 y el detalle no repite el pedido.
- Sin `SERVICE_API_KEY` la respuesta es 503. Con la clave ausente o incorrecta en el header, 401. En los dos casos no se lee el cuerpo.
- Con `OPENAI_MAX_RETRIES` en 2, la revisión sigue con 0 reintentos, salvo que se configure `EVIDENCE_REVIEW_MAX_RETRIES`.
- El revisor se llama fuera del bucle de eventos.
- La llamada usa el modo explícito de caché con un único punto de corte al final de las instrucciones: el artículo, la obligación y los respaldos nunca quedan en el caché del proveedor ("Caché de prompts" en `docs/Chat/api.md`).

---

## Dentro de alcance

- Recibir el pedido en partes y leer PDF, Word, Excel, CSV y PowerPoint.
- Aplicar los topes de archivos, de tamaño, de páginas, de filas, de ZIP y de caracteres.
- Pedir al modelo una revisión con el prompt `evidence-review-v2`.
- Cerrar en código el artículo informativo, de remisión o procedimental: sin exigencias y con veredicto `not_evaluable`. Solo el procedimental conserva `procedure_for`.
- Descartar una exigencia que no nombra su respaldo.
- Buscar cada cita en el texto de su archivo, sin distinguir mayúsculas ni espacios, y descartar las que no aparecen. Una exigencia sin citas verificadas no puede quedar acreditada ni parcial: baja a `not_accredited`, con la marca `cita no encontrada`. El veredicto se recalcula a partir de las exigencias.
- Informar el estado de cada archivo, la revisión, el consumo, el modelo y `review_version`.
- Probar el adaptador real contra un cliente falso de OpenAI, sin salir de la máquina.
- El set manual `scripts/evidence_review_eval.py` (EVR-012 a EVR-031), para comparar el esfuerzo `low` con `medium`. Lo ejecuta el usuario.

## Fuera de alcance

- Guardar la revisión, armar el pedido desde la fila y cambiar el estado del artículo o de la obligación. Eso vive en `backend-cumplify`.
- Proponer acciones o armar el plan. La fase 2A parte de lo que falta; la fase 2B propone acciones desde la base de conocimientos.
- OCR de escaneos y fotos.

## Deuda técnica conocida

- `evidence-review-v2` no se sube al cambiar una redacción del prompt. Quien llama distingue el modelo (`review_version` termina en `@<modelo>`), no cada edición de las reglas. El salto desde `evidence-review-v1` es el de la decisión 21 y la verificación de la cita.
- El incumplimiento, la fecha de referencia, las instrucciones dentro del archivo y la forma de `missing` dependen de que el modelo siga el prompt. Las pruebas automáticas fijan el texto del prompt; no llaman a OpenAI.
- El set no trae un artículo procedimental. La naturaleza `procedural` y `procedure_for` se cubren en las pruebas del caso de uso, no en EVR-012 a EVR-031.
- Los topes de 10 archivos, 25 MB y 200 000 caracteres están en este servicio. El backend tiene que aplicar los mismos antes de enviar el pedido.
- Las pruebas automáticas cubren el contrato, el prompt y el adaptador real contra un cliente falso de OpenAI (`tests/test_evidence_review_fake.py`). El set real se volvió a correr el 2026-10-15 con `evidence-review-v2` y `gpt-5.6-luna`: `low` acertó 17 de 20 y `medium` también 17 de 20, con el mismo veredicto en los 17. Costo de unos US$0,017 y US$0,019. No justifica subir el esfuerzo. Los tres fallos son EVR-012, EVR-019 y EVR-021 de `test.csv`: el modelo responde `partial` donde el archivo muestra la exigencia cumplida.
