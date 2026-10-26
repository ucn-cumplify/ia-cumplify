# Módulo: Propuesta del plan de acción

> Propone tareas para los hallazgos de una revisión de respaldos, solo con los fragmentos de la base de conocimientos que manda el backend, y cada tarea cita el suyo. No guarda la propuesta, no crea las tareas y no busca en la base.

---

## Objetivo

Paso 2B.9 del plan del sprint 3 de backend-cumplify (`docs/AI/plan-sprint-3.md`). Cuando la revisión encuentra exigencias no acreditadas, el usuario registra hallazgos y arma el plan de acción (fase 2A). Las acciones que proponga la IA tienen que salir de la base de conocimientos que entrega el cliente (decisión 18 del plan): sin esa base, el plan no tendría respaldo ni se podría comprobar.

El pedido llega desde `POST /legal-requirements/evidence-reviews/{reviewId}/action-plan-proposal` del backend (paso 2B.10), que busca en la base los fragmentos más cercanos a cada hallazgo abierto y guarda la propuesta. Este módulo no lee ni escribe la base.

---

## Requerimientos

### APL-001 — Recibir los hallazgos y sus fragmentos con el contrato del backend

| Campo | Detalle |
|---|---|
| **ID** | APL-001 |
| **Rol** | Servicio interno. Exige `X-API-Key`. |

**Descripción:**

El sistema debe aceptar el JSON que arma `IaCumplifyClient.ProposeActionPlanAsync`: `prompt_version` y `findings`, cada hallazgo con `id`, `description`, `requirement`, `missing` y `fragments`, y cada fragmento con `document_id`, `chunk_index`, `locator` y `text`. Los opcionales pueden llegar en `null`, como los manda System.Text.Json.

**Validaciones:**

- `prompt_version` es `action-plan-v1`.
- Entre 1 y `ACTION_PLAN_MAX_FINDINGS` hallazgos, sin repetir el `id`. Hasta 5 fragmentos por hallazgo.
- Los ids son UUID distintos del nulo. `chunk_index` es un entero de 0 o más.
- Los topes de `api.md`: descripción 2.000, exigencia y faltante 20.000, fragmento 4.000 y localizador 1.000 caracteres.
- Un campo extra, un JSON inválido o un tope pasado responde 422, sin repetir el contenido del pedido.

---

### APL-002 — Proponer tareas solo desde los fragmentos del hallazgo

| Campo | Detalle |
|---|---|
| **ID** | APL-002 |
| **Rol** | Servicio interno |

**Descripción:**

El prompt `action-plan-v1` pide al modelo tareas que salgan solo de los fragmentos de cada hallazgo, sin su propio conocimiento ni los fragmentos de otro hallazgo. Cada tarea trae un nombre breve en español, una descripción y una prioridad `alta`, `media` o `baja`. Las instrucciones escritas dentro de un fragmento o de un hallazgo se ignoran.

**Validaciones:**

- El prompt contiene esas reglas (`tests/test_action_planner.py`).
- Los data URI embebidos se quitan antes de armar el mensaje.

---

### APL-003 — Ninguna tarea sin una cita verificada

| Campo | Detalle |
|---|---|
| **ID** | APL-003 |
| **Rol** | Servicio interno |

**Descripción:**

Cada tarea cita un fragmento con `document_id`, `chunk_index` y un texto literal. El servicio la deja solo si el fragmento es de ese hallazgo y el texto aparece en él sin distinguir mayúsculas ni espacios, con la misma comparación que `ActionPlanResponseReader` (`ToLowerInvariant`, no `casefold`).

**Validaciones:**

- Una cita parafraseada, en blanco, a otro hallazgo, o a un documento o una parte que no se mandó, deja la tarea fuera.
- Una diferencia solo de mayúsculas o espacios no la deja fuera.

---

### APL-004 — Un hallazgo sin respaldo vuelve sin tareas

| Campo | Detalle |
|---|---|
| **ID** | APL-004 |
| **Rol** | Servicio interno |

**Descripción:**

Un hallazgo sin fragmentos, uno cuyas tareas no tienen una cita válida y uno que el modelo omite vuelven con `tasks: []`. Es la marca «sin respaldo en la base de conocimientos» que el backend lee: el contrato no tiene otro campo para eso.

**Validaciones:**

- Un hallazgo sin fragmentos no se manda al modelo.
- Si ningún hallazgo tiene fragmentos, no se llama al modelo, el consumo es 0 y la respuesta igual trae `model` y `proposal_version`, sin los cuales el backend no guarda la propuesta.
- La respuesta trae cada hallazgo del pedido una vez, en su orden.

---

### APL-005 — Ajustar los campos a lo que guarda el backend

| Campo | Detalle |
|---|---|
| **ID** | APL-005 |
| **Rol** | Servicio interno |

**Descripción:**

El servicio aplica a cada tarea los topes de `ActionPlanResponseReader` antes de responder: nombre de 1 a 255 caracteres, o la tarea queda fuera; descripción de hasta 2.000, o `null`; prioridad del catálogo en minúscula, o `null`. Los topes se cuentan en unidades UTF-16, como .NET. Quedan las 5 primeras tareas válidas por hallazgo.

**Validaciones:**

- Toda tarea que el servicio devuelve la conserva el lector del backend, con los mismos campos (`tests/action_plan_backend.py` reproduce el lector).

---

### APL-006 — Devolver el consumo, el modelo y la versión

| Campo | Detalle |
|---|---|
| **ID** | APL-006 |
| **Rol** | Servicio interno |

**Descripción:**

`usage` trae `prompt_tokens`, `completion_tokens`, `total_tokens`, `cached_tokens` y `llm_calls`, con el formato de la revisión de respaldos. El backend lo registra como la tarea `action_plan` de `ai_usage`. `proposal_version` es `action-plan-v1@<modelo>` y `model` es el modelo usado.

---

### APL-007 — Plazo, reintentos, modelo y esfuerzo propios

| Campo | Detalle |
|---|---|
| **ID** | APL-007 |
| **Rol** | Servicio interno |

**Descripción:**

La llamada usa `ACTION_PLAN_TIMEOUT_SECONDS` (80) y `ACTION_PLAN_MAX_RETRIES` (0), bajo los 90 s que espera el backend. `ACTION_PLAN_MODEL` y `ACTION_PLAN_REASONING_EFFORT` vacíos usan `OPENAI_MODEL` y `OPENAI_REASONING_EFFORT`. Usa el modo explícito de caché con un único corte al final de las instrucciones.

**Validaciones:**

- Sin `OPENAI_API_KEY`, 503. Un fallo del proveedor o una respuesta sin propuesta, 502, sin repetir el pedido.
- Con `OPENAI_MAX_RETRIES` en 2, la propuesta sigue con 0 reintentos.

---

### APL-008 — No leer la base ni registrar textos de la empresa

| Campo | Detalle |
|---|---|
| **ID** | APL-008 |
| **Rol** | Servicio interno |

**Descripción:**

El endpoint no depende de `get_db_pool`. Los hallazgos y los fragmentos son datos de la empresa: el log solo cuenta hallazgos, fragmentos y tareas, y nombra `proposal_version` o el tipo de error.

---

### APL-009 — Evaluar la propuesta con la base del cliente

| Campo | Detalle |
|---|---|
| **ID** | APL-009 |
| **Rol** | Usuario que ejecuta la evaluación |

**Descripción:**

El plan pide un set de evaluación con casos de la base del cliente. Queda pendiente hasta que llegue la base: tiene que medir si las tareas propuestas cierran el hallazgo, si las citas son pertinentes y no solo literales, y si un hallazgo sin respaldo queda sin tareas en lugar de recibir una inventada. Lo ejecuta el usuario, porque llama a OpenAI.

---

## Dentro de alcance

- Validar el pedido con el contrato de la PR #225 de backend-cumplify y los topes de `api.md`.
- Pedir al modelo una propuesta estructurada con el prompt `action-plan-v1`.
- Verificar cada cita contra los fragmentos del hallazgo y ajustar los campos a lo que guarda el backend.
- Devolver `tasks: []` para un hallazgo sin respaldo, sin llamar al modelo si no tiene fragmentos.
- Probar el adaptador real contra un cliente falso de OpenAI, sin salir de la máquina.

## Fuera de alcance

- Buscar los fragmentos, cargar o indexar la base (pasos 2B.6 y 2B.8) y guardar la propuesta (2B.10). Eso vive en `backend-cumplify`.
- Extraer el texto de los documentos de la base (paso 2B.7, `POST /api/v1/documents/extract`).
- El botón «Proponer con la base de conocimientos» del frontend (paso 2B.11).
- Las fechas y el responsable de las tareas: los completa el usuario al confirmar el plan.

## Deuda técnica conocida

Cada punto lleva su estado: **Pendiente** (falta una decisión, una implementación o una validación), **Mitigado** (hay una medida temporal, pero el problema sigue) o **Resuelto** (implementado y verificado).

**Pendiente**

- **`ACTION_PLAN_MAX_FINDINGS` (30) es incompatible con el backend.** Documentarlo no lo resuelve.
  - *Problema:* el backend manda todos los hallazgos abiertos de la revisión, sin tope, y no valida este límite antes de embeberlos.
  - *Impacto:* con más de 30, este servicio responde 422 y el backend muestra «No se pudo proponer el plan de acción.» después de haber pagado los embeddings. El usuario no tiene cómo seguir.
  - *Se necesita:* acordar el valor con el backend, medido con el caso APL-017 de `test.csv`, y que el backend lo aplique antes de embeber, con un mensaje propio. Repartir los hallazgos en varias llamadas sería un cambio aparte.
  - *Resuelto cuando:* el backend rechace o reparta un pedido por encima del tope antes de embeber, los dos lados usen el mismo valor y una prueba de cada lado lo fije.
- **Topes propios que el backend no aplica.** `requirement` y `missing` (20.000) y `locator` (1.000) son valores de este servicio: el backend los manda sin recortar. Un solo campo más largo rechaza el pedido entero con 422. *Se necesita:* confirmar el largo máximo que puede mandar el backend, o acordar quién recorta. *Resuelto cuando:* el tope esté acordado con el backend o el backend no pueda mandar más.

**Mitigado**

- **`AI_ACTION_PLAN_ENABLED` en `false` en el backend** es una medida de control: apagada, el backend responde 503 y no llama a este servicio, así que la propuesta no se activa por accidente. No resuelve la incompatibilidad del tope de hallazgos: al encenderla, el problema sigue igual.
- **El contrato y el lector están copiados del backend.** Los topes de entrada (`FragmentsPerFinding` = 5, kb-v1 = 4.000 caracteres, descripción del hallazgo = 2.000) y `tests/action_plan_backend.py`, la copia en Python de `ActionPlanResponseReader`, coinciden hoy con la PR #225, y las pruebas leen cada respuesta con esa copia. Si el backend cambia uno, este servicio responde 422 o las pruebas siguen pasando con el lector viejo: hay que cambiarlo aquí en el mismo momento.

**Resuelto**

- **El contrato ya no sale de una PR abierta.** La PR #225 de backend-cumplify se fusionó en `capstone-2s` el 2026-10-23, y el pedido, la respuesta y los topes se revisaron contra su commit de merge ese mismo día.

**Por confirmar** (valores fijados por el equipo o por este servicio, sin acuerdo registrado con el cliente)

- **Un hallazgo sin respaldo vuelve sin tareas.** El plan del sprint 3 lo deja entre los valores por defecto a confirmar. La alternativa es proponer tareas con el conocimiento del modelo, marcadas como tales.
- **El reparto de `priority`.** Las reglas de `alta`, `media` y `baja` son una propuesta de este servicio; el cliente no las revisó.
- **`MAX_TASKS_PER_FINDING` (5).** El backend no tiene tope de tareas.

**Limitaciones de diseño**

- **Un fragmento compartido viaja una vez por hallazgo.** Si varios hallazgos reciben el mismo fragmento, el mensaje lo repite y se paga cada vez.
- **La pertinencia de la cita depende del modelo.** El servicio comprueba que la cita esté en el fragmento, no que respalde la tarea. Lo mide el set del requisito APL-009.

**Pruebas pendientes**

- Las pruebas automáticas cubren el contrato, la verificación, el prompt y el adaptador real contra un cliente falso de OpenAI. Faltan la prueba de punta a punta con el backend y las que llaman a OpenAI: los casos APL-016 a APL-019 de `test.csv`, que siguen en «Pendiente». El set del requisito APL-009 espera la base del cliente.
