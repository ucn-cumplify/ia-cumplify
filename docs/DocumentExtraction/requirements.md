# Módulo: Extracción de documentos

> Expone en un endpoint propio la extracción de texto de la revisión de respaldos, para que `backend-cumplify` indexe la base de conocimientos del plan de acción. No indexa, no guarda y no llama al modelo.

---

## Objetivo

El plan del sprint 3 de `backend-cumplify` pide, en el paso 2B.7: «Exponer la extracción del paso 1.2 en un endpoint propio, `POST /api/v1/documents/extract`, para indexar la base». El backend indexa cada documento como un `KnowledgeBaseDocument` con un solo `Text` y un `Title` (paso 2B.8). Este módulo le entrega ese texto.

El contrato es provisional: la carga de la base (paso 2B.6) sigue abierta y todavía no hay un cliente.

**Origen de cada regla.** Vienen del plan: la ruta, reutilizar la extracción de 1.2 y su fin (indexar la base). Vienen del código existente: los formatos, los estados y los topes del extractor (EVR-004). Son decisiones técnicas de este paso, acordadas con el equipo para el contrato provisional: un archivo por pedido, `text` y `fragments`, la regla de consolidación, rechazar un documento cortado, contar el tope en UTF-16, los textos fijos de `detail` y 25 MB por archivo.

---

## Requerimientos

### DOC-001 — Recibir un documento

| Campo | Detalle |
|---|---|
| **ID** | DOC-001 |
| **Rol** | Servicio interno. Exige `X-API-Key`. |

**Descripción:**

El sistema debe aceptar `multipart/form-data` con una sola parte `file`. El nombre subido es obligatorio, se reduce a su último segmento y tiene hasta 255 caracteres. El archivo tiene hasta 25 MB; la lectura se corta al pasarlo y no conserva lo que sobra. Cualquier otro pedido es 422, con un mensaje que no repite el contenido.

---

### DOC-002 — Devolver el estado, el texto y los fragmentos

| Campo | Detalle |
|---|---|
| **ID** | DOC-002 |
| **Rol** | Servicio interno |

**Descripción:**

El documento vuelve con `filename`, `status`, `detail`, `text` y `fragments`. Los estados son los del extractor: `read`, `unreadable`, `unsupported` y `error`. Solo `read` trae texto. En los demás, `text` es `null` y `fragments` está vacío, para que nada se indexe por accidente.

`fragments` conserva las páginas, hojas o diapositivas que tienen texto, con su ubicación original. `text` es exactamente sus textos, en orden, unidos con `"\n\n"`. Un surrogate suelto pasa a U+FFFD en los dos campos, como en el chat.

---

### DOC-003 — No devolver un documento cortado

| Campo | Detalle |
|---|---|
| **ID** | DOC-003 |
| **Rol** | Servicio interno |

**Descripción:**

En la revisión de respaldos, el texto que pasa de 200 000 caracteres se corta con aviso. Aquí, un documento cortado es `error`, sin texto parcial: el backend no puede distinguir medio documento de uno completo, y el hash del texto cortado nunca lo volvería a embeber.

Además se cuenta `text` en unidades UTF-16, como `string.Length` del backend. Un carácter fuera del plano básico (un emoji) cuenta dos. Si pasa de 200 000, también es `error`. Ese es el máximo por documento del backend: manda todos los fragmentos de un documento en un pedido de embeddings de hasta 200 000 caracteres.

---

### DOC-004 — No exponer el contenido en errores ni en el log

| Campo | Detalle |
|---|---|
| **ID** | DOC-004 |
| **Rol** | Servicio interno |

**Descripción:**

`detail` es uno de dos textos fijos. El del extractor no se devuelve: es el mensaje de la excepción del parser, y puede citar el archivo (un DOCX dañado devuelve el nombre de la entidad que no está definida). El log registra los bytes, el estado y la cantidad de fragmentos, nunca el nombre ni el texto.

---

### DOC-005 — No detener el servicio ni llamar al modelo

| Campo | Detalle |
|---|---|
| **ID** | DOC-005 |
| **Rol** | Servicio interno |

**Descripción:**

La lectura se ejecuta en el pool de hilos. El endpoint no usa OpenAI ni exige `OPENAI_API_KEY`, y no lee la base.

---

## Dentro de alcance

- `POST /api/v1/documents/extract` con el extractor y los topes existentes.
- La consolidación de `text`, el rechazo del documento cortado y el tope en UTF-16.
- Pruebas del endpoint en `tests/test_document_extraction_http.py`.

## Fuera de alcance

- Cargar, guardar, versionar o indexar la base: pasos 2B.6 y 2B.8, en `backend-cumplify`.
- Cambiar el extractor o la revisión de respaldos.
- OCR de escaneos y fotos (decisión 13 del plan).

## Deuda técnica conocida

Pendiente para el sprint de testing; nada de esto se corrigió en este paso.

- **El `detail` del extractor puede citar el archivo.** `_error_detail` devuelve el mensaje de la excepción (hasta 500 caracteres). Este endpoint no lo devuelve (DOC-004), pero la revisión de respaldos sí, y `backend-cumplify` lo guarda en el `jsonb` de los archivos de la revisión. Contradice EVR-004 («sin el contenido»).
- **Topes provisionales para la base.** 100 páginas, 10 000 filas y 25 MB vienen de los respaldos. Un manual puede pasarlos. El de 200 000 caracteres coincide con el backend: subirlo exige cambiar a la vez `EmbeddingRequestPlanner.MaxChars`.
- **`detail` no distingue la causa del `error`.** Roto, cifrado o fuera de un tope dan el mismo texto. Si el paso 2B.6 necesita la causa, hace falta un código por causa.
- **Word y las páginas de PDF unen sus líneas con un solo `\n`.** El backend toma un salto suelto como un espacio, así que un Word entero queda como un único párrafo y se corta por oraciones. Cambiarlo en el extractor cambia también el texto de la revisión. Evaluarlo con la base real.
- **El formato se elige por la extensión.** No se mira el contenido. Un archivo con la extensión equivocada queda `error` o `unsupported`.
- **Starlette recibe el cuerpo entero antes del endpoint.** El tope de 25 MB se aplica al leer la parte, no al recibir el pedido. Igual que en la revisión de respaldos.
- **Lo que debe respetar el backend en el paso 2B.6:**
  - indexar solo `read`;
  - usar `text` tal como llega: otra unión cambia el hash de kb-v1 y vuelve a embeber;
  - asignar `Id` y `Title`;
  - enviar un archivo por pedido;
  - no guardar ni registrar el cuerpo de la respuesta ni de un error;
  - definir su propio plazo.
- **Sin prueba de punta a punta.** No hay cliente en el backend ni base del cliente.
