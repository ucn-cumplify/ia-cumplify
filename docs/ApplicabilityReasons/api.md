# API — Módulo: Motivos de aplicabilidad

> Base path: `/api/v1`

Exige el header `X-API-Key` con el valor de `SERVICE_API_KEY`. Lee `legal_bodies` y `articles` (solo los artículos pedidos) mediante `DATABASE_URL`. No recibe ni envía al modelo el texto con que la empresa se describe.

---

## Escribir motivos de aplicabilidad

```text
POST /api/v1/applicability-reasons
```

**Descripción:** Recibe la app, la norma y, por artículo, los valores del perfil que coinciden y las acciones de `activity_action`. Lee el cuerpo legal y solo esos artículos. Por cada uno devuelve:
- un motivo en español, salvo que el pedido traiga `write_reason` en falso. El backend usa ese texto al emitir la alerta y, si un artículo no trae motivo, conserva la plantilla;
- desde `applicability-v2`, un análisis con lo que el artículo exige a la empresa: su naturaleza (obligaciones explícitas, informativo o remisión), sus obligaciones explícitas y la norma a la que remite. Es C9 del plan del sprint 3: las obligaciones sugeridas salen de aquí, nunca de `activity_action`.

**Requerimiento relacionado:** APR-001, APR-002, APR-003, APR-004, APR-005, APR-006, APR-007, APR-008

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
| articles[].matched_values | lista de string | Sí, si el artículo pide motivo | Máximo 10. Con motivo, al menos 1: los mismos valores que nombraría la plantilla del backend. Con `write_reason` en `false` puede ir vacía o faltar |
| articles[].actions | lista de string | No | Máximo 20. Valores de `activity_action` del artículo. Defecto: lista vacía |
| articles[].write_reason | booleano | No | Defecto: `true`. En `false`, el artículo no recibe motivo y solo vuelve su análisis. Sirve cuando el backend conserva su propio texto, como en un par difuso del nivel 2 o en una alerta que ya tiene su motivo |

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
  "analyses": [
    {
      "article_id": "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c",
      "nature": "explicit_obligations",
      "obligations": [
        {
          "title": "Monitorear la estabilidad de los depósitos de relaves",
          "summary": "El titular de un depósito de relaves debe monitorear su estabilidad física e informar los resultados a la autoridad."
        }
      ],
      "referred_legal_body": null
    }
  ],
  "reason_version": "applicability-v2@gpt-5.6-luna",
  "usage": {
    "prompt_tokens": 900,
    "completion_tokens": 80,
    "total_tokens": 980,
    "cached_tokens": 0,
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

`reason_version` y `usage` viajan siempre. Del motivo, un artículo sin entrada en `reasons`, con `reason` vacío o de más de 2000 caracteres, conserva la plantilla.

`analyses` trae como máximo un análisis por artículo pedido que exista en la norma, independiente de su motivo: un motivo descartado no se lleva el análisis. Un artículo que no aparece en `analyses` quedó sin evaluar.

| Campo | Descripción |
|---|---|
| nature | `explicit_obligations`, `informative` o `referral`, con las reglas de C9 (decisión 7 del plan del sprint 3). Son los mismos valores que la revisión de respaldos (`docs/EvidenceReview`) |
| obligations | Con `explicit_obligations`, de 1 a 10 obligaciones, cada una con `title` (hasta 200 caracteres) y `summary` (hasta 1000). Con `informative` y `referral`, siempre vacía |
| referred_legal_body | Con `referral`, la norma que el artículo modifica o a la que remite, como la nombra el texto (hasta 300 caracteres), o `null` si no la nombra. Con las otras naturalezas, siempre `null` |

El backend de `capstone-2s` todavía no lee `analyses` (paso 0.18 del plan) y, como ignora los campos que no conoce, la respuesta nueva no lo afecta. `usage.cached_tokens` es la parte de `prompt_tokens` que el proveedor sirvió desde su caché, o 0 si no la informa; `dev_metrics` no lo repite. Cada campo del `usage` del proveedor se lee por separado, como en la clasificación de cuerpos legales: uno que falta o no es un entero positivo cuenta 0, sin hacer fallar la respuesta.

`dev_metrics` solo trae datos cuando `INCLUDE_DEV_METRICS` es verdadero.

**Reglas:**

- Una llamada al modelo por pedido. Timeout `APPLICABILITY_REASONS_TIMEOUT_SECONDS` (defecto 25) y `APPLICABILITY_REASONS_MAX_RETRIES` (defecto 0), para caber en los 30 s que espera el backend (`AI_APPLICABILITY_REASONS_TIMEOUT_SECONDS`).
- Se lee el cuerpo legal y **solo** los artículos pedidos. El resto de la norma no viaja al modelo.
- Un `article_id` que no está en esa norma se omite de `reasons` y de `analyses`. Si no queda ninguno, las dos listas vuelven vacías y no se llama al modelo. El cuerpo inexistente es `404`.
- El motivo va en español, en un párrafo de dos o tres oraciones. Nombra los valores coincidentes entre comillas («…»). No dictamina aplicabilidad. No cita ni inventa la descripción de la empresa: ese texto no llega en el pedido.
- La naturaleza sale solo del texto del artículo, nunca de los valores coincidentes ni de las acciones:
  - `explicit_obligations`: impone una o más obligaciones a la empresa, o al tipo de empresa al que se dirige (empleador, dueño, operador, titular de un permiso). Una prohibición también cuenta;
  - `informative`: no impone obligaciones a la empresa. Lo es un artículo que solo obliga o faculta al Estado, y también uno que otorga un beneficio, condona una deuda, define términos, fija la entrada en vigencia o dice cómo se financia un gasto público;
  - `referral`: modifica otra ley o reglamento, aunque el texto agregado imponga una obligación, porque esa obligación es de la otra ley.
- Las obligaciones salen solo del texto del artículo. Una acción de `activity_action` nunca se convierte en obligación, y una obligación que recae solo en el Estado no cuenta.
- La regla de no dictaminar («debe», «es aplicable») vale solo para el motivo: una obligación repite lo que exige el artículo.
- El servicio cierra en código lo que el modelo podría confundir:
  - un artículo informativo o de remisión queda sin obligaciones;
  - solo una remisión conserva la norma que nombra;
  - una obligación sin título o sin resumen, o que pasa de su tope, se descarta, y también la repetida;
  - un artículo con `explicit_obligations` y sin ninguna obligación utilizable queda sin análisis, así que el backend lo muestra sin evaluar.
- No menciona artículos que no vinieron en el pedido, ni ids internos, ni que el texto lo escribió una IA.
- Antes de armar el prompt se quitan los data URI embebidos: una imagen se reemplaza por `[imagen omitida]` y cualquier otro tipo, como un PDF, por `[archivo omitido]`.
- El log registra el id de la norma, cuántos artículos llegaron y cuántos motivos y análisis volvieron. No registra etiquetas, acciones, obligaciones ni el texto del artículo.

**Errores:**

| Código | Cuándo |
|---|---|
| 401 | Falta `X-API-Key` o no coincide con `SERVICE_API_KEY` |
| 404 | No existe la fila en `legal_bodies` |
| 422 | Falta un campo, `articles` está vacío o pasa de 10, `article_id` repetido, `matched_values` vacío en un artículo que pide motivo, o una etiqueta no cumple las reglas |
| 502 | El proveedor falló, el modelo no devolvió un JSON válido o lo rechazó |
| 503 | Falta `SERVICE_API_KEY`, `OPENAI_API_KEY` o `DATABASE_URL` |

**Limitaciones:**

- No persiste los motivos ni los análisis.
- No arma los valores coincidentes ni las acciones: los envía el backend.
- No lee el perfil de la empresa ni el texto declarado.
- El timeout del cliente es menor que el del backend a propósito: si el modelo se pasa, este servicio responde 502 y el backend deja la plantilla.
- `applicability-v2` escribe más que la v1: un título y un resumen por obligación. El plazo de 25 s no cambió. La evaluación del paso 0.17 mide la latencia además del costo. Si el modelo se pasa con frecuencia, el síntoma es que las alertas vuelven a la plantilla y quedan sin análisis.
