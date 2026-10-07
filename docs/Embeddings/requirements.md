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

`scripts/retrieval_eval.py` es una herramienta manual, fuera del servicio y de pytest, que compara las recetas de embeddings de artículos que `backend-cumplify` guarda en `ai_embeddings`. La usa el caso AI-044 del backend (`docs/AI/test.csv` de `backend-cumplify`; AI-040 a AI-044 y la receta `art-v2` están en la rama `feat/ai-article-chunking` del backend, todavía sin integrar): antes de purgar las filas `art-v1`, comprobar que `art-v2` iguala o mejora el recall@k de `art-v1` en todos los tipos de pregunta y lo mejora en los artículos largos. En este repositorio la cubre EMB-007 de `test.csv`.

**Qué mide.** Para cada pregunta del conjunto y cada identificador de `--recipes` (por defecto `text-embedding-3-large@1024#art-v1`, un vector por artículo con el texto recortado a 12.000 caracteres, y `text-embedding-3-large@1024#art-v2`, trozos de hasta 4.000):

1. Embebe la pregunta con este endpoint, con el modelo y las dimensiones de las recetas (`--model` y `--dimensions`, por defecto `text-embedding-3-large` y 1024), en lotes de hasta `EMBEDDINGS_MAX_TEXTS` preguntas. Una receta de otro modelo o de otro tamaño se rechaza antes de gastar tokens: solo se comparan vectores del mismo espacio.
2. Busca de forma exacta, sin índice: compara la pregunta con todos los trozos de la receta (`entity_type` `article`) con la distancia coseno de pgvector (`<=>`), le da a cada artículo la distancia de su trozo más cercano y ordena todos los artículos de normas públicas de la BCN. Con `art-v2`, un artículo cuenta como recuperado si aparece cualquiera de sus trozos. Los empates exactos comparten la posición.
3. Calcula por receta, por tipo de pregunta y para todas juntas:
   - recall@k, para cada k de `--k` (por defecto 1, 5 y 10): la fracción de los artículos esperados de la pregunta que quedan entre los k primeros, promediada entre las preguntas;
   - MRR: el promedio de 1 / la posición del primer artículo esperado en el orden completo, o 0 si la receta no tiene vectores de ninguno.

**El conjunto de preguntas.** `scripts/retrieval_eval_set.json` tiene `version` (1), `description` y `questions`. Cada pregunta trae `id`, `type`, `question`, `expected` y `note`:

- `type` es `referencia` (nombra la norma y el artículo), `parafrasis` (pregunta por el contenido sin nombrarlos) o `largo` (el contenido está dentro de un artículo largo, que `art-v2` trocea y `art-v1` recorta).
- Cada artículo de `expected` se identifica igual en todos los ambientes: el `bcn_id` de la norma, el `order` del artículo (su posición, estable desde la hidratación de la BCN) y su `number` legible. Nunca un id local.
- Solo cuentan las normas públicas de la BCN, entre los artículos esperados y en el orden: con `bcn_id`, sin empresa (`company_id` nulo, porque el backend también deja que una norma privada lleve `bcn_id`) y con un título que no empieza con `[DEV]` ni `[PRUEBA E2E]`.
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
- Por consola muestra el corpus de cada receta (los artículos de normas públicas de la BCN, sus trozos, los que cambiaron de texto desde que se embebieron y los de otras normas, que quedan fuera del orden), los tokens, los avisos, la posición de los artículos esperados en cada pregunta y la tabla de recall@k y MRR por tipo y receta. El detalle por pregunta va a `--out`: la posición, la distancia y el trozo más cercano de cada artículo esperado, y los primeros resultados de cada receta. Lo guarda después de mostrar el informe, para que una falla al escribirlo no se lleve las métricas ya pagadas.
- El orden incluye solo los artículos de normas públicas de la BCN. Las normas de prueba de la base local (`[DEV]`, `[PRUEBA E2E]`) y las privadas de una empresa no compiten: así las métricas no dependen del ambiente y `--out` no guarda datos de empresas.
- Termina con código 0 aunque deje preguntas fuera. Termina con 2 ante un error de configuración antes del primer pedido, sin gastar tokens: argumentos, conjunto, `--out`, conexión o consultas a la base, o recetas sin vectores. Termina con 1 si falla el endpoint o la base durante la evaluación, después de mostrar los tokens gastados, o si no puede guardar `--out` al final, después de mostrar el informe.

---

## Evaluación del perfil embebido

`scripts/profile_retrieval_eval.py` es la fase 0 de la tarea 2.4 del backend (recuperación semántica en el cruce; tareas 2.3 y 2.4 de `docs/AI/plan-sprint-2-chatbot.md` de `backend-cumplify`). Es una herramienta manual, fuera del servicio y de pytest, sin código de producción. Antes de escribir el código del backend, mide si el vector del perfil de una app, armado con la receta `prof-v1` de la tarea 2.3, se parece más a los artículos que importan a la app que a los demás. La cubre EMB-008 de `test.csv`.

No reimplementa el puntaje del cruce. La combinación con la taxonomía, el peso de la evidencia semántica y la compuerta se miden después en la simulación del backend, con los vectores que este script guarda (`--vectors-out`).

### La receta `prof-v1`

Es una reimplementación declarada, solo del texto. La PR del backend que implemente `prof-v1` tiene que dar el mismo texto y el mismo SHA-256:

- para las apps de demostración de una misma base: `--dry-run` muestra sus SHA-256 por consola y guarda los textos en `--out`;
- para los casos de `scripts/profile_text_parity.json`, que no necesitan base.

El plan del backend deja el detalle del recorte por el total a la primera PR que se escriba entre la de este script y la del backend, y prefiere la de este script («Selección» en `docs/AI/plan-recuperacion-semantica.md`). Lo fija la regla 8, y la PR del backend la replica como las demás. Como dice el plan, toda la receta queda provisional hasta repetir la medición con textos declarados («Con textos declarados», más abajo). Si esa medición la cambia, se actualizan juntos estas reglas, el script y los casos de paridad, y la PR del backend vuelve a replicarlos.

**El `--dry-run` de referencia.** Para que el SHA-256 de cada app de demostración tenga que coincidir con el que arma el backend, el `--dry-run` se hace:

- justo después de una ejecución de sugerencias, sin cambios posteriores en la ficha ni en las unidades de control de las empresas. El script lee el structured guardado, y cada ejecución lo regenera desde la ficha y las unidades de control (`CompanyProfileRegenerator`): un structured desactualizado no lo puede detectar. En cambio, el derived lo rearma desde las vinculaciones y el `family_article_count` lo cuenta en vivo, y en los dos casos avisa si no coincide con lo guardado;
- con las variables `AI_SCORE_*` del backend en sus valores por defecto: el script usa esos valores fijos, y el backend, los de su configuración.

Si no se cumplen, los SHA-256 no tienen por qué coincidir.

Cada regla cambia el hash:

1. **Unidad.** Un texto por app elegible: de Requisitos Legales (`app_type = legal_requirements`), que no es de catálogo, de una empresa `Activa` que no es la plantilla.
2. **Entradas.** Las de la empresa (declared, structured y excluded, con `legal_requirement_id` nulo) más el derived de la app, todas resueltas a la raíz de su familia (`COALESCE(canonical_id, id)`), como `CompanyProfileQueries.LoadEntriesAsync`. El script rearma el derived desde las vinculaciones de la app, como el regenerador del perfil, y avisa si no coincide con el guardado: el rearmado es el que dejaría la próxima ejecución.
3. **Pesos.** Los de `SuggestionScoring.ProfileSourceWeights`, con los valores por defecto de `AI_SCORE_*`:
   - declared, 1,0;
   - structured, 0,4;
   - derived, 0,5 + 0,5 · mín(1, ln(1 + n) / ln 6), con n = artículos vinculados distintos que traen algún valor de la familia.

   Se toma el máximo entre procedencias. En un empate gana declared, después derived y al final structured. Una entrada excluded quita toda la familia.
4. **Dimensiones**, en este orden: `scope`, `productive_sector`, `activity_action` y `facility_installation_equipment`, con las etiquetas de tax-v1: «Ámbito regulatorio», «Sector productivo», «Actividad» y «Obra, instalación o equipo». Nunca `territorial_coverage` ni `others`.
5. **Valor.** El `value` de la raíz, tal como está guardado. Nunca `display_value`, que cambia sin subir ninguna versión.
6. **Orden dentro de una dimensión.**
   - El peso, de mayor a menor.
   - Después, `family_article_count`, de menor a mayor. Es el IDF de la familia de mayor a menor: se compara el entero para no depender del redondeo. Se cuenta en vivo, con el SQL de `RecalculateFamilyArticleCountsAsync`, que es lo que recalcula la ejecución antes del cruce.
   - Al final, el `value` en orden ordinal por unidades UTF-16: `string.CompareOrdinal` en C#; en Python, comparar `value.encode("utf-16-be")`. No es el orden alfabético de una cultura, que depende de la versión de ICU, ni el de los puntos de código de Python, que difiere cuando hay caracteres fuera del plano básico.

   La procedencia no entra en el orden, como en el desempate del plan del backend. Con los pesos por defecto, un derived con cinco artículos vinculados o más pesa 1,0, lo mismo que un declared, y entre ellos decide `family_article_count`: un declared con una familia más grande queda después, y el tope del punto 7 lo puede dejar fuera (P8).
7. **Topes por dimensión.** 20 de ámbito, 10 de sector, 30 de actividad y 20 de instalación: quedan los primeros del orden del punto 6.
8. **Tope de largo.** El plan del backend fija 8.000 caracteres en total, y el detalle del recorte es este:
   - 8.000 unidades UTF-16: `string.Length` en C#; en Python, `len(texto.encode("utf-16-le")) // 2`;
   - mientras el texto supere el tope, se quita el último valor de un orden global: el peso, de mayor a menor; `family_article_count`, de menor a mayor; el orden de las dimensiones del punto 4; y el `value`, en orden ordinal UTF-16.

   Como en el punto 6, la procedencia no entra en este orden: con el mismo peso, el recorte quita antes un declared con una familia más grande que un derived saturado (P9).
9. **Texto.** Una línea por dimensión con valores, en el orden del punto 4: `etiqueta: valor; valor`, con los valores en orden ordinal UTF-16 y separados por `"; "`. Una dimensión sin valores no deja línea. Las líneas se unen con `"\n"`, sin salto al final.
10. **Señal mínima.** Si ninguna raíz con peso de las dimensiones del punto 4 tiene una entrada declared o derived, gane la procedencia que gane, la app no tiene texto ni vector. Se evalúa antes de los topes, como dice el plan de la recuperación semántica del backend (`docs/AI/plan-recuperacion-semantica.md`): «sin al menos una raíz con entrada declared o derived en esas cuatro dimensiones».
    - Con los pesos por defecto da lo mismo evaluarla antes o después de los topes, o mirar la procedencia ganadora. Declared (1,0) y derived (0,5 o más) siempre quedan antes que structured (0,4) en su dimensión, y el recorte por largo quita primero lo de menor peso.
    - Con otros pesos, por ejemplo con `AI_SCORE_W_SRC_STRUCTURED` mayor o igual que `AI_SCORE_W_SRC_DERIVED_MIN`, esas lecturas se separan. El caso P4 lo fija con pesos propios.
11. **Hash.** SHA-256 del texto en UTF-8, en hexadecimal en minúsculas, como el `content_hash` de `ai_embeddings`. El identificador de la receta es `text-embedding-3-large@1024#prof-v1`.

**Casos de paridad.** `scripts/profile_text_parity.json` trae nueve casos. Cada uno tiene:

- las entradas del perfil (`root`, `dimension` y `source`);
- el `value` y el `family_article_count` de cada raíz;
- las ocurrencias de derived por raíz;
- `settings`, solo si el caso usa pesos propios: los aplica en lugar de los de la raíz del archivo, que son los valores por defecto de `AI_SCORE_W_SRC_*` y `AI_SCORE_DERIVED_SATURATION`;
- el texto, el SHA-256 y el largo esperados.

Los ids son inventados y no influyen en el texto. Tienen la forma de un UUID, como el `Guid` de `ProfileValue` en el backend, y cada caso usa su propio rango.

| Caso | Qué fija |
|---|---|
| P1 | Exclusión por familia; una raíz con dos procedencias aparece una vez; tope de sector con empate de peso resuelto por `family_article_count`; orden ordinal UTF-16 dentro de la línea («Área portuaria» al final; un valor con un carácter fuera del plano básico antes que uno con U+FF27); territorio y `others` fuera; tope de instalaciones. Sin recorte por largo: 6.551 unidades UTF-16 y 6.550 caracteres de Python |
| P2 | Sin señal mínima: solo un structured en las cuatro dimensiones. Sin texto |
| P3 | Un solo valor declared: la forma mínima del texto |
| P4 | Señal mínima con pesos propios (structured 0,8): la única raíz con una entrada derived gana como structured y queda fuera por el tope de sector. Igual hay texto, solo con valores structured, porque la señal se evalúa antes de los topes y basta la entrada |
| P5 | Los tres criterios del punto 6 al borde de los topes, con las entradas en un orden que no es el del resultado. En sector, un structured con la familia más chica queda fuera porque pesa menos, y de tres derived empatados en peso y en conteo queda solo el primero en orden ordinal UTF-16, que no es el de entrada ni el alfabético de una cultura. En actividad, de dos empatados queda el que va primero en unidades UTF-16 y no en puntos de código |
| P6 | Recorte por largo en dos pasos. Primero sale el último del orden global, un valor de ámbito, aunque no está en la última línea. Con eso el texto mide 8.001 unidades UTF-16 y 8.000 caracteres de Python, así que se sigue recortando. Después el orden global decide entre tres derived con el mismo peso y el mismo conteo, por la dimensión y luego por el `value`. Un declared con una familia más grande que la del valor que sale se queda porque pesa más |
| P7 | Texto de exactamente 8.000 unidades UTF-16: no se recorta |
| P8 | El tope de ámbito exacto, con 22 raíces de peso 1,0: un derived con nueve artículos pesa lo mismo que uno con cinco, porque el peso se satura, y entre pesos iguales decide `family_article_count` y no la procedencia. Quedan las 20 raíces con las familias más chicas, y salen un declared y el derived con nueve artículos, que tienen las más grandes. Con un tope de 19 o de 21, con la procedencia antes del conteo o con un derived sin saturar, el texto sería otro |
| P9 | En el recorte por largo, un empate de peso 1,0 entre procedencias de dimensiones distintas: decide `family_article_count`, y no la procedencia ni la dimensión. Sale el declared de ámbito, que tiene la familia más grande, y no el derived saturado de sector. La línea de ámbito queda sin valores y desaparece |

La autoprueba los comprueba. La PR del backend tiene que cargarlos todos en una prueba de la función pura de la receta, con los pesos de cada caso.

### Qué mide

Para cada app elegible de Norte, Altiplano y Litoral, o para las de `--apps`:

1. **Corpus.** Los artículos de normas públicas, con trozos `art-v2` del texto actual del artículo (su `content_hash` es el SHA-256 del texto) y sin las filas «Encabezado» y «Promulgación».
   - Una norma es pública con la regla de visibilidad del cruce: sin empresa, y global o con `bcn_id`. Es la misma expresión que la constante `LegalBodyPublic` que propone la tarea 2.4.
   - A diferencia de `retrieval_eval.py`, cuentan también las normas globales que no vienen de la BCN.
   - Tampoco deja fuera las normas de prueba (`[DEV]`, `[PRUEBA E2E]`): el cruce no las excluye, y la norma de control de AI-036 es una. El informe las marca.
   - Las empresas de demostración se buscan por RUT, como `AiSuggestionsDemoSeeder`.
2. **Similitud.** La búsqueda es exacta, sin índice. Por artículo, cuenta el coseno con su trozo vigente más cercano. Por norma, el de su mejor artículo, que es la métrica principal, y el promedio de sus tres mejores artículos, que es un agregado de similitud y no el score del cruce.
3. **Grupos, por app.** El filtro territorial y el puntaje no se aplican: el script no reimplementa el cruce.
   - **Seguidas:** las normas públicas que la app sigue.
   - **Candidatas de la taxonomía:** las de `CandidateNormsAsync` en un cruce completo. Son las normas con un artículo clasificado con una raíz del perfil de una dimensión con peso (ámbito, sector o territorio), sin el país, y que no están seguidas, notificadas ni descartadas en la app.
   - **Relacionadas fuera del cruce:** las normas con un artículo clasificado con una raíz del perfil que va al texto pero no genera candidatas, porque su dimensión no pesa en el cruce (actividad o instalación). Tampoco están seguidas, notificadas ni descartadas, y no son candidatas. Su contenido coincide con una línea del perfil, así que no sirven de control negativo: se informan aparte.
   - **Sin relación:** las demás, sin las notificadas ni las descartadas. Es el control negativo: de sus percentiles salen el piso provisional y el control entre rubros.
   - **Sin trozos vigentes.** Las candidatas y las seguidas públicas sin trozos `art-v2` vigentes quedan fuera de los grupos. El informe de cada app las cuenta (`candidatas_total`, `candidatas_sin_trozos` y `seguidas_publicas_sin_trozos`). Avisa si falta más de un cuarto de las candidatas, o cualquier seguida pública.
4. **(a) Distribución del coseno por grupo.**
   - Por artículo y por norma: cantidad, media, mínimo, percentiles del 5 al 95 y máximo.
   - El AUC de las seguidas, el de las candidatas y el de las relacionadas fuera del cruce contra las sin relación: la probabilidad de que un artículo del grupo tenga más similitud que uno sin relación. 0,5 es no separar.
   - Con `prof-v1`, cuántos artículos de cada grupo caen en cada tramo de 0,05, con una muestra fija de dos por grupo y tramo para la revisión humana: norma, artículo, coseno y el comienzo del trozo.

   El grupo de seguidas está contaminado por construcción, porque el vector sale de sus artículos. La medida limpia es la del punto 5.
5. **(b) Evaluación que deja una norma afuera.** Por cada norma seguida, el derived se rearma sin sus artículos y el texto se vuelve a embeber.
   - Se registra el puesto de la norma apartada, por similitud sola, entre las normas públicas del corpus que la app no sigue, más la apartada. Los empates exactos comparten el puesto.
   - También se registra su percentil entre las sin relación.
   - Por variante, se resume con la mediana del puesto, el MRR y el recall@1, 5, 10 y 25.
   - Una norma seguida que no es pública, o que no tiene trozos vigentes, se informa y no se cuenta.
   - Los artículos se apartan con su propia norma (`articles.legal_body_id`), y las normas seguidas salen de las vinculaciones (`source_id`). El backend no valida que el artículo de una vinculación sea de su norma, así que el script avisa cuántos artículos vinculados son de otra norma: al apartar la de la vinculación, el derived los conserva.
6. **(c) Variantes** (`--variants`):

   | Variante | Qué cambia |
   |---|---|
   | `prof-v1` | La receta. Va siempre: es la referencia |
   | `sin-actividades` | Sin la línea de actividades |
   | `sin-etiquetas` | Solo los valores en cada línea, sin la etiqueta |
   | `por-empresa` | Solo declared y structured, sin el derived de la app. No exige la señal mínima: sin textos declarados, mide el supuesto de que un texto de structured se comporta como tax-v1. El informe dice si `prof-v1` lo aceptaría |
   | `centroide-tax` | El promedio de los vectores tax-v1 ya guardados de los valores que elige `prof-v1`, ponderado por su peso. No gasta tokens |
   | `tope-alto` | Topes por dimensión y de largo al doble |
   | `tope-proporcional` | Los 80 valores de los topes se reparten entre las dimensiones en proporción a los valores disponibles, por el mayor resto (empates en el orden de las dimensiones). Si caben todos, cada dimensión toma los suyos |

   `tope-proporcional` es el reparto proporcional puro que pide el plan. Puede dejar ámbito y sector, las dimensiones que pesan en el cruce, por debajo de lo que conserva `prof-v1`: con 30, 6, 110 y 60 valores disponibles da 12, 2, 43 y 23, y `prof-v1` conserva 20, 6, 30 y 20. Queda para decidir con el equipo un reparto con relleno: cada dimensión toma lo menor entre sus disponibles y su parte del total, y lo que sobra se reparte entre las dimensiones que todavía tienen valores. Si se elige, se documenta aquí.

   Con dos apps o más, un caso sintético une sus perfiles para que los topes corten. Se mide con `prof-v1`, `tope-alto` y `tope-proporcional`. Si la unión no supera ningún tope, el informe lo muestra con cero valores quitados.
7. **(d) Control entre rubros**, con cada variante.
   - Las normas no seguidas cuyo título trae una palabra clave de otra empresa de demostración y ninguna de la propia. Las palabras son las de `AiSuggestionsDemoCatalog` y se comparan sin tildes ni mayúsculas.
   - Cuántas quedan en el top 10 y en el top 25, y sobre los percentiles 90 y 95 de las sin relación.
   - La norma de control de AI-036, la que trae «emblema» en el título, con su puesto y su percentil. Se busca por el título porque su id cambia con la base.

### Costo y uso

**Costo.** Cada texto distinto se embebe una sola vez, en su propio pedido, para conocer sus tokens.

- Un texto por app y variante, más uno por norma seguida y variante en (b).
- `centroide-tax` no gasta tokens.
- Las variantes que dejan el texto igual (`tope-alto` y `tope-proporcional` cuando nada se corta, o `por-empresa` en (b)) no vuelven a pagar.

`--dry-run` cuenta los textos distintos y estima los tokens con 3,8 caracteres por token. En la base local se esperan decenas de miles de tokens, menos de un centavo de dólar.

**Con textos declarados.** Ningún seeder del backend crea textos de perfil, así que sin cargarlos la receta queda provisional. Se congela después de repetir la medición con los textos declarados de las tres empresas, que se cargan con `PUT /ai/profile/text` del backend y se analizan antes de la medición. Con datos reales hay que repetirla.

**Rol de solo lectura.** Además de abrir transacciones de solo lectura, la ejecución real usa un rol temporal que solo puede leer las tablas de «Lo que lee», y que se borra al terminar. Como superusuario, con una contraseña que solo sirve para esta ejecución:

```sql
CREATE ROLE perfil_eval LOGIN PASSWORD '<contraseña temporal>';
GRANT CONNECT ON DATABASE cumplify_db TO perfil_eval;
GRANT USAGE ON SCHEMA public TO perfil_eval;
GRANT SELECT ON companies, legal_requirements, legal_requirement_vinculations, company_profile_entries,
    ai_taxonomy_values, ai_article_classifications, cl_territories, legal_body_company_suggestions,
    regulatory_alerts, regulatory_alert_suggestions, regulatory_alert_suggestion_discards, legal_bodies,
    articles, ai_embeddings TO perfil_eval;
-- Al terminar:
DROP OWNED BY perfil_eval;
DROP ROLE perfil_eval;
```

```bash
export SERVICE_API_KEY=...        # la misma del servicio
export DATABASE_URL='postgresql://perfil_eval:<contraseña temporal>@localhost:5432/cumplify_db'
uv run python scripts/profile_retrieval_eval.py --dry-run
uv run python scripts/profile_retrieval_eval.py --base-url http://127.0.0.1:8000 --out /tmp/perfil.json
```

| Opción | Defecto | Uso |
|---|---|---|
| `--apps APP ...` | `norte altiplano litoral` | Empresas de demostración (todas sus apps elegibles) o ids de apps. Una app pedida por id que no es elegible es un error |
| `--variants V ...` | Todas | Variantes que se miden; `prof-v1` va siempre |
| `--skip-holdout` | | Sin la evaluación que deja una norma afuera |
| `--unrelated-sample N` | 0, todas | Normas sin relación por app, elegidas por el SHA-256 de una semilla fija con su id, para bases grandes |
| `--out FILE` | Un archivo del directorio temporal | Informe completo; fuera del repositorio, en un directorio que exista. En `--dry-run`, solo si se indica |
| `--vectors-out FILE` | Ninguno | Los vectores, uno por línea (app, variante, norma apartada y SHA-256 del texto), para la simulación del backend. Fuera del repositorio |
| `--timeout S` | 120 | Segundos por pedido |
| `--dry-run` | | Arma los textos y muestra, de los de `prof-v1`, el SHA-256, el largo y los valores por dimensión, con el costo estimado, sin el endpoint ni `SERVICE_API_KEY`. Los textos van solo a `--out` |
| `--self-test` | | Comprueba la receta, los casos de paridad y las métricas con datos inventados, sin servicio ni base |

**Comportamiento.**

- **Variables.** `SERVICE_API_KEY` y `DATABASE_URL` van exportadas o como argumentos (`--api-key`, `--database-url`): el script no lee `.env`.
- **Base de solo lectura.** Usa una conexión propia de solo lectura, la de `retrieval_eval.py`. Si no puede conectarse, muestra solo el tipo del error.
- **Comprobaciones antes del primer pedido.** Se ejecutan todas las consultas, incluida una búsqueda de prueba con un vector ya guardado, y se arman todos los textos: un error de configuración no gasta tokens.
- **Lo que lee.** `companies`, `legal_requirements`, `legal_requirement_vinculations`, `company_profile_entries`, `ai_taxonomy_values`, `ai_article_classifications`, `cl_territories`, `legal_body_company_suggestions`, `regulatory_alerts`, `regulatory_alert_suggestions`, `regulatory_alert_suggestion_discards`, `legal_bodies`, `articles` y `ai_embeddings`.
- **Consola y `--out`.** La consola muestra solo métricas: el corpus, los tokens, los avisos y, por app y variante, el largo, los tokens, las AUC, las medianas por grupo y la evaluación que deja una norma afuera. En `--dry-run` muestra, por app, el SHA-256, el largo y los valores por dimensión del texto de `prof-v1`, y nunca el texto: sin `--out`, los textos no se guardan. Los textos de perfil son datos de empresas y van a `--out`, con las distribuciones, los primeros puestos, la muestra por tramo y el detalle de cada norma apartada. El informe se guarda después de mostrarse, para que una falla al escribirlo no se lleve las métricas ya pagadas.
- **Privacidad.** Las normas privadas de una empresa nunca aparecen con su título.
- **Códigos de salida.** Termina con 0 aunque una app no tenga vector. Termina con 2 ante un error de configuración antes del primer pedido, sin gastar tokens: argumentos, `--out`, conexión o consultas a la base, ninguna app elegible o ningún trozo `art-v2` vigente. Termina con 1 si falla el endpoint o la base durante la evaluación, después de mostrar los tokens gastados, o si no puede guardar el informe.

---

## Dentro de alcance

- Llamar al endpoint de embeddings de OpenAI.
- Usar timeout y reintentos explícitos del cliente OpenAI (`OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`).
- Ordenar los vectores por el índice que devuelve el proveedor, para no depender del orden de la respuesta.
- Reportar `usage.total_tokens`.
- El script manual de evaluación de recuperación (`scripts/retrieval_eval.py`), descrito en "Evaluación de recuperación".
- El script manual de evaluación del perfil embebido (`scripts/profile_retrieval_eval.py`) y sus casos de paridad (`scripts/profile_text_parity.json`), descritos en "Evaluación del perfil embebido".

## Fuera de alcance

- Armar el texto (prefijos de taxonomía, troceo de artículos, perfil de una app). Lo hace el llamador; el script de evaluación del perfil solo reproduce `prof-v1` para medirla.
- Guardar los vectores.
- Autenticación.
- Leer PostgreSQL. Este endpoint no usa `DATABASE_URL`; solo los scripts de evaluación leen la base, cada uno con su propia conexión de solo lectura.

## Deuda técnica conocida

- El cliente OpenAI reintenta 408, 409, 429, 5xx, timeouts y errores de conexión según `OPENAI_MAX_RETRIES` antes de responder 502.
- El tope de 3072 es el del esquema HTTP. El modelo configurado puede rechazar un tamaño menor con 422.
