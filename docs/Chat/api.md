# API — Módulo: Chat

> Base path: `/api/v1/chat`

Exige el header `X-API-Key` con el valor de `SERVICE_API_KEY`. No usa la base de datos: funciona aunque `DATABASE_URL` esté vacía. No guarda estado: cada pedido trae todo lo que el modelo necesita.

Es el contrato de las tareas 4.1, 4.2 y 4.3 del plan del equipo (`docs/AI/plan-chatbot-rag.pdf` de `backend-cumplify`, rama `capstone-2s`). Los demás números, como (4.5) o "tarea 4.10", también son puntos de ese plan (4.5: fase 4, punto 5). Lo que falta acordar con el backend, el frontend o el equipo está al final, en "Pendiente de acordar".

---

## Responder una pregunta del chat

```text
POST /api/v1/chat
```

**Descripción:** Recibe la pregunta del usuario, los mensajes recientes de la conversación y los pasajes que el backend recuperó para este turno, y devuelve la respuesta en streaming (Server-Sent Events), citando el pasaje en que se apoya cada afirmación. El backend recupera los pasajes (4.5), guarda la conversación (4.4) y reenvía el stream al navegador (4.7). Este endpoint no busca nada por su cuenta: responde solo con lo que recibe.

**Requerimiento relacionado:** CHT-001 a CHT-014

**Request:**

```json
{
  "question": "¿Qué exige la Ley 16.744 sobre el comité paritario?",
  "history": [
    { "role": "user", "content": "¿Qué obligaciones de seguridad tenemos registradas?" },
    { "role": "assistant", "content": "La app registra la obligación de constituir el Comité Paritario [1]." }
  ],
  "passages": [
    {
      "id": "0b6f3d2e-5c1a-4f7e-9d84-2a1c7e9b4f10",
      "kind": "article",
      "reference": "Ley 16.744, art. 66",
      "text": "(texto del artículo 66)"
    },
    {
      "id": "7c2e9a41-3b8d-4e6f-a5c0-9d1f2b3e4a57",
      "kind": "obligation",
      "reference": "Obligación: Constituir el Comité Paritario",
      "text": "(descripción de la obligación)"
    }
  ],
  "context": { "app_name": "Planta Quilicura" }
}
```

Ejemplo ilustrativo: los textos entre paréntesis reemplazan el contenido real.

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| question | string | Sí | La pregunta del usuario. Se recorta y debe quedar entre 1 y `CHAT_QUESTION_MAX_CHARS` caracteres (defecto 2.000) |
| history | lista de mensajes | No | Mensajes anteriores de la conversación, del más antiguo al más reciente, sin la pregunta actual. Defecto: lista vacía. Hasta `CHAT_HISTORY_MAX_MESSAGES` mensajes (defecto 6: alcanza para tres preguntas con sus respuestas) y `CHAT_HISTORY_MAX_CHARS` caracteres en total (defecto 8.000). ia no exige que los roles alternen: el historial puede empezar con `assistant`, terminar en `user` o tener mensajes seguidos del mismo rol, y llega al modelo en el orden recibido |
| history[].role | string | Sí | `user` o `assistant`. Cualquier otro valor, incluido `system`, se rechaza |
| history[].content | string | Sí | El texto del mensaje. Se recorta; un mensaje vacío después del recorte se descarta antes de contar |
| passages | lista de pasajes | Sí | Lo que el backend recuperó para este turno, del más relevante al menos. Puede ir vacía. Hasta `CHAT_MAX_PASSAGES` (defecto 12) |
| passages[].id | string | Sí | Id opaco del backend, de 1 a 100 caracteres, en una línea y único en el pedido. No se recorta. ia no lo interpreta: lo devuelve tal cual en las citas |
| passages[].kind | string | Sí | Tipo de fuente, de una lista cerrada (abajo) |
| passages[].reference | string | Sí | Referencia legible, por ejemplo `Ley 16.744, art. 66`. Se recorta y debe quedar entre 1 y 200 caracteres, en una línea. El modelo la usa para nombrar la fuente en el texto |
| passages[].text | string | Sí | El contenido del pasaje. Se recorta y debe quedar entre 1 y `CHAT_PASSAGE_MAX_CHARS` caracteres (defecto 6.000) |
| context | objeto | No | Contexto de la conversación |
| context.app_name | string | No | Nombre de la app de Requisitos Legales desde la que se abrió el chat (modo de contexto, tarea 4.10). Se recorta y debe quedar entre 1 y 200 caracteres, en una línea. Se omite si el chat no se abrió desde una app |

- La suma de los `passages[].text` no puede pasar de `CHAT_PASSAGES_MAX_TOTAL_CHARS` (defecto 48.000).
- Los largos se miden sobre el texto recibido, después del recorte en los campos que se recortan, y antes de quitar las imágenes base64. El backend tiene que mandar los pasajes sin imágenes: una imagen embebida ocupa el tope sin aportar texto.
- Un carácter es un punto de código Unicode: lo que cuentan `len` de Python y `max_length` de Pydantic. `string.Length` de .NET cuenta unidades UTF-16 y nunca da menos, así que sirve como espejo conservador. Los grafemas (`StringInfo`) pueden dar menos, por ejemplo con tildes combinadas (texto en NFD), y no sirven como espejo.
- Recortar es quitar de los extremos los espacios en blanco de Unicode (propiedad White_Space), que son los que quitan `string.Trim()` de .NET y `strip_whitespace` de Pydantic. ia no usa `str.strip()` de Python, que quita además U+001C a U+001F, para que el backend pueda espejar el recorte con `Trim()`.
- "En una línea" significa sin ningún salto de línea Unicode: ni U+000A a U+000D, ni U+001C a U+001E, ni U+0085, U+2028 o U+2029 (los que corta `str.splitlines()` de Python). En `reference` y `app_name` se comprueba después del recorte: un salto al final se quita y no da 422, salvo U+001C a U+001E, que no son espacios en blanco. En `id`, que no se recorta, todo salto da 422.
- `null` en un campo opcional (`history`, `context`, `context.app_name`) equivale a omitirlo: `history: null` es la lista vacía, y `context: null`, `context: {}` o `context.app_name: null` indican que el chat no se abrió desde una app. En un campo requerido, `null` da 422.
- Los campos que no están en esta tabla se rechazan con 422 (ver "Pendiente de acordar").

Valores de `kind` (la lista es cerrada; los valores se confirman con el backend y el frontend, ver "Pendiente de acordar"):

| `kind` | Qué es |
|---|---|
| `article` | Un artículo, o un trozo de artículo, de una norma de la biblioteca |
| `legal_body` | Los datos de una norma completa (título, tipo, número, resumen), por ejemplo desde la búsqueda de texto completo |
| `legal_requirement` | Los datos de una app de Requisitos Legales de la empresa |
| `vinculation` | Una vinculación de una app (`LegalRequirementVinculation` del backend): el artículo u obligación vinculado, con su estado, criticidad y avance de cumplimiento |
| `obligation` | Una obligación de la biblioteca |

A ia-cumplify el `kind` solo le sirve para presentarle el pasaje al modelo ("Artículo", "Norma", "Obligación"…). La lista cerrada fija qué tipos de cita tiene que saber mostrar el frontend.

---

**Response `200 OK`:**

Cabeceras:

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
X-Accel-Buffering: no
```

Cuerpo (ejemplo ilustrativo; los tokens y los tiempos no son medidos, y en la realidad cada `delta` trae unas pocas palabras):

```text
: ping

event: delta
data: {"text":"La Ley 16.744 exige constituir un Comité Paritario de Higiene y Seguridad en toda faena, sucursal o agencia con más de 25 trabajadores [1]."}

event: delta
data: {"text":"\n\nLa app ya registra la obligación de constituirlo [2]."}

event: done
data: {"citations":[{"n":1,"id":"0b6f3d2e-5c1a-4f7e-9d84-2a1c7e9b4f10"},{"n":2,"id":"7c2e9a41-3b8d-4e6f-a5c0-9d1f2b3e4a57"}],"coverage":"answered","citations_dropped":0,"finish_reason":"stop","usage":{"prompt_tokens":3480,"completion_tokens":410,"total_tokens":3890,"cached_tokens":0,"reasoning_tokens":96,"llm_calls":1},"chat_version":"chat-v3@gpt-5.6-luna","dev_metrics":{"elapsed_ms":5230.4,"first_delta_ms":1840.2}}
```

El `: ping` del principio aparece solo si pasan 15 s sin que ia emita un evento, por ejemplo mientras el proveedor no responde o el modelo razona. FastAPI lo cuenta desde el último evento del generador de ia, no desde los fragmentos del proveedor.

**Eventos:**

| Evento | Cuántas veces | `data` |
|---|---|---|
| `delta` | Cero o más, antes del evento final | `{"text": "..."}`: un fragmento de la respuesta. La respuesta es la concatenación de todos los `text`, en orden |
| `done` | Como máximo una, al final | La respuesta terminó. Campos abajo |
| `error` | Como máximo una, al final | La respuesta no se pudo completar. Campos abajo |
| `: ping` | Cada 15 s sin eventos | Comentario SSE, sin `event` ni `data`. Solo mantiene viva la conexión |

**Reglas del stream:**

- **Exactamente un evento final:** `done` o `error`. Si la conexión termina sin ninguno de los dos, la respuesta se cortó.
- `done` siempre llega después de al menos un `delta`. `error` puede llegar sin ningún `delta` antes.
- **Después del 200, el código HTTP no cambia.** ia manda las cabeceras sin esperar la respuesta del proveedor, así que las fallas del proveedor (429, 5xx, timeout o clave de OpenAI rechazada) llegan como `event: error` dentro del 200, salvo una falla posterior a `finish_reason`, que cierra como un final normal (ver "Errores durante el stream"). Los únicos códigos de error reales son los de "Errores antes del stream".
- `data` es una sola línea de JSON en UTF-8. Puede traer caracteres escapados (`"Seg\u00fan"`) o sin escapar (`"Según"`), según cómo se serialice: hay que leerlo con un parser JSON, nunca compararlo como texto.
- El texto no trae surrogates sueltos (U+D800 a U+DFFF): ia los reemplaza por U+FFFD antes de emitirlo. Escapado en el JSON, un surrogate suelto no lo lee System.Text.Json, y el backend daría la respuesta por cortada.
- No se usan `id:` ni `retry:`. Un stream cortado no se puede retomar.
- **ia no reintenta después del primer `delta`.** Los reintentos del SDK (`CHAT_MAX_RETRIES`, defecto 0) solo ocurren antes de que el proveedor responda 2xx. Si el proveedor ya respondió y después falla o se queda callado, no hay reintento, aunque ia no haya mandado ningún `delta`. Mientras se reintenta, ia sigue mandando `: ping`. Con `CHAT_MAX_RETRIES` mayor que 0:
  - Se reintenta ante un timeout (sin cabeceras del proveedor en `CHAT_TIMEOUT_SECONDS`, o sin conexión en 5 s), una falla de conexión o un 408, 409, 429 o 5xx. El SDK no distingue el 429 por cuota agotada (`insufficient_quota`): también lo reintenta.
  - En una respuesta con código de error, la cabecera `x-should-retry` del proveedor manda sobre el código: con `false` no se reintenta, y con `true` se reintenta cualquier 4xx o 5xx. Un `Retry-After` de más de 120 s anula el reintento, incluso con `x-should-retry: true`.
  - Entre intentos, el SDK espera lo que indique `retry-after-ms` o `Retry-After` (hasta 120 s). Si no viene ninguna de las dos, espera de 0,375 s a 8 s, según el número de reintento.
  - La espera de la conexión y la de las cabeceras se repiten en cada intento, y se suman las esperas entre intentos. Después del 2xx valen los plazos por fragmento del punto 2 de "Consumo desde el backend": el primer evento no tiene una cota fija.
- El lector ignora los eventos y los campos que no conozca: así se pueden agregar sin romper el contrato.

**Evento `done`:**

| Campo | Tipo | Descripción |
|---|---|---|
| citations | lista de `{n, id}` | Una entrada por pasaje citado, ordenadas por `n`. Ver "Citas" |
| coverage | string | Cuánto respaldo tiene la respuesta. Ver "Cobertura" |
| citations_dropped | int | Marcadores que escribió el modelo y que ia quitó por inválidos. Sirve para medir el prompt; no se muestra |
| finish_reason | string | `stop`: terminó normalmente. `length`: llegó a `CHAT_MAX_COMPLETION_TOKENS` y el texto quedó incompleto. `content_filter`: el proveedor cortó el texto con su filtro. Sin llamada al modelo vale `stop`. Cualquier otro valor del proveedor (por ejemplo `tool_calls`) se informa como `stop`, con un aviso en el log |
| usage | objeto o `null` | Tokens de la llamada, o `null` si el proveedor no informó el uso (el backend lo estima). Ver "Uso de tokens" |
| chat_version | string | `<CHAT_PROMPT_VERSION>@<modelo>`, por ejemplo `chat-v3@gpt-5.6-luna`; `fake-v1@fake` con el respondedor falso. Ver "Versión del prompt" |
| dev_metrics | objeto o `null` | Con `INCLUDE_DEV_METRICS` en verdadero (el valor por defecto): `elapsed_ms` (hasta el evento final) y `first_delta_ms` (hasta el primer `delta`), en milisegundos. A diferencia de clasificación y perfil, no repite los tokens de `usage`. Con `INCLUDE_DEV_METRICS=false`, `null`. Es un campo de desarrollo: el backend no debe depender de él |

Con un `finish_reason` distinto de `stop`, el texto está incompleto, pero sus citas son válidas. Cómo se muestra está en "Pendiente de acordar".

**Evento `error`:**

| Campo | Tipo | Descripción |
|---|---|---|
| code | string | Tipo de falla. Ver "Errores durante el stream" |
| retryable | bool | Si el mismo pedido puede funcionar más tarde. No obliga a reintentar |
| usage | objeto o `null` | Los tokens facturados, si el proveedor llegó a informarlos (por ejemplo, con `empty_output`). Casi siempre `null` |
| chat_version | string | Igual que en `done` |

`error` no trae citas: una respuesta que no terminó no tiene lista de fuentes. Los `[n]` que ya llegaron en los `delta` quedan sin mapeo, como en una respuesta detenida o cortada, que tampoco recibe `done` (punto 6 de "Consumo desde el backend").

---

### Citas

- El modelo ve cada pasaje con una clave corta, de `P1` a `Pn`, en el orden recibido, y cita con `[P3]`. Nunca ve los `id`: citar un UUID cuesta más tokens y el modelo lo puede copiar mal.
- Una clave es una `P`, en mayúscula o minúscula, seguida de un número (dígitos decimales de cualquier alfabeto: `P１` es `P1`), corresponda o no a un pasaje del pedido, con espacios opcionales entre las dos y escrita como palabra aparte: `P3`, `p3` y `P 3` son claves; `P2O5` no lo es.
- ia valida cada marcador contra las claves de este pedido y lo renumera por orden de primera aparición: el primer pasaje citado es `[1]`, el siguiente distinto `[2]`, y así. Un pasaje citado varias veces conserva su número.
- El prompt pide una clave por corchete y, para varios pasajes, marcadores seguidos: `[P1][P3]`. ia también acepta una lista de claves en un mismo corchete, separadas por coma, punto y coma o `y` (`[P1, P3]`, `[p1; P3]`, `[P1 y P3]`). La lista sale como marcadores seguidos, en el orden escrito y sin repetir un pasaje: si son las primeras citas de la respuesta, `[P1, P3]` sale como `[1][2]`.
- Cada clave que no se asignó en este pedido se quita del texto y suma 1 en `citations_dropped`.
- Cualquier otro tramo entre corchetes que contenga una clave, por ejemplo un rango `[P1-P3]` o `[ver P2]`, se quita entero y suma 1 en `citations_dropped`.
- **En el texto que emite ia, un número entre corchetes (`[3]`) es siempre un marcador válido.** Si el modelo escribe un número entre corchetes por su cuenta, ia lo quita y suma 1 en `citations_dropped`. Lo demás entre corchetes (`[imagen omitida]`, `[sic]`, `[P2O5]`) pasa tal cual, salvo la marca de `not_covered` (ver "Cobertura").
- El filtro funciona aunque el proveedor parta un marcador entre dos fragmentos: ia retiene el texto desde `[` hasta `]`, con un máximo de 64 caracteres contando el `[`, y solo entonces lo emite.
  - Si junta 64 caracteres sin `]`, el tramo no es un marcador: ia emite lo retenido tal cual y no vuelve a retener hasta el próximo `[`. Lo descarta, en cambio, si contiene una clave (suma 1 en `citations_dropped`) o si todavía puede formar un número entre corchetes o la marca de `not_covered` (suma 1 si tiene dígitos).
  - Si el stream termina con un tramo retenido sin `]`, ia lo emite tal cual, incluido el comienzo de un número, salvo en dos casos: si contiene una clave, lo descarta y suma 1 en `citations_dropped`; si es el comienzo de la marca de `not_covered`, lo descarta.
  - Si aparece otro `[` antes del `]`, el tramo cortado no se emite enseguida: espera al siguiente. Si ese se elimina, el cortado sigue retenido como si el eliminado no estuviera: `[3[P9]]` es el número `[3]` y se quita, con 2 en `citations_dropped`. Si no se elimina, el cortado ya no puede formar un número ni la marca y sale tal cual: `[nota [P1]` sale como `[nota ` seguido del marcador de `P1`, y `[SIN_RESPALDO[x]` llega al texto como está. Se retienen así hasta 128 caracteres pendientes más el tramo en curso; pasado ese tope, el tramo más antiguo sale con `(` en lugar de `[`.
  - Cada marcador `[n]` que emite ia llega entero dentro de un mismo `delta`: nunca se parte entre dos.
- `done.citations` trae `{n, id}` para cada número que aparece en el texto, ordenados por `n` y sin huecos (1, 2, 3…). Cada `id` es uno de los `passages[].id` de este pedido y aparece una sola vez. El mapeo viaja solo en `done`.
- Antes de mandar el historial al modelo, ia quita los marcadores `[n]` de los mensajes del asistente, para que no reutilice números de otro turno. Un pasaje citado en un turno anterior se puede volver a citar solo si el backend lo manda de nuevo.
- Un marcador escrito en la entrada no se convierte en cita: antes de armar el prompt, ia lo pasa a paréntesis, o lo quita si es un `[n]` de un mensaje del asistente (ver "Reglas").
- Las citas no repiten `kind` ni `reference`: el backend ya los tiene, porque armó los pasajes. Con el `id` arma el enlace que muestra el frontend.

### Cobertura

| `coverage` | Significado | Texto | Citas |
|---|---|---|---|
| `answered` | La respuesta se apoya en los pasajes | Del modelo | Al menos una |
| `not_covered` | El modelo indicó que los pasajes no responden la pregunta | Del modelo: dice qué falta. Si después de la marca no hay texto, fijo (abajo) | Puede no tener |
| `uncited` | El modelo respondió sin ninguna cita válida | Del modelo | Ninguna |
| `refused` | El proveedor marcó la respuesta como negativa (campo `refusal`) | Fijo: "No puedo responder esta pregunta."; si la negativa llega después de emitir texto, ese texto seguido del fijo | Las del texto emitido antes de la negativa; ninguna si no se emitió texto |
| `no_passages` | El pedido llegó sin pasajes y no se llamó al modelo | Fijo (abajo) | Ninguna |

- `coverage` es la señal: el backend y el frontend deciden con ella, no leyendo el texto.
- Para `not_covered`, el prompt le pide al modelo empezar con una marca fija entre corchetes cuando los pasajes no alcanzan. El texto exacto lo fija el prompt, por ejemplo `[SIN_RESPALDO]`.
  - La marca cuenta solo al comienzo del texto. Puede tener antes espacios, saltos de línea y marcadores que ia quita (claves no asignadas, números entre corchetes, rangos u otros tramos con una clave, y tramos sin cierre que ia descarta). Todo eso también se descarta, y los marcadores suman en `citations_dropped` como siempre. Cualquier otra cosa antes de la marca, incluido un marcador válido, un tramo que pasa tal cual, como `[sic]`, o un `[` sin cerrar que la contiene, como en `[[SIN_RESPALDO]1]`, hace que la marca ya no esté al comienzo.
  - ia retiene el comienzo hasta confirmarla o descartarla, y la reconstruye aunque llegue partida, con la misma retención de los marcadores.
  - Si aparece más adelante, ia la quita sin cambiar `coverage`.
  - Si el stream termina a mitad de la marca, en cualquier posición, ia descarta lo retenido; si así no queda texto visible, el resultado es `error` con `empty_output`.
  - La marca nunca llega al texto, ni completa ni partida.
- Si después de la marca no viene texto, ia manda uno fijo: "Las fuentes disponibles no alcanzan para responder esta pregunta." Si los pasajes cubren solo una parte, el modelo responde esa parte con citas y dice qué falta; eso es `answered`.
- `uncited` es una falla del modelo frente a la regla de citar: texto sin ninguna cita válida y sin la marca al comienzo. Cómo se muestra está en "Pendiente de acordar".
- Si aplica más de un valor, gana el primero de este orden: `refused`, `not_covered`, `answered`, `uncited`. `no_passages` se decide antes de llamar al modelo.
- `refused` se decide solo por el campo `refusal` de los fragmentos del proveedor, nunca leyendo el texto. Una negativa que el modelo escriba en `content` se trata como cualquier otra respuesta: por ejemplo, sale como `uncited` y se reenvía. El texto de `refusal` no se reenvía ni se registra, porque podría repetir el contenido. Desde el primer fragmento con `refusal`:
  - Si ia todavía no emitió texto, descarta lo que tenga retenido, manda el texto fijo como único `delta` y, al final, `done` con `coverage: refused` y `citations` vacía.
  - Si ya emitió texto, no emite más contenido y descarta lo retenido (un marcador a medio armar o el comienzo pendiente de la marca de `not_covered`). Manda el texto fijo como un `delta` más, precedido de un salto de párrafo, y al final `done` con `coverage: refused` y las citas del texto ya emitido, para que cada `[n]` tenga su entrada.
  - En los dos casos, ia sigue leyendo el stream del proveedor hasta el final, sin emitir nada más, para informar `finish_reason` y `usage`.
- Texto fijo de `no_passages`: "No encontré información para responder esta pregunta en la normativa ni en los datos de la empresa." Viaja como un único `delta`, seguido de `done` con `usage` en cero y `llm_calls` 0. El backend puede ahorrarse la llamada y responder lo mismo por su cuenta (punto 14 de "Consumo desde el backend").
- Los textos fijos se pueden ajustar (ver "Pendiente de acordar"); cambiarlos sube `CHAT_PROMPT_VERSION`.

### Uso de tokens

```json
"usage": {
  "prompt_tokens": 3480,
  "completion_tokens": 410,
  "total_tokens": 3890,
  "cached_tokens": 0,
  "reasoning_tokens": 96,
  "llm_calls": 1
}
```

| Campo | Descripción |
|---|---|
| prompt_tokens | Tokens de entrada, incluidos los que salieron del caché |
| completion_tokens | Tokens de salida, incluidos los de razonamiento: el razonamiento se factura como salida aunque no se vea |
| total_tokens | Suma de los dos |
| cached_tokens | Parte de `prompt_tokens` que el proveedor sirvió desde su caché. 0 si no lo informa. Su valor depende del modo de caché que se acuerde. Con un modo explícito, el valor de la repetición de un pedido idéntico delata si el proveedor no lo respeta (ver "Privacidad") |
| reasoning_tokens | Parte de `completion_tokens` que el modelo usó para razonar. 0 si no lo informa |
| llm_calls | 1 si se llamó al modelo; 0 sin pasajes o con el respondedor falso. Los intentos fallidos del SDK no se cuentan |

- ia pide el uso con `stream_options.include_usage`, y el proveedor lo manda en un fragmento aparte, después del que trae `finish_reason`. **Si ese fragmento no llega, `usage` vale `null`:** en `error` si el stream se cortó, y en `done` si el proveedor terminó la respuesta sin mandar el uso. En los dos casos el backend estima (punto 9 de "Consumo desde el backend").
- Es un esquema propio del chat. El `usage` de clasificación, perfil y embeddings no cambia.
- Sin pasajes o con el respondedor falso, `usage` no es `null`: vale cero en todos los campos.

### Errores antes del stream

| Código | Cuándo |
|---|---|
| 401 | Falta `X-API-Key` o no coincide con `SERVICE_API_KEY` |
| 422 | El cuerpo no cumple el esquema (falta un campo, un tipo no corresponde, `role` o `kind` fuera de la lista, un `id` repetido, un `id` con un surrogate suelto (vuelve tal cual en `done.citations` y System.Text.Json no lo podría leer), un `id`, una `reference` o un `app_name` que no está en una línea, o un campo extra), o un largo supera su tope |
| 503 | Falta `SERVICE_API_KEY`, o falta `OPENAI_API_KEY` con el respondedor real (el falso no la exige) |

- Todos se deciden antes de abrir el stream y de llamar al proveedor: no gastan tokens.
- Sin `OPENAI_API_KEY` y con el respondedor real, la respuesta es 503 aunque el pedido venga sin pasajes, y la falta de configuración gana también sobre el 422 de esquema y de largos. El orden es: lectura del cuerpo; `SERVICE_API_KEY` y `X-API-Key` (503 o 401); `OPENAI_API_KEY` (503); y al final el esquema y los largos (422). La excepción es un cuerpo que no se puede decodificar: FastAPI lo lee antes de cualquier dependencia, así que un JSON mal formado da 422 (`json_invalid`) y un cuerpo que no es UTF-8 válido da 400, aunque falte `X-API-Key`.
- El 422 tiene dos formas, como en los otros endpoints: la lista de FastAPI (`{"detail": [{"type": "missing", "loc": ["body", "passages"], "msg": "Field required"}]}`) y el mensaje del caso de uso (`{"detail": "The question has 2345 characters; the limit is 2000."}`). **Ninguna repite el contenido:** el manejador global de la app quita `input` y `ctx` de la lista, y todo mensaje propio es fijo, venga de un validador del esquema (un `value_error`, cuyo `msg` el manejador conserva) o del caso de uso. Da largos, topes o posiciones, nunca valores del pedido; por ejemplo, `"msg": "Value error, Passage ids must be unique."` para un `id` repetido (Pydantic antepone "Value error, " al mensaje del validador).
- Cualquier otro código, por ejemplo un 500 inesperado antes del stream, se trata como falla transitoria: el backend puede reintentarlo según el punto 10 de "Consumo desde el backend". El 400 de un cuerpo que no se puede decodificar no es transitorio: se repite igual.

### Errores durante el stream

| `code` | `retryable` | Cuándo |
|---|---|---|
| `rate_limited` | `true` | El proveedor respondió 429 por límite de tasa, también después de los reintentos de `CHAT_MAX_RETRIES` |
| `timeout` | `true` | El proveedor no respondió, o dejó de mandar fragmentos, durante `CHAT_TIMEOUT_SECONDS`, o no se pudo conectar en 5 s. Con `CHAT_MAX_RETRIES` mayor que 0, la espera de las cabeceras se reintenta y el error solo llega cuando falla el último intento (ver "Reglas del stream") |
| `provider_error` | `true` | El proveedor respondió 408, 409 o 5xx; la conexión fue rechazada o se cortó; el proveedor mandó un error dentro del stream ya aceptado (`data: {"error": ...}` después de su 200); o el stream del proveedor terminó sin `finish_reason` (cortado a mitad) |
| `provider_rejected` | `false` | El proveedor rechazó el pedido: cualquier otro 4xx (400, 401, 403, 404, 413, 422…) o un 429 por cuota agotada (`insufficient_quota`). Clave de OpenAI inválida, modelo desconocido, parámetro no admitido, entrada demasiado larga para el modelo o cuota agotada: reintentar no sirve hasta corregirlo |
| `empty_output` | `false` | El modelo terminó sin texto visible: no escribió texto (por ejemplo, porque el razonamiento consumió todo `CHAT_MAX_COMPLETION_TOKENS`), solo escribió marcadores que ia quitó, o el stream terminó a mitad de la marca de `not_covered` |
| `internal` | `false` | Falla inesperada de ia-cumplify |

- Un timeout sale como `timeout`, nunca como `provider_error`, aunque también sea una falla de la conexión.
- Un error dentro del stream sale como `provider_error`, sea cual sea su tipo. `rate_limited` y `provider_rejected` salen solo del código HTTP con que el proveedor rechazó el pedido al abrir el stream, antes del primer fragmento y sin generar nada.
- Si la falla (conexión, error dentro del stream o timeout) llega después de que el proveedor mandó `finish_reason` y antes del fragmento de uso, el modelo ya terminó de generar: ia cierra como en un final normal, con `usage: null`. Manda `done` con sus citas y su `coverage`, o `error` con `empty_output` si no hubo texto visible.
- El evento no lleva el mensaje de la excepción ni la respuesta del proveedor, que pueden repetir la entrada: solo `code`.
- Si el error llega después de algunos `delta`, el texto ya enviado es una respuesta incompleta.

---

**Reglas:**

- Una llamada al modelo por pedido, o ninguna si no hay pasajes o con el respondedor falso.
- El modelo solo puede usar los pasajes de este pedido. El historial sirve para entender la pregunta (por ejemplo, "¿y para las bodegas?"), no como fuente.
- Los pasajes van al modelo en el orden recibido, cada uno en un bloque que abre con la línea `--- PASAJE P<n> ---` y cierra con `--- FIN PASAJE P<n> ---`, con su tipo y su referencia en líneas propias dentro del bloque. La regla exacta y el orden de los mensajes están en CHT-007 de `requirements.md`.
- Antes de armar el prompt se quitan las imágenes embebidas `data:image/...;base64,...` y se reemplazan por `[imagen omitida]`, igual que en la clasificación. Además:
  - En el texto de los pasajes, en la pregunta y en los mensajes del historial se neutralizan las líneas que imitan un delimitador del prompt.
  - En todo el pedido (texto y referencia de los pasajes, pregunta, historial y `context.app_name`), los tramos entre corchetes sobre los que actúa el filtro de citas, los que contienen una clave o solo un número, pasan a paréntesis: `[P2]` a `(P2)`, `[P1, P2]` a `(P1, P2)`, `[3]` a `(3)`. La excepción son los números entre corchetes de los mensajes `assistant`, que se quitan (ver "Citas"). Si no, un `[P2]` escrito dentro de otro pasaje, por ejemplo en una obligación cargada por un usuario, llega al modelo y, si lo copia, pasa el filtro y la validación del backend como una cita a P2.
  - En los mismos campos se quita la marca de `not_covered`.
  - Los surrogates sueltos (U+D800 a U+DFFF, escapados en el JSON) de `question`, `history[].content`, `passages[].text`, `passages[].reference` y `context.app_name` pasan a U+FFFD, sin cambiar el largo.
  - Lo que queda vacío después de estos pasos no cambia los topes, que se midieron antes. Un mensaje del historial vacío no se manda al modelo; un `app_name` vacío se omite, sin la línea `App:`; una pregunta vacía llega como el texto fijo "(pregunta sin texto)", para que el proveedor no rechace un mensaje vacío; una `reference` o un `text` vacíos se mandan vacíos dentro de su bloque, para no cambiar las claves `P<n>`.
- **Los pasajes y el nombre de la app son datos, no instrucciones.** El prompt pide ignorar cualquier instrucción escrita en ellos (en `reference`, `text` o `app_name`), también la que aparezca dentro del texto de una norma o de una obligación cargada por un usuario.
- **La pregunta es el pedido del usuario.** El modelo atiende lo que pide sobre el contenido o la forma de la respuesta (por ejemplo, "resume el artículo 184" o "explícalo en tres oraciones"), pero no cumple lo que contradiga estas reglas, como responder sin los pasajes o sin citar, o usar Markdown. El historial solo sirve para entender la pregunta: una pregunta anterior le da sentido a un seguimiento ("¿y el 185?" después de "resume el artículo 184"), pero no agrega pedidos ni cambia las reglas, y las respuestas anteriores no son instrucciones ni fuente.
- **Solo lectura:** el chat no cambia nada en la plataforma. El modelo no afirma haber hecho un cambio ni ofrece hacerlo.
- **Texto plano en español neutro**, sin Markdown, con saltos de línea entre párrafos. Para enumerar, cada ítem va en su propio párrafo y empieza con «•» o con la letra o el número que le da el pasaje; ninguna línea empieza con `*`, `-`, `+` o `#`, aunque la pregunta pida Markdown. Para nombrar una fuente usa su `reference` ("según el artículo 66 de la Ley 16.744 [1]"), y para referirse a todas dice "las fuentes disponibles", nunca "pasajes". Es una regla del prompt, no una garantía: quien muestra la respuesta la trata como texto no confiable (punto 6 de "Consumo desde el backend").
- La llamada a OpenAI usa `chat.completions` con `stream=True`, `stream_options.include_usage`, `store=False`, `prompt_cache_options` según lo que se acuerde en "Caché de prompts" (sin el parámetro rige el modo implícito; ver "Privacidad"), `CHAT_REASONING_EFFORT` y `CHAT_MAX_COMPLETION_TOKENS` (que incluye el razonamiento), con el timeout y los reintentos propios del chat (`CHAT_TIMEOUT_SECONDS` y `CHAT_MAX_RETRIES`), no los de clasificación.
- Cuando el backend corta la conexión, ia cierra la llamada al proveedor en milisegundos (CHT-011).

**Versión del prompt:**

- `chat_version` es `<CHAT_PROMPT_VERSION>@<modelo>`, con el modelo efectivo: `CHAT_MODEL`, o `OPENAI_MODEL` si está vacía. La versión actual es `chat-v3`. Con el respondedor falso vale `fake-v1@fake` (ver "Respondedor falso").
- `CHAT_PROMPT_VERSION` es una constante del código, como `PROMPT_VERSION` en la clasificación, y no una variable de entorno. Sube con cada cambio del prompt de sistema, del formato del bloque de pasajes, de los textos fijos o de las reglas de marcadores y de cobertura.
- Viaja en `done` y en `error`. El backend la guarda con cada respuesta de ia, para comparar versiones en el registro de 4.8.

**Privacidad:**

- El texto de la pregunta, del historial, de los pasajes, del nombre de la app y de la respuesta no se registra en el log ni aparece en ningún error. El log lleva largos, cantidades, tipos de pasaje, tipo de excepción, tokens y tiempos.
- **El 422 no repite la entrada.** Por defecto, FastAPI la devuelve en `input`. Si falta un campo del primer nivel, `input` es el cuerpo completo, con la pregunta y los pasajes; si falta uno dentro de un pasaje, es ese pasaje. El manejador de `RequestValidationError` se registra en la app (`APIRouter` no admite manejadores) y vale para todos los endpoints, no solo para `/chat`: también cambia el 422 de clasificación, perfil y embeddings, del que solo quita `input` y `ctx`. El manejador no registra `exc`, `str(exc)`, `exc.errors()` ni `exc.body`: los tres primeros traen el `input` de cada error, y `exc.body` es el cuerpo recibido (el texto crudo si el JSON es inválido). Si registra algo, solo `type` y `loc`.
- El manejador corrige también un incumplimiento actual del requisito PRF-006 de `docs/CompanyProfile/requirements.md` (el texto no aparece en el cuerpo de un error). Hoy el 422 de `/company-profiles/classify` repite el texto de la empresa en tres casos: cuando `text` llega como lista u objeto; cuando falta `text` y el texto viene bajo otro nombre (por ejemplo `txt` o `Text`); y cuando el cuerpo no es un objeto JSON (un string, una lista, o un `Content-Type` ausente o distinto de JSON). En los dos últimos casos, `input` es el cuerpo completo.
- El evento `error` y los logs no copian el mensaje del proveedor. La negativa del modelo tampoco se copia.
- `store=False`: OpenAI no guarda la respuesta para sus productos de destilación y evals. No se envían `user` ni `safety_identifier`.
- **Caché de prompts de OpenAI.** `store=False` no lo cubre. Qué modo usa el chat está pendiente de acordar ("Caché de prompts" en "Pendiente de acordar"). Según el SDK (openai 3.14.1):
  - Sin `prompt_cache_options` rige el modo implícito: OpenAI elige dónde cortar el prefijo que guarda, y ese prefijo puede incluir los pasajes. Tiene una vida mínima de 30 minutos (`prompt_cache_options.ttl`) y una retención de hasta 24 horas (`prompt_cache_retention`, deprecado en favor de `prompt_cache_options.ttl`, que desde gpt-5.5 solo admite `24h`).
  - Desde gpt-5.6, `prompt_cache_options={"mode": "explicit"}` quita el punto de corte implícito: OpenAI solo escribe en el caché los prefijos que terminan en un `prompt_cache_breakpoint`, hasta los cuatro últimos del pedido. Sin ninguno, el pedido no usa el caché y se pierde todo el descuento de `cached_tokens`.
  - Con un único punto de corte al final de las instrucciones fijas, el caché guarda solo las instrucciones y conserva el descuento sobre ellas. El punto de corte se marca en la última parte de texto de las instrucciones, que no lleva ningún dato del pedido (nombre de la app, pasajes, historial ni pregunta). Para eso, el mensaje `system` se manda como lista de partes de texto.
  - Que OpenAI respete un modo explícito no se puede comprobar sin llamarlo. La comprobación es indirecta: se repite un pedido idéntico (mismas instrucciones y mismos pasajes) dentro de los 30 minutos y se lee `cached_tokens` en la repetición. Tiene que valer 0 sin puntos de corte, y no superar los tokens de las instrucciones con un punto de corte al final de ellas. Un valor mayor delata la falla. Un valor dentro del límite no prueba que se cumpla: `cached_tokens` mide lecturas del caché, no escrituras, y un pedido que no encuentra el prefijo en el caché también da 0.

**Configuración:**

| Variable | Defecto | Uso |
|---|---|---|
| `CHAT_MODEL` | vacía (usa `OPENAI_MODEL`) | Modelo del chat. Si se acuerda un modo explícito de caché, tiene que admitir `prompt_cache_options` (gpt-5.6 en adelante, ver "Privacidad") |
| `CHAT_REASONING_EFFORT` | vacía (usa `OPENAI_REASONING_EFFORT`) | Esfuerzo de razonamiento del chat |
| `CHAT_MAX_COMPLETION_TOKENS` | 4.000 | Tope de salida, razonamiento incluido. Se calibra con el set de evaluación |
| `CHAT_TIMEOUT_SECONDS` | 60 | Lectura, por intento: lo máximo que ia espera las cabeceras del proveedor o el siguiente fragmento. La conexión tiene 5 s |
| `CHAT_MAX_RETRIES` | 0 | Reintentos del SDK, solo antes de que el proveedor responda 2xx (ver "Reglas del stream"). Con 0, el backend decide si reintenta (punto 10 de "Consumo desde el backend") |
| `CHAT_QUESTION_MAX_CHARS` | 2.000 | Largo máximo de `question` |
| `CHAT_HISTORY_MAX_MESSAGES` | 6 | Mensajes máximos de `history` |
| `CHAT_HISTORY_MAX_CHARS` | 8.000 | Caracteres máximos de `history`, sumados |
| `CHAT_MAX_PASSAGES` | 12 | Pasajes máximos |
| `CHAT_PASSAGE_MAX_CHARS` | 6.000 | Largo máximo de cada `passages[].text` |
| `CHAT_PASSAGES_MAX_TOTAL_CHARS` | 48.000 | Suma máxima de los `passages[].text` |
| `CHAT_FAKE_RESPONDER` | `false` | Respondedor falso, sin OpenAI (ver "Respondedor falso") |

- `SERVICE_API_KEY`, `OPENAI_API_KEY` e `INCLUDE_DEV_METRICS` (verdadero por defecto) son los mismos de los otros endpoints, aunque el `dev_metrics` del chat tiene su propia forma (ver el evento `done`). `OPENAI_TIMEOUT_SECONDS` (180) y `OPENAI_MAX_RETRIES` (2), que usan los otros endpoints, no se aplican al chat: en el peor caso suman minutos sin respuesta.
- Los topes de largo se espejan en el backend, igual que `PROFILE_TEXT_MAX_CHARS` con `AI_PROFILE_TEXT_MAX_CHARS`: si el backend sube un tope y ia no, un pedido válido para el backend recibe 422. Las reglas fijas del pedido también se espejan (punto 11 de "Consumo desde el backend").
- Con los topes por defecto, la entrada no pasa de unos 16.900 tokens (60.600 caracteres a 3,85 por token, más unos 1.150 de instrucciones), y la salida, de `CHAT_MAX_COMPLETION_TOKENS`: unos 20.900 tokens por pregunta en el peor caso. Medido con `chat-v3` en el set de evaluación de 4.2: de 1.275 a 3.948 tokens de entrada y hasta 834 de salida por pregunta.

**Respondedor falso:**

- Con `CHAT_FAKE_RESPONDER=true` (CHT-014), ia no llama a OpenAI ni exige `OPENAI_API_KEY`. Sirve para integrar el backend (4.7) sin gastar tokens. Está desactivado por defecto y, si arranca activo, el log lo avisa.
- Con pasajes, emite un texto fijo, en uno o más `delta`, que cita el primer pasaje como `[1]`. Después viene `done` con `citations` `[{"n":1,"id":"<id del primer pasaje>"}]`, `coverage: answered`, `citations_dropped` 0, `finish_reason: stop`, `usage` en cero y `llm_calls` 0.
- Sin pasajes, da la misma negativa fija que el respondedor real (`coverage: no_passages`).
- `chat_version` vale siempre `fake-v1@fake`, también sin pasajes y en `error`. Con ese valor el backend reconoce las respuestas falsas y no las confunde con las reales, por ejemplo en el registro de 4.8. Como `usage` viaja en cero, no mueven el presupuesto de 4.7.

**Limitaciones:**

- No recupera pasajes ni lee la base: responde con lo que recibe.
- No verifica que los pasajes sean de la empresa del usuario. Eso lo filtra el backend al recuperarlos (4.5 y 5.3).
- No guarda conversaciones ni aplica límites de uso o de presupuesto (4.4 y 4.7).
- No reformula las preguntas de seguimiento ni clasifica la intención (4.6).
- Un stream cortado no se retoma y no informa el uso.

---

## Consumo desde el backend

Lo que necesita el backend (4.7 y 4.8) para consumir este endpoint. El cliente actual, `IaCumplifyClient`, está hecho para workers: lee la respuesta completa con `PostAsync` y `ReadAsStringAsync` y espera hasta 15 minutos, así que no sirve para el stream.

1. **Cliente propio.** Un `HttpClient` y una clase separados de los de los workers, con `SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct)` para leer el cuerpo a medida que llega. Conviene hacerlo después de integrar el worker del perfil (tarea 1.5), que toca los mismos archivos (`IaCumplifyClient.cs`, `AiOptions.cs`, `AiModuleExtensions.cs`, `docker-compose.yml` y `.env.example`).
2. **Tiempos acordes al stream.** ia manda las cabeceras en milisegundos, así que el tiempo para recibirlas puede ser corto (unos 10 s). Después, ia manda `: ping` tras 15 s sin eventos, también mientras espera al proveedor. Un tope de inactividad de unos 45 s detecta una conexión muerta, siempre que se renueve con cada lectura de bytes del stream (por ejemplo, envolviendo el `Stream` de la respuesta) y no con cada evento del lector SSE, que no entrega los pings (punto 3).
   - Medirlo por evento no es seguro con ningún valor. El timeout de lectura de ia (`CHAT_TIMEOUT_SECONDS`) se renueva con cada lectura de bytes del proveedor, no con cada evento que emite ia. Antes del primer `delta` pueden pasar, sin que ia corte, 5 s de conexión, hasta 60 s hasta las cabeceras del proveedor y hasta 60 s por cada fragmento sin texto: el primero suele traer solo el rol, y el proveedor puede mandar fragmentos vacíos mientras el modelo razona (no verificado). Con `CHAT_MAX_RETRIES` mayor que 0, más todavía. Los fragmentos que siguen a una negativa tampoco producen eventos. Un tope por evento de 90 s cortaría respuestas válidas y también el `error` con `timeout` que mandaría ia.
   - El tope total puede ser generoso (unos 5 minutos), porque la salida ya está acotada por `CHAT_MAX_COMPLETION_TOKENS`. Con `CHAT_MAX_RETRIES` mayor que 0 tiene que cubrir también los reintentos y las esperas entre ellos (ver "Reglas del stream").
   - Con `ResponseHeadersRead`, `HttpClient.Timeout` acota solo el `SendAsync`, hasta las cabeceras: sirve para los 10 s, pero no para la inactividad ni para el tope total. En `IaCumplifyClient` sí acota todo, porque `PostAsync` lee el cuerpo completo.
   - La inactividad (`CancelAfter(45 s)`, renovado en cada lectura de bytes) y el tope total (`CancelAfter(5 min)`) van en `CancellationTokenSource` enlazados a `HttpContext.RequestAborted`, cuyo token se pasa a la lectura del stream, como hace `BcnApiClient` con las cabeceras y el cuerpo.
   - Al cancelar, el backend revisa qué fuente tiene `IsCancellationRequested` y registra el corte como detenido por el usuario, por inactividad o por tope total: la excepción trae el token que se pasó a la lectura, no la fuente que se disparó.
3. **Lector SSE.** `System.Net.ServerSentEvents` (`SseParser`), que viene en el runtime de .NET 10 que usa el backend. Omite por su cuenta los comentarios `: ping`: con un stream de solo pings no entrega ningún ítem. Hay que leer `event` y decodificar `data` como JSON, que puede traer `\uXXXX`, e ignorar los eventos y campos desconocidos.
4. **Evento final obligatorio.** Se trata como respuesta cortada tanto el fin del stream sin `done` ni `error` como una excepción de lectura, por ejemplo un cuerpo chunked incompleto. `SseParser` no avisa el corte: descarta sin error un evento sin su línea en blanco final y termina la enumeración. ia captura en el generador toda `Exception` para mandar siempre un evento final, pero el lector no debe depender de eso.
5. **Cancelación.** Pasar `HttpContext.RequestAborted` (el usuario detuvo la respuesta o cerró la página), enlazado con los topes del punto 2, al `SendAsync` y a la lectura (`SseParser.EnumerateAsync(token)`), y liberar la respuesta al terminar. Cerrar la conexión con ia es la señal que hace que ia cierre la llamada a OpenAI.
   - Cancelar una lectura pendiente cierra la conexión al instante; liberar la respuesta sin una lectura cancelada, no. Si el corte llega mientras el backend escribe al navegador, o si el bucle termina por una excepción, `SocketsHttpHandler` sigue leyendo la respuesta hasta 2 s para reutilizar la conexión (`ResponseDrainTimeout`), y mientras tanto OpenAI sigue generando. Por eso el cliente del chat, y no el de los workers, se registra con `ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler { ResponseDrainTimeout = TimeSpan.Zero })`.
   - Con `TypedResults.ServerSentEvents`, el iterador recibe `RequestAborted` como parámetro y crea dentro de él el `CancellationTokenSource` de los topes. El manejador termina antes de que empiece la enumeración, y un `CancellationTokenSource` creado con `using` en el manejador ya está liberado y no cancela.
   - Si el backend sigue leyendo después de que el navegador se fue, OpenAI sigue generando, y facturando, hasta el final.
6. **Reenvío al navegador.** El endpoint de mensajes del backend responde `text/event-stream` sin buffer ni compresión, con `Cache-Control: no-cache` y `X-Accel-Buffering: no`. Hoy el backend no configura compresión de respuestas; si se agrega, hay que excluir este tipo. ASP.NET Core 10 trae `TypedResults.ServerSentEvents`, que reenvía un `IAsyncEnumerable` como SSE sin manejar el flush a mano. Puede reenviar los mismos `delta` y convertir `done.citations` en las citas del frontend, conservando `n`.
   - **Cabeceras.** `TypedResults.ServerSentEvents` no envía las cabeceras hasta el primer ítem. Pone `Content-Type: text/event-stream`, `Cache-Control: no-cache,no-store`, `Pragma: no-cache` y `Content-Encoding: identity`, pero no `X-Accel-Buffering: no`: hay que agregarla en `Response.Headers` antes de devolver el resultado. Si se escribe a mano, `Response.StartAsync()` no envía las cabeceras; hace falta llamar a `Response.Body.FlushAsync()` después.
   - **Keepalive propio.** `SseParser` no le entrega al backend los `: ping` de ia (punto 3) y `SseItem<T>` no admite comentarios. Si el backend reenvía solo lo que entrega el lector, el navegador no recibe ni un byte durante los silencios del punto 2 ni durante un reintento (punto 10); antes del primer ítem no recibe ni las cabeceras. Un proxy con tope de inactividad, como el nginx que está delante del backend (`proxy_read_timeout`, 60 s por defecto), corta el stream o, si todavía no salieron las cabeceras, responde 504. Por eso el backend escribe un keepalive cada 15 s sin eventos, con un temporizador propio que arranca al abrir la respuesta: así cubre también la recuperación de pasajes (4.5) antes de llamar a ia. Con `TypedResults.ServerSentEvents` es un evento propio del backend, por ejemplo `event: ping` con `data: {}`, intercalado sin cancelar la lectura pendiente del lector, que el frontend ignora (ver "Pendiente de acordar"). Si el backend escribe el SSE a mano, es un comentario `: ping` seguido de `Response.Body.FlushAsync()`, que todo lector SSE ignora.
   - **Texto no confiable.** El texto de los `delta` es salida de un modelo que leyó contenido no confiable, e ia no filtra URL ni HTML. El backend lo guarda y lo reenvía sin interpretarlo, y el frontend lo muestra como texto: sin interpretar HTML ni Markdown, sin convertir URL en enlaces y sin cargar imágenes. Solo los marcadores `[n]` se convierten en referencias a `done.citations`, creando los elementos de la interfaz, sin armar HTML.
   - **Marcadores sin cita.** El frontend une cada marcador con su cita por `n`, no por su posición en la lista, y no supone que todo marcador tiene su cita ni que los números van sin huecos. En una respuesta detenida, con `error` o cortada, los `[n]` ya reenviados no tienen mapeo, y la segunda validación (punto 7) puede dejar alguno sin cita. Un marcador sin cita se muestra como texto plano, sin enlace. Cómo se muestran los marcadores en general está en "Pendiente de acordar".
7. **Segunda validación de citas.** Antes de guardar o reenviar `done`, comprobar que cada `citations[].id` esté entre los pasajes enviados en este turno. ia ya lo garantiza: si una falla, es un defecto de ia. La cita se quita de `citations` y se registra sin texto. `coverage` solo cambia si era `answered` y la validación le quitó todas las citas: en ese caso pasa a `uncited`. Los demás valores se mantienen aunque la lista quede vacía: `not_covered` y `refused` pueden no tener citas, y `no_passages` nunca las tiene. El `[n]` de la cita quitada ya llegó al navegador en un `delta`, así que el texto puede traer un número sin cita y la lista puede quedar con huecos (punto 6). El backend guarda el texto tal como llegó, y la misma regla vale al volver a cargar la conversación.
8. **Registro sin texto (4.8).** Por respuesta: `chat_version`, `coverage`, `finish_reason`, `usage` (o la estimación, marcada como tal), la latencia hasta el primer `delta` y la total, los ids enviados y los citados, `citations_dropped`, y `code` y `retryable` si hubo error. Nunca la pregunta, el historial, los pasajes ni la respuesta. Como en el cliente del perfil de la tarea 1.5 (rama `feat/ai-profile-text-worker` del backend, todavía sin integrar; ver el punto 1), no guardar el cuerpo de los errores HTTP (`includeErrorBody: false`), por si una versión de ia sin el manejador del 422 repite la entrada.
9. **Uso estimado.** Se estima si el stream se cortó sin evento final, o si el evento final (`done` o `error`) trae `usage: null`, salvo un `error` con `rate_limited` o `provider_rejected`. La entrada se estima como (caracteres de la pregunta, el historial, los `passages[].text`, las `reference` y `context.app_name`) / 3,85, más unos 1.150 tokens de instrucciones (con `chat-v3`); la salida, como caracteres recibidos / 3,85.
   - Con `rate_limited` o `provider_rejected` no se estima: ia los emite solo por el código HTTP con que el proveedor rechazó el pedido al abrir el stream, antes del primer fragmento y sin generar nada. Se registra 0 y no se descuenta del presupuesto.
   - En una respuesta cortada después de que el proveedor la aceptó, o en un `done` sin uso, el razonamiento no se puede estimar y la cifra queda por debajo de lo facturado. Con `provider_error`, `timeout` o `internal` sin ningún `delta`, la falla pudo ocurrir antes de que el proveedor aceptara el pedido (por ejemplo, un 5xx al abrir) y la cifra puede quedar por encima. Se acepta, porque el backend no distingue ese caso de un corte mientras el modelo razonaba.
   - La cifra se marca como estimada para el presupuesto (4.7) y para el registro (4.8).
   - Si el corte lo hizo el usuario, el uso estimado, el registro de 4.8 y el texto parcial, si se guarda, se escriben con otro token y no con `RequestAborted`. Ese token ya está cancelado: con él, la escritura en la base falla y la respuesta detenida no descuenta del presupuesto.
10. **Reintento.** El backend puede reintentar una vez el mismo pedido, y solo si no recibió ningún `delta`, en tres casos: llegó `error` con `retryable: true`; el stream terminó sin evento final (punto 4), salvo que el corte venga del usuario o del tope total (un corte por inactividad sin ningún `delta` se trata como una conexión muerta y se puede reintentar); o falló antes del stream por una causa transitoria (un código que no sea 400, 401, 422 ni 503, una falla de conexión o las cabeceras que no llegan en el tiempo del punto 2).
    - No se reintentan 400, 401, 422 ni 503, que son de validación o de configuración y se repiten igual, ni un `error` con `retryable: false`. Esto difiere de `IsTransient` del cliente de los workers, que trata 401 y todo 5xx como transitorios: no hay que copiarlo.
    - Con `rate_limited`, ia no informa el `Retry-After` del proveedor y, con `CHAT_MAX_RETRIES=0`, no espera, así que un reintento inmediato puede volver a recibir 429: conviene esperar unos segundos o no reintentarlo.
    - Después de un `delta`, no se reintenta: el usuario ya vio texto.
11. **Reglas y topes espejados.** ia rechaza el turno completo con un 422, que no se puede reintentar, si el pedido no cumple cualquiera de las reglas de la tabla del pedido, no solo los topes configurables. El backend deja el pedido dentro de todas antes de llamar, con los mismos valores en su configuración (por ejemplo `AI_CHAT_*`):
    - **Texto de cada pasaje.** Quita las imágenes y recorta los espacios. Un texto que pasa `CHAT_PASSAGE_MAX_CHARS` se corta, o se parte en trozos con ids distintos. Un pasaje que queda sin texto, por ejemplo una obligación sin descripción, no se manda.
    - **Ids únicos.** Cada pasaje lleva un `id` distinto. Después del troceo (2.2), la búsqueda vectorial puede traer varios trozos del mismo artículo, y un mismo artículo u obligación puede llegar por la búsqueda y por las vinculaciones de la app. En esos casos se deja el más relevante, se unen, o cada trozo lleva su propio `id` y el backend traduce la cita al artículo.
    - **Una línea y hasta 200 caracteres.** En `reference` y `context.app_name`, todo carácter de control (categoría Cc, que incluye los saltos de línea y U+001C a U+001E) y los separadores U+2028 y U+2029 pasan a un espacio. Después, cada secuencia de espacios en blanco pasa a un solo espacio, el texto se recorta y se corta a 200 caracteres. El nombre de una app admite 255 caracteres y el de una obligación, 500; el título de una norma no tiene tope.
    - **Lista cerrada y sin campos extra.** `kind` es uno de la lista y el pedido no trae campos fuera de la tabla.
    - **Sin surrogates sueltos.** System.Text.Json ya escribe U+FFFD en lugar de un surrogate suelto, aunque el corte a 200 unidades UTF-16 parta un emoji, así que el backend no necesita código propio para el `id` ni para los demás campos.
    - **Topes.** Después de lo anterior, elige los pasajes de más a menos relevante hasta `CHAT_MAX_PASSAGES` y `CHAT_PASSAGES_MAX_TOTAL_CHARS`, y arma el historial según el punto 12.
    - **Pregunta.** Se valida antes de llamar a ia: si queda vacía o pasa `CHAT_QUESTION_MAX_CHARS`, se le responde un 400 al navegador.
    - **Medida.** Cada largo se mide con `string.Length`, después de `Trim()` en los campos que ia recorta (todos menos `id`). .NET cuenta unidades UTF-16 (un emoji vale 2) e ia cuenta puntos de código (`len` de Python, donde vale 1), y `Trim()` quita lo mismo que el recorte de ia. Por eso, un texto que entra en un tope medido así también entra en el de ia.
12. **Historial.** La pregunta del último turno y su respuesta, si se manda, van siempre. Si no entran juntas en `CHAT_HISTORY_MAX_CHARS`, la respuesta se acorta al espacio que deja la pregunta y se conserva su comienzo: una sola respuesta puede pasar de 8.000 caracteres, porque `CHAT_MAX_COMPLETION_TOKENS` admite hasta unos 15.400 (4.000 tokens a 3,85 caracteres por token). Después se agregan los mensajes anteriores, del más reciente al más antiguo, y el historial termina en el primero que no entra completo en los topes. Ese mensaje no se salta para seguir con los anteriores, porque saltarlo puede juntar la pregunta de un turno con la respuesta de otro. Los mensajes se mandan del más antiguo al más reciente, con las respuestas tal como se guardaron, salvo el acortamiento: ia quita los marcadores. ia no exige que los roles alternen. Qué pasa con las respuestas detenidas o con error está en "Pendiente de acordar".
13. **Seguimientos.** Para una pregunta como "¿y el artículo siguiente?", conviene reenviar como pasajes los citados en el turno anterior, porque el modelo solo puede citar lo de este pedido.
14. **Sin pasajes.** El backend puede no llamar a ia y responder el texto fijo de `no_passages` con `coverage: no_passages`. Si lo hace, el texto se espeja en el backend como los topes (punto 11): si se cambia en ia, lo que sube `CHAT_PROMPT_VERSION`, se cambia en el backend en la misma entrega, porque esa versión no le llega en las respuestas que da por su cuenta. En el registro (punto 8), esa respuesta lleva `coverage: no_passages`, ninguna cita, `citations_dropped` 0, `finish_reason: stop`, `usage` en cero con `llm_calls` 0 y `chat_version` vacío: no se completa con una versión configurada ni con la última recibida, para que 4.8 no la atribuya a un prompt de ia.

---

## Pendiente de acordar

| Tema | Propuesta | Con quién |
|---|---|---|
| Valores de `kind` | `article`, `legal_body`, `legal_requirement` (la app), `vinculation` (una vinculación con su estado) y `obligation`. Fijan qué tipos de cita muestra el frontend | Backend (4.5) y frontend (4.9) |
| Topes y tope de salida | Los de la tabla de configuración. Se calibran con lo que envíe 4.5 y con el set de evaluación de 4.2 | Backend |
| Historial | Hasta 6 mensajes y 8.000 caracteres. Las respuestas detenidas o con error no se mandan; la pregunta de ese turno sí | Backend y frontend |
| Campos extra | Rechazarlos con 422: detecta errores de nombre en los campos opcionales (`histroy` en vez de `history`, o `appName` en vez de `app_name`), que si se ignoraran pasarían sin aviso. Un error en un campo obligatorio, como `passage` en vez de `passages`, da 422 en los dos casos. Ignorarlos permitiría agregar campos sin desplegar ia primero | Backend |
| Textos fijos y aviso legal | Los textos de `no_passages`, `refused` y `not_covered`, y si se agrega un aviso de que el chat no reemplaza la asesoría legal, en el texto o en el widget | Equipo |
| Respuestas `uncited` | Mostrarlas con un aviso o tratarlas como sin respaldo | Backend y frontend |
| Marcadores `[n]` en pantalla | Mostrarlos como superíndices que llevan a la fuente de `done.citations`, o que el backend los quite al reenviar, sin cambiar el texto que guarda (así se pierde el vínculo de cada afirmación con su fuente). Si se muestran, vale el punto 6 de "Consumo desde el backend": el backend conserva `n` al convertir las citas, y un marcador sin cita se muestra sin enlace | Backend (4.7) y frontend (4.9) |
| Respuestas recortadas | Con `finish_reason` distinto de `stop`, mostrar el texto con un aviso de "respuesta recortada" y con sus citas, que son válidas | Backend y frontend |
| Keepalive hacia el navegador | Nombre y forma del evento propio del backend (por ejemplo `event: ping` con `data: {}`), que el frontend ignora | Backend (4.7) y frontend (4.9) |
| Caché de prompts | Elegir una de tres opciones: (a) modo implícito, aceptando y documentando como riesgo que OpenAI retenga el comienzo del prompt con los pasajes (vida mínima de 30 minutos, hasta 24 horas); (b) modo explícito con un único punto de corte al final de las instrucciones fijas, que conserva el ahorro en las instrucciones (desde `chat-v2` superan el mínimo que OpenAI cachea: unos 1.150 tokens en caché por llamada con `chat-v3`) y deja fuera los datos del pedido; (c) modo explícito sin puntos de corte, que no usa el caché. (b) y (c) valen desde gpt-5.6 según el SDK y no se comprobaron con OpenAI (ver "Privacidad"). Afecta también al endpoint de perfil, que hoy aprovecha ese caché: para (b) hay que partir su mensaje de usuario, que une etiquetas y descripción en una sola cadena, y poner el corte después de EXISTING LABELS, o al final del prompt de sistema si no hay etiquetas. El modo se fija en cada llamada, así que el chat y el perfil pueden elegir distinto. Medido el 2026-10-05 con el set de evaluación, en el modo implícito: un pedido idéntico repetido sacó del caché 1.570 de sus 1.573 tokens de entrada, pasajes incluidos, así que el riesgo de (a) ocurre en la práctica; y desde `chat-v2`, preguntas distintas comparten las instrucciones en caché (con `chat-v2`, de 24.936 a 44.011 tokens por corrida de 26 casos) | Equipo |
| Correlación de logs | Header opcional `X-Request-Id`, que ia repite en su log | Backend |
| Proxies | Confirmar que ningún proxy, entre el backend e ia o hacia el navegador, acumula el stream ni lo corta por inactividad. Con los `: ping` de ia y el keepalive del backend (punto 6), ningún tramo pasa más de 15 s sin bytes. Basta con que el tope de inactividad de cada proxy supere ese intervalo con margen: el `proxy_read_timeout` de nginx (60 s por defecto) alcanza | Equipo |
| Fragmento ilegible del proveedor | Hoy sale `internal` (`retryable: false`), por la regla de CHT-010 ("cualquier otra Exception, internal"), aunque "Errores durante el stream" describe `internal` como falla de ia. Opciones: mantenerlo y aclararlo en la tabla, o clasificarlo como `provider_error` (`retryable: true`), lo que cambia el reintento del punto 10 | Backend |
