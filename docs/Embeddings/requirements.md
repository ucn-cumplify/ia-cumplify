# Módulo: Embeddings

> Genera un vector por cada texto recibido, en el mismo orden, para que `backend-cumplify` los guarde.

---

## Objetivo

Ofrecer un endpoint genérico de embeddings. El backend arma los textos (por ejemplo, anteponiendo la dimensión de una etiqueta de taxonomía) y este servicio solo los convierte en vectores. Sirve para valores de taxonomía, valores de perfil y, más adelante, fragmentos de artículos, sin un endpoint por cada uso.

El tamaño por defecto es 1024, que es el de la columna `vector(1024)` del backend.

---

## Requerimientos

### EMB-001 — Embeber un lote de textos

| Campo | Detalle |
|---|---|
| **ID** | EMB-001 |
| **Rol** | Servicio interno. El endpoint no exige autenticación. |

**Descripción:**

El sistema debe devolver un vector por cada texto, en el mismo orden de la lista de entrada, junto con el modelo usado, el tamaño del vector y los tokens consumidos.

**Validaciones:**

- `texts` tiene al menos un elemento y como máximo `EMBEDDINGS_MAX_TEXTS` (defecto 256).
- Ningún texto puede quedar vacío después de recortar espacios.
- Si el request no trae `model`, se usa `OPENAI_EMBEDDING_MODEL` (defecto `text-embedding-3-large`).
- Si el request no trae `dimensions`, se usa `OPENAI_EMBEDDING_DIMENSIONS` (defecto 1024). `dimensions`, cuando viene, está entre 1 y 3072.
- La cantidad de vectores devueltos por el proveedor tiene que ser igual a la cantidad de textos. Si no coincide, la petición falla.

---

### EMB-002 — Separar un rechazo de entrada de un fallo del proveedor

| Campo | Detalle |
|---|---|
| **ID** | EMB-002 |
| **Rol** | Servicio interno |

**Descripción:**

Un modelo desconocido, un tamaño no soportado o un texto que supera el límite de tokens del modelo se responde como entrada inválida (422). Esos casos se repiten si se reintenta igual. Un fallo transitorio del proveedor se responde 502.

---

## Evaluación de recuperación

`scripts/retrieval_eval.py` es una herramienta manual, fuera del servicio y de pytest, que compara las recetas de embeddings de artículos que `backend-cumplify` guarda en `ai_embeddings`. La usa el caso AI-044 del backend (`docs/AI/test.csv` de `backend-cumplify`): antes de purgar las filas `art-v1`, comprobar que `art-v2` iguala o mejora el recall@k de `art-v1` en todos los tipos de pregunta y lo mejora en los artículos largos. En este repositorio la cubre EMB-007 de `test.csv`.

**Qué mide.** Para cada pregunta del conjunto y cada identificador de `--recipes` (por defecto `text-embedding-3-large@1024#art-v1`, un vector por artículo con el texto recortado a 12.000 caracteres, y `text-embedding-3-large@1024#art-v2`, trozos de hasta 4.000):

1. Embebe la pregunta con este endpoint, con el modelo y las dimensiones de las recetas (`--model` y `--dimensions`, por defecto `text-embedding-3-large` y 1024), en lotes de hasta `EMBEDDINGS_MAX_TEXTS` preguntas. Una receta de otro modelo o de otro tamaño se rechaza antes de gastar tokens: solo se comparan vectores del mismo espacio.
2. Busca de forma exacta, sin índice: compara la pregunta con todos los trozos de la receta (`entity_type` `article`) con la distancia coseno de pgvector (`<=>`), le da a cada artículo la distancia de su trozo más cercano y ordena todos los artículos de la base. Con `art-v2`, un artículo cuenta como recuperado si aparece cualquiera de sus trozos. Los empates exactos comparten la posición.
3. Calcula por receta, por tipo de pregunta y para todas juntas:
   - recall@k, para cada k de `--k` (por defecto 1, 5 y 10): la fracción de los artículos esperados de la pregunta que quedan entre los k primeros, promediada entre las preguntas;
   - MRR: el promedio de 1 / la posición del primer artículo esperado en el orden completo, o 0 si la receta no tiene vectores de ninguno.

**El conjunto de preguntas.** `scripts/retrieval_eval_set.json` tiene `version` (1), `description` y `questions`. Cada pregunta trae `id`, `type`, `question`, `expected` y `note`:

- `type` es `referencia` (nombra la norma y el artículo), `parafrasis` (pregunta por el contenido sin nombrarlos) o `largo` (el contenido está dentro de un artículo largo, que `art-v2` trocea y `art-v1` recorta).
- Cada artículo de `expected` se identifica igual en todos los ambientes: el `bcn_id` de la norma, el `order` del artículo (su posición, estable desde la hidratación de la BCN) y su `number` legible. Nunca un id local.
- Solo cuentan las normas públicas de la BCN: con `bcn_id` y con un título que no empieza con `[DEV]` ni `[PRUEBA E2E]`.
- Si un artículo esperado no está en la base, o el de esa posición tiene otro número, el script lo avisa y deja la pregunta fuera de las métricas, sin fallar. El número se compara sin «Artículo», tildes, signos de ordinal ni puntuación: «Art. 1º» equivale a «Artículo 1°».
- Si una receta no tiene vectores de un artículo esperado (por ejemplo, de uno que queda vacío sin sus imágenes), cuenta como no recuperado con esa receta, con un aviso.
- Hoy trae tres preguntas de ejemplo, marcadas en `note`, para probar el script. El conjunto real de AI-044 las reemplaza.

**Costo.** Solo los tokens de las preguntas: los vectores de los artículos ya están en `ai_embeddings` y no se vuelven a embeber. Son del orden de los caracteres de las preguntas divididos por 3,8, decenas de tokens por pregunta: las tres de ejemplo suman 358 caracteres, unos 100 tokens. El script suma y muestra el `usage.total_tokens` que devuelve el endpoint.

**Uso.** Con un ia-cumplify en marcha y la base local:

```bash
export SERVICE_API_KEY=...        # la misma del servicio
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/cumplify_db
uv run python scripts/retrieval_eval.py --base-url http://127.0.0.1:8000
```

| Opción | Defecto | Uso |
|---|---|---|
| `--recipes ID ...` | `art-v1` y `art-v2` de `text-embedding-3-large@1024` | Identificadores de `embedding_model` que se comparan |
| `--k K ...` | `1 5 10` | Cortes de recall@k |
| `--question ID` | Todas | Solo esa pregunta; se puede repetir |
| `--top N` | El mayor k | Primeros resultados que guarda `--out` por pregunta y receta |
| `--out FILE` | Un archivo del directorio temporal | Detalle por pregunta; tiene que quedar fuera del repositorio, en un directorio que exista |
| `--batch N` | `EMBEDDINGS_MAX_TEXTS` o 256 | Preguntas por pedido al endpoint |
| `--self-test` | | Comprueba el cálculo de las métricas con datos inventados, sin servicio ni base |

- `SERVICE_API_KEY` y `DATABASE_URL` van exportadas o como argumentos (`--api-key`, `--database-url`): el script no lee `.env`. La base se lee con una conexión propia de solo lectura; el endpoint sigue sin usar `DATABASE_URL`.
- Antes del primer pedido comprueba que se puede escribir `--out`, se conecta a la base, comprueba que cada receta tiene vectores y resuelve los artículos esperados: un error de configuración no gasta tokens. Si no puede conectarse, muestra solo el tipo del error de psycopg, porque su mensaje puede repetir `DATABASE_URL` con la contraseña.
- Por consola muestra el corpus de cada receta (artículos, trozos, los de normas que no son públicas de la BCN y los que cambiaron de texto desde que se embebieron), los tokens, los avisos, la posición de los artículos esperados en cada pregunta y la tabla de recall@k y MRR por tipo y receta. El detalle por pregunta va a `--out`: la posición, la distancia y el trozo más cercano de cada artículo esperado, y los primeros resultados de cada receta. Lo guarda después de mostrar el informe, para que una falla al escribirlo no se lleve las métricas ya pagadas.
- El orden incluye todos los artículos de la base. En la base local también compiten las normas de prueba (`[DEV]`, `[PRUEBA E2E]`) y las privadas de una empresa, que pueden quedar delante del artículo esperado; en el detalle llevan `public_bcn` en `false`.
- Termina con código 0 aunque deje preguntas fuera. Termina con 2 ante un error de configuración antes del primer pedido, sin gastar tokens: argumentos, conjunto, `--out`, conexión o consultas a la base, o recetas sin vectores. Termina con 1 si falla el endpoint o la base durante la evaluación, después de mostrar los tokens gastados, o si no puede guardar `--out` al final, después de mostrar el informe.

---

## Dentro de alcance

- Llamar al endpoint de embeddings de OpenAI.
- Usar timeout y reintentos explícitos del cliente OpenAI (`OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`).
- Ordenar los vectores por el índice que devuelve el proveedor, para no depender del orden de la respuesta.
- Reportar `usage.total_tokens`.
- El script manual de evaluación de recuperación (`scripts/retrieval_eval.py`), descrito en "Evaluación de recuperación".

## Fuera de alcance

- Armar el texto (prefijos de taxonomía, troceo de artículos). Lo hace el llamador.
- Guardar los vectores.
- Autenticación.
- Leer PostgreSQL. Este endpoint no usa `DATABASE_URL`; solo el script de evaluación de recuperación lee la base, con su propia conexión de solo lectura.

## Deuda técnica conocida

- El cliente OpenAI reintenta 408, 409, 429, 5xx, timeouts y errores de conexión según `OPENAI_MAX_RETRIES` antes de responder 502.
- El tope de 3072 es el del esquema HTTP. El modelo configurado puede rechazar un tamaño menor con 422.
