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

## Dentro de alcance

- Llamar al endpoint de embeddings de OpenAI.
- Ordenar los vectores por el índice que devuelve el proveedor, para no depender del orden de la respuesta.
- Reportar `usage.total_tokens`.

## Fuera de alcance

- Armar el texto (prefijos de taxonomía, troceo de artículos). Lo hace el llamador.
- Guardar los vectores.
- Autenticación.
- Leer PostgreSQL. Este endpoint no usa `DATABASE_URL`.

## Deuda técnica conocida

- No hay reintento ante un 502.
- El tope de 3072 es el del esquema HTTP. El modelo configurado puede rechazar un tamaño menor con 422.
- No hay autenticación: quien alcance el puerto puede consumir la cuota del proveedor.
