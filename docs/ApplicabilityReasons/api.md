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
- desde `applicability-v2`, un análisis con lo que el artículo exige a la empresa: su naturaleza (obligaciones explícitas, informativo, remisión o información procedimental), sus obligaciones explícitas con el respaldo que acredita cada una, la norma que modifica una remisión y la autorización a la que sirve un artículo procedimental. Es C9 del plan del sprint 3, con el criterio del cliente del 2026-10-15 (decisión 21): las obligaciones sugeridas salen de aquí, nunca de `activity_action`, y solo es obligación lo que un respaldo acredita.

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
          "summary": "El titular de un depósito de relaves debe monitorear su estabilidad física e informar los resultados a la autoridad.",
          "evidence": "Informes de monitoreo de estabilidad entregados a la autoridad"
        }
      ],
      "referred_legal_body": null,
      "procedure_for": null
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
| nature | `explicit_obligations`, `informative`, `referral` o `procedural`, con las reglas de C9 (decisiones 7 y 21 del plan del sprint 3). La revisión de respaldos (`docs/EvidenceReview`) todavía usa las tres primeras: se igualan cuando adopte la decisión 21 |
| obligations | Con `explicit_obligations`, de 1 a 10 obligaciones, cada una con `title` (hasta 200 caracteres), `summary` (hasta 1000) y `evidence`, el respaldo que la acredita (hasta 300). Con las otras naturalezas, siempre vacía |
| referred_legal_body | Con `referral`, la norma que el artículo modifica, como la nombra el texto (hasta 300 caracteres), o `null` si no la nombra. Con las otras naturalezas, siempre `null` |
| procedure_for | Con `procedural`, la autorización, el permiso, la licencia o el registro a los que sirve el artículo, como los nombra el texto o la norma (hasta 300 caracteres), o `null` si no los nombra. Con las otras naturalezas, siempre `null` |

El backend de `capstone-2s` todavía no lee `analyses` (paso 0.18 del plan) y, como ignora los campos que no conoce, la respuesta nueva no lo afecta. `usage.cached_tokens` es la parte de `prompt_tokens` que el proveedor sirvió desde su caché, o 0 si no la informa; `dev_metrics` no lo repite. Cada campo del `usage` del proveedor se lee por separado, como en la clasificación de cuerpos legales: uno que falta o no es un entero positivo cuenta 0, sin hacer fallar la respuesta.

`dev_metrics` solo trae datos cuando `INCLUDE_DEV_METRICS` es verdadero.

**Reglas:**

- Una llamada al modelo por pedido. Timeout `APPLICABILITY_REASONS_TIMEOUT_SECONDS` (defecto 25) y `APPLICABILITY_REASONS_MAX_RETRIES` (defecto 0), para caber en los 30 s que espera el backend (`AI_APPLICABILITY_REASONS_TIMEOUT_SECONDS`).
- Se lee el cuerpo legal y **solo** los artículos pedidos. El resto de la norma no viaja al modelo.
- Un `article_id` que no está en esa norma se omite de `reasons` y de `analyses`. Si no queda ninguno, las dos listas vuelven vacías y no se llama al modelo. El cuerpo inexistente es `404`.
- El motivo va en español, en un párrafo de dos o tres oraciones. Nombra los valores coincidentes entre comillas («…»). No dictamina aplicabilidad. No cita ni inventa la descripción de la empresa: ese texto no llega en el pedido.
- Una obligación es lo que el artículo exige a la empresa y un respaldo puede acreditar: un documento o registro que la empresa mostraría para probar que cumplió, como una autorización que debe tener, un aviso que debe enviar o un registro que debe llevar. El modelo nombra el respaldo antes de listar la obligación; si no puede, no es una obligación.
- La naturaleza sale solo del texto del artículo, nunca de los valores coincidentes ni de las acciones. El modelo revisa las naturalezas en este orden y se queda con la primera que corresponde:
  1. `referral`: modifica otra ley o reglamento, aunque el texto agregado imponga una obligación o regule un trámite, porque ese contenido es de la otra ley. Un artículo que solo nombra otra ley, o dice que otra ley rige ciertos casos, no la modifica y no es una remisión: su naturaleza sale de lo que él mismo exige;
  2. `explicit_obligations`: impone al menos una obligación a la empresa, o al tipo de empresa al que se dirige (empleador, dueño, operador, titular de un permiso). Exigir una autorización, un permiso, una licencia o un registro impone la obligación de tenerlo, aunque el artículo también diga cómo pedirlo. Una prohibición cuenta solo si un respaldo acredita que se cumple, como la autorización que la levanta;
  3. `procedural`: solo regula cómo obtener, renovar o tramitar una autorización: la solicitud, los antecedentes que la acompañan, los pasos y plazos, o lo que la autoridad hace con la solicitud. Los antecedentes no son obligaciones, aunque el artículo diga que el solicitante «deberá» presentarlos;
  4. `informative`: cualquier otro. Lo es un artículo que solo obliga o faculta al Estado, salvo que regule una solicitud, y también uno que otorga un beneficio, condona una deuda, define términos, fija la entrada en vigencia o dice cómo se financia un gasto público.
- Las obligaciones salen solo del texto del artículo. Una acción de `activity_action` nunca se convierte en obligación, y una obligación que recae solo en el Estado no cuenta.
- La regla de no dictaminar («debe», «es aplicable») vale solo para el motivo: una obligación repite lo que exige el artículo.
- El servicio cierra en código lo que el modelo podría confundir:
  - un artículo informativo, de remisión o procedimental queda sin obligaciones;
  - solo una remisión conserva la norma que nombra, y solo un artículo procedimental la autorización a la que sirve;
  - una obligación sin título, sin resumen o sin respaldo, o que pasa de su tope, se descarta, y también la repetida;
  - un artículo con `explicit_obligations` y sin ninguna obligación utilizable queda sin análisis, así que el backend lo muestra sin evaluar;
  - un motivo, una obligación, una norma o una autorización con letras de un alfabeto que no aparece en el español legal (cirílico, árabe, hebreo o asiáticos) se descarta. El griego se permite por los símbolos técnicos.
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
- `applicability-v2` escribe más que la v1: un título, un resumen y un respaldo por obligación. El plazo de 25 s no cambió. La evaluación del paso 0.17 mide la latencia además del costo. Si el modelo se pasa con frecuencia, el síntoma es que las alertas vuelven a la plantilla y quedan sin análisis.
