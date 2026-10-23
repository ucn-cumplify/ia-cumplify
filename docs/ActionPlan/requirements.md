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

- **El contrato se copia del backend.** Sale de la PR #225 de backend-cumplify, fusionada en `capstone-2s` el 2026-10-23. Si el backend cambia el JSON, este módulo tiene que cambiar con él. `AI_ACTION_PLAN_ENABLED` sigue en `false` por defecto en el backend, así que la llamada no sale hasta que se active.
- **`ACTION_PLAN_MAX_FINDINGS` (30) no está acordado.** El backend manda todos los hallazgos abiertos de la revisión sin tope, con hasta 5 fragmentos de 4.000 caracteres cada uno: 30 hallazgos son unos 600.000 caracteres de fragmentos en el peor caso. Un pedido con más responde 422, y el backend lo muestra como «No se pudo proponer el plan de acción.». Hay que acordar el valor y que el backend lo aplique antes de llamar.
- **Los topes de entrada copian valores del backend** (`FragmentsPerFinding` = 5, kb-v1 = 4.000 caracteres, descripción del hallazgo = 2.000). Si el backend sube uno, este servicio responde 422 hasta que se suba aquí también. La exigencia y lo que falta salen de la revisión guardada, que no tiene tope: 20.000 es un valor propio.
- **Un fragmento compartido viaja una vez por hallazgo.** Si varios hallazgos reciben el mismo fragmento, el mensaje lo repite y se paga cada vez.
- **`MAX_TASKS_PER_FINDING` (5) es propio de este servicio.** El backend no tiene tope de tareas.
- **La pertinencia de la cita depende del modelo.** El servicio comprueba que la cita esté en el fragmento, no que respalde la tarea. Lo mide el set de APL-009.
- **El reparto de `priority` depende del prompt.** Las reglas de `alta`, `media` y `baja` son una propuesta de este servicio y no se acordaron con el cliente.
- **`tests/action_plan_backend.py` es una copia en Python de `ActionPlanResponseReader`.** Si el lector cambia en el backend, hay que cambiarla.
- Las pruebas automáticas cubren el contrato, la verificación, el prompt y el adaptador real contra un cliente falso de OpenAI. No hay pruebas de punta a punta con el backend ni con OpenAI.
