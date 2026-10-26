# API — Módulo: Extracción de documentos

> Base path: `/api/v1`

Exige el header `X-API-Key` con el valor de `SERVICE_API_KEY`. No lee la base y no llama al modelo: devuelve el texto del archivo que viene en el pedido.

> **Contrato provisional.** Se ajusta cuando `backend-cumplify` implemente la carga de la base (paso 2B.6 del plan del sprint 3). Hoy no hay un cliente que lo consuma.

---

## Extraer el texto de un documento

```text
POST /api/v1/documents/extract
Content-Type: multipart/form-data
```

**Descripción:** Lee un documento de la base de conocimientos con el mismo extractor de la revisión de respaldos y devuelve su estado, su texto y sus fragmentos (páginas, hojas o diapositivas), para que el backend lo indexe con `KnowledgeBaseEmbedder.IndexAsync`. Un documento nunca vuelve cortado.

**Requerimiento relacionado:** DOC-001, DOC-002, DOC-003, DOC-004, DOC-005

**Request:**

Una sola parte, `file`. El nombre subido es obligatorio y una ruta se reduce a su último segmento. La extensión decide el lector.

```text
--…
Content-Disposition: form-data; name="file"; filename="procedimiento-epp.pdf"
Content-Type: application/pdf

<bytes>
--…--
```

**Response `200 OK`:**

```json
{
  "filename": "procedimiento-epp.pdf",
  "status": "read",
  "detail": null,
  "text": "Procedimiento de entrega de EPP…\n\nRegistro de entrega firmado…",
  "fragments": [
    { "locator": "page", "reference": "1", "text": "Procedimiento de entrega de EPP…" },
    { "locator": "page", "reference": "3", "text": "Registro de entrega firmado…" }
  ]
}
```

Un documento que no se puede indexar también responde 200, sin texto:

```json
{
  "filename": "manual.pdf",
  "status": "error",
  "detail": "The text is longer than 200000 characters.",
  "text": null,
  "fragments": []
}
```

**Estados:**

| status | Cuándo | `text` y `fragments` |
|---|---|---|
| read | Se leyó texto | El texto y los fragmentos con texto |
| unreadable | No hay texto legible: un PDF escaneado o una imagen | `null` y `[]` |
| unsupported | La extensión no es PDF, Word (`.docx`), Excel (`.xlsx`), CSV ni PowerPoint (`.pptx`) | `null` y `[]` |
| error | El archivo está roto o cifrado, pasa un tope de páginas, filas o ZIP, o su texto pasa de 200 000 caracteres | `null` y `[]` |

**Consolidación de `text`:** son los `text` de `fragments`, en orden, unidos con una línea en blanco (`"\n\n"`). `fragments` solo trae los fragmentos con texto: una página en blanco no aparece, y los demás conservan su número. La línea en blanco es el salto de párrafo con que corta el backend (`ArticleChunker`), así que una página nunca se junta con la siguiente.

**Ubicaciones:** `page` y `slide` llevan el número desde 1; `sheet`, el nombre de la hoja; `body` (Word y CSV), `reference` vacía.

**`detail`:** solo en `error`, con uno de dos textos fijos:

| detail | Cuándo |
|---|---|
| `The file is damaged or encrypted, or it passes a page, row or archive limit.` | El extractor devolvió `error` |
| `The text is longer than 200000 characters.` | El extractor cortó el texto, o el texto unido pasa de 200 000 unidades UTF-16 |

El mensaje propio del extractor no se devuelve: un parser puede citar el archivo en su excepción (DOC-004).

**Topes:**

| Tope | Valor | Origen |
|---|---|---|
| Archivos por pedido | 1 | Decisión técnica de este paso |
| Tamaño del archivo | 25 MB | Decisión técnica, provisional (el de la revisión de respaldos es por pedido) |
| Texto | 200 000 caracteres, contados como el extractor y también en UTF-16 sobre `text` | El máximo por documento del backend (`EmbeddingRequestPlanner.MaxChars`) |
| Páginas o diapositivas, filas, tamaño sin comprimir, entradas de ZIP | 100, 10 000, 64 MB, 4 096 | Los de la revisión de respaldos (`ReadLimits`). Provisionales para la base |

La lectura se ejecuta en el pool de hilos, fuera del bucle de eventos. El log registra los bytes, el estado y la cantidad de fragmentos; no registra el nombre del archivo ni su texto.

**Errores:**

| Código | Cuándo |
|---|---|
| 401 | Falta `X-API-Key` o no coincide con `SERVICE_API_KEY` |
| 422 | No llega la parte `file`, llega más de una, el nombre está vacío o pasa de 255 caracteres, o el archivo pasa de 25 MB. El mensaje no repite el contenido |
| 503 | Falta `SERVICE_API_KEY`. No exige `OPENAI_API_KEY` |

**Limitaciones:**

- No hace OCR. Una imagen o un PDF escaneado queda `unreadable`.
- No lee `.doc`, `.xls` ni `.ppt`.
- No guarda el documento ni lo indexa: eso es el backend (pasos 2B.6 y 2B.8).
