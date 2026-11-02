# Contratos compartidos con backend-cumplify

Ejemplos de pedido y de respuesta de cada endpoint que consume el backend (plan del sprint 4, paso 1.4). Fijan
el contrato HTTP entre los dos servicios: si un lado cambia un campo, el ejemplo deja de coincidir con el código
de ese lado y su prueba falla.

`tests/test_contracts.py` valida cada ejemplo contra los modelos Pydantic y contra el código que construye la
respuesta. Corre con `uv run pytest tests/test_contracts.py`, sin red, base de datos ni OpenAI.

## Endpoints

| Archivo | Endpoint |
|---|---|
| `embeddings.json` | `POST /api/v1/embeddings` |
| `legal-bodies-classify.json` | `POST /api/v1/legal-bodies/classify` |
| `company-profiles-classify.json` | `POST /api/v1/company-profiles/classify` |
| `applicability-reasons.json` | `POST /api/v1/applicability-reasons` |
| `action-plans.json` | `POST /api/v1/action-plans` |
| `evidence-reviews.json` | `POST /api/v1/evidence-reviews` |
| `chat.json` | `POST /api/v1/chat` |

## Formato

Cada archivo es un objeto con `endpoint` (`"<MÉTODO> <ruta>"`), `request` y `response`: los cuerpos tal como
viajan, con los campos y los `null` que envía el backend. Los datos son sintéticos.

Dos endpoints no intercambian un único objeto JSON:

- **`evidence-reviews.json`** (`multipart/form-data`): `request.review` es el JSON del campo `review`, y
  `request.files` describe cada parte `files` con su `filename`, su `content_type` y su contenido como `text`
  UTF-8. No hay archivos binarios.
- **`chat.json`** (server-sent events): `response` es el stream, `{"events": [{"event", "data"}, ...]}`, con
  eventos `delta` y un `done` final. `error_response` es otro stream que termina en `error`: un stream termina
  en uno de los dos, nunca en ambos.

`chat-limits.json` no es un contrato HTTP. Guarda los nueve topes del chat que el backend repite en
`ChatTurnRules`, `ChatHistory` y `ChatPassageLimits`: en `settings`, los valores por defecto de las variables
`CHAT_*`; en `fixed`, las constantes de `domain/chat.py`. Lo usará el paso 1.5.

## Sincronización con el backend

- **ia-cumplify es la fuente.** backend-cumplify guarda una copia de estos archivos en
  `Cumplify.Tests/AI/Contracts/` y la pasa por los lectores de `IaCumplifyClient`.
- **Un cambio de contrato se hace en los dos repositorios:** se actualiza el ejemplo aquí, se copia al
  backend y se corren las pruebas de contrato de ambos.
- **Las pruebas de cada repositorio no detectan por sí solas una copia desactualizada:** cada una valida su
  copia contra su propio código. La comprobación de que las dos copias coinciden queda pendiente en el
  backend.
- **Fines de línea:** con `core.autocrlf` en Windows, una copia puede quedar con CRLF y la otra con LF. Esa
  diferencia no cambia el JSON: las copias se comparan por su contenido leído, no byte a byte.
