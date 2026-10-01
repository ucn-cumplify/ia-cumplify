# API — Módulo: Embeddings

> Base path: `/api/v1/embeddings`

El servicio no exige `Authorization`. Este endpoint no consulta PostgreSQL.

---

## Crear embeddings

```text
POST /api/v1/embeddings
```

**Descripción:** Devuelve un vector por cada texto, en el mismo orden. El llamador decide el contenido de cada texto.

**Requerimiento relacionado:** EMB-001, EMB-002

**Request:**

```json
{
  "texts": [
    "ámbito: Medio Ambiente",
    "sector: Minería"
  ],
  "model": "text-embedding-3-large",
  "dimensions": 1024
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| texts | lista de string | Sí | Al menos 1 y como máximo `EMBEDDINGS_MAX_TEXTS` (defecto 256). Ninguno puede ser blanco. |
| model | string | No | Defecto: `OPENAI_EMBEDDING_MODEL` |
| dimensions | int | No | Entre 1 y 3072. Defecto: `OPENAI_EMBEDDING_DIMENSIONS` (1024) |

**Response `200 OK`:**

```json
{
  "model": "text-embedding-3-large",
  "dimensions": 1024,
  "vectors": [
    [0.012, -0.034],
    [0.056, 0.078]
  ],
  "usage": {
    "total_tokens": 12
  }
}
```

`vectors[i]` corresponde a `texts[i]`. `dimensions` es el largo del primer vector devuelto.

**Reglas:**

- El orden se toma del índice del proveedor, no del orden en que llegue el arreglo.
- Si el proveedor devuelve una cantidad distinta de vectores, la petición falla con 502. No se recorta ni se rellena.
- Cada llamada HTTP a OpenAI usa `OPENAI_TIMEOUT_SECONDS` (defecto 180) y `OPENAI_MAX_RETRIES` (defecto 2) para 408, 429 y 5xx.
- Los vectores no se guardan.

**Errores:**

| Código | Cuándo |
|---|---|
| 422 | `texts` vacío, texto en blanco, más textos que el máximo, `dimensions` fuera de 1..3072, modelo desconocido, tamaño no soportado, o texto sobre el límite de tokens |
| 502 | Fallo del proveedor, o cantidad de vectores distinta a la de textos |
| 503 | Falta `OPENAI_API_KEY` |

**Limitaciones:**

- No persiste los vectores.
- No lee la base de Cumplify.
- 1024 es el contrato con la columna `vector(1024)` del backend. Otro `dimensions` produce vectores que esa columna no puede guardar.
