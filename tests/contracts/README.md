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
| `documentary-situation.json` | `POST /api/v1/company-profiles/documentary-situation` |
| `chat.json` | `POST /api/v1/chat` |

## Formato

Cada archivo es un objeto con `endpoint` (`"<MÉTODO> <ruta>"`), `request` y `response`: los cuerpos tal como
viajan, con los campos y los `null` que envía el backend. Los datos son sintéticos.

Dos endpoints no intercambian un único objeto JSON:

- **`evidence-reviews.json`** (`multipart/form-data`): `request.review` es el JSON del campo `review`, y
  `request.files` describe cada parte `files` con su `filename`, su `content_type` y su contenido como `text`
  UTF-8. No hay archivos binarios.
- **`documentary-situation.json`** (`multipart/form-data`): igual, con el JSON en el campo `situation`.
- **`chat.json`** (server-sent events): `response` es el stream, `{"events": [{"event", "data"}, ...]}`, con
  eventos `delta` y un `done` final. `error_response` es otro stream que termina en `error`: un stream termina
  en uno de los dos, nunca en ambos.

`chat-limits.json` no es un contrato HTTP. Guarda los nueve topes del chat que el backend repite en
`ChatTurnRules`, `ChatHistory` y `ChatPassageLimits`: en `settings`, los valores por defecto de las variables
`CHAT_*`; en `fixed`, las constantes de `domain/chat.py`. Lo usa `ChatLimitsContractTests` del backend (paso 1.5).

## Sincronización con el backend

- **ia-cumplify es la fuente.** backend-cumplify guarda una copia de estos archivos en
  `Cumplify.Tests/AI/Contracts/` y la pasa por los lectores de `IaCumplifyClient`.
- **Un cambio de contrato se hace en los dos repositorios:** se actualiza el ejemplo aquí, se copia al
  backend, se actualizan en `ContractSyncTests` el commit de origen y la huella del archivo, y se corren las
  pruebas de contrato de ambos. El procedimiento está en `Cumplify.Tests/AI/Contracts/README.md` del backend.
- **Copias desactualizadas:** las pruebas de contrato de cada repositorio validan su copia contra su propio
  código. La comprobación de que las dos copias coinciden es `ContractSyncTests` del backend. Compara siempre
  cada copia con su huella SHA-256 en el commit de ia del que se copió (`SourceCommit` en esa prueba) y, si
  encuentra estos archivos (en `IA_CUMPLIFY_CONTRACTS_DIR` o en ia-cumplify clonado al lado del backend), los
  compara uno por uno. Sin ia-cumplify disponible, como en el CI del backend, esa segunda comparación se omite.
- **Fines de línea:** con `core.autocrlf` en Windows, una copia puede quedar con CRLF y la otra con LF. Esa
  diferencia no cambia el JSON: las copias se comparan por su contenido leído, no byte a byte.
