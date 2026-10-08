# API — Módulo: Motivos de aplicabilidad

> Base path: `/api/v1`

Exige el header `X-API-Key` con el valor de `SERVICE_API_KEY`. Lee `legal_bodies` y `articles` (solo los artículos pedidos) mediante `DATABASE_URL`. No recibe ni envía al modelo el texto con que la empresa se describe.

---

## Escribir motivos de aplicabilidad

```text
POST /api/v1/applicability-reasons
```

**Descripción:** Recibe la app, la norma y, por artículo, los valores del perfil que coinciden y las acciones de `activity_action`. Lee el cuerpo legal y solo esos artículos, y devuelve un motivo en español por artículo. El backend usa ese texto al emitir la alerta; si un artículo no trae motivo, conserva la plantilla.

**Requerimiento relacionado:** APR-001, APR-002, APR-003, APR-004, APR-005

**Request:**

```json
{
  "legal_requirement_id": "7c8e9f01-2345-6789-abcd-ef0123456789",
  "legal_body_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "articles": [
    {
      "article_id": "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c",
      "matched_values": ["Minería", "Gestión de relaves"],
      "actions": ["Monitoreo de la estabilidad de los depósitos de relaves"]
    }
  ]
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| legal_requirement_id | UUID | Sí | Id de la app de Requisitos Legales. Se acepta para que el cuerpo del backend valide; este servicio no lee esa tabla y no envía el id al modelo |
| legal_body_id | UUID | Sí | Id de la fila en `legal_bodies` |
| articles | lista | Sí | 1 a 10 artículos. `article_id` no se puede repetir |
| articles[].article_id | UUID | Sí | Id de la fila en `articles` |
| articles[].matched_values | lista de string | Sí | Al menos 1, máximo 10. Los mismos valores que nombraría la plantilla del backend |
| articles[].actions | lista de string | No | Máximo 20. Valores de `activity_action` del artículo. Defecto: lista vacía |

Cada etiqueta se recorta, debe tener entre 1 y 100 caracteres, y no puede contener `|` ni saltos de línea. Campos extra se rechazan. Los duplicados de una misma lista se eliminan conservando el orden.

**Response `200 OK`:**

```json
{
  "reasons": [
    {
      "article_id": "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c",
      "reason": "Este artículo coincide con el perfil de tu empresa en «Minería» y «Gestión de relaves», y describe el monitoreo de la estabilidad de los depósitos de relaves."
    }
  ],
  "reason_version": "applicability-v1@gpt-5.6-luna",
  "usage": {
    "prompt_tokens": 900,
    "completion_tokens": 80,
    "total_tokens": 980,
    "llm_calls": 1
  },
  "dev_metrics": {
    "elapsed_ms": 2100.5,
    "prompt_tokens": 900,
    "completion_tokens": 80,
    "total_tokens": 980,
    "llm_calls": 1
  }
}
```

`reason_version` y `usage` viajan siempre. El backend solo usa `reasons`: un artículo sin entrada, con `reason` vacío o de más de 2000 caracteres, conserva la plantilla.

`dev_metrics` solo trae datos cuando `INCLUDE_DEV_METRICS` es verdadero.

**Reglas:**

- Una llamada al modelo por pedido. Timeout `APPLICABILITY_REASONS_TIMEOUT_SECONDS` (defecto 25) y `APPLICABILITY_REASONS_MAX_RETRIES` (defecto 0), para caber en los 30 s que espera el backend (`AI_APPLICABILITY_REASONS_TIMEOUT_SECONDS`).
- Se lee el cuerpo legal y **solo** los artículos pedidos. El resto de la norma no viaja al modelo.
- Un `article_id` que no está en esa norma se omite de `reasons`. Si no queda ninguno, `reasons` es `[]` y no se llama al modelo. El cuerpo inexistente es `404`.
- El motivo va en español, en un párrafo de dos o tres oraciones. Nombra los valores coincidentes entre comillas («…»). No dictamina aplicabilidad. No cita ni inventa la descripción de la empresa: ese texto no llega en el pedido.
- No menciona artículos que no vinieron en el pedido, ni ids internos, ni que el texto lo escribió una IA.
- Antes de armar el prompt se quitan las imágenes embebidas `data:image/...;base64,...`.
- El log registra el id de la norma y cuántos artículos pidieron motivo y cuántos lo obtuvieron. No registra etiquetas, acciones ni el texto del artículo.

**Errores:**

| Código | Cuándo |
|---|---|
| 401 | Falta `X-API-Key` o no coincide con `SERVICE_API_KEY` |
| 404 | No existe la fila en `legal_bodies` |
| 422 | Falta un campo, `articles` está vacío o pasa de 10, `article_id` repetido, `matched_values` vacío, o una etiqueta no cumple las reglas |
| 502 | El proveedor falló, el modelo no devolvió un JSON válido o lo rechazó |
| 503 | Falta `SERVICE_API_KEY`, `OPENAI_API_KEY` o `DATABASE_URL` |

**Limitaciones:**

- No persiste los motivos.
- No arma los valores coincidentes ni las acciones: los envía el backend.
- No lee el perfil de la empresa ni el texto declarado.
- El timeout del cliente es menor que el del backend a propósito: si el modelo se pasa, este servicio responde 502 y el backend deja la plantilla.
