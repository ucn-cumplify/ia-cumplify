# Docs — Context Engineering

## Qué es Context Engineering

Context engineering es la práctica de documentar, de forma estructurada y trazable, todo el conocimiento que no queda obvio en el código: decisiones de diseño, contratos de API, reglas de negocio, limitaciones conocidas y casos de prueba esperados.

El objetivo no es duplicar el código sino preservar el porqué detrás de cada decisión, de modo que cualquier integrante del equipo (o una herramienta de IA) pueda retomar el trabajo sin perder contexto ni romper contratos ya acordados.

> Regla de oro: si una decisión afecta el comportamiento del sistema o la experiencia de usuario y no queda evidente en el código, debe quedar en docs.

Esta carpeta no reemplaza tests ni comentarios de código. Su función es mantener un contrato compartido y trazable entre este servicio, `backend-cumplify` y QA.

---

## Archivos estándar por módulo

Cada carpeta de módulo contiene siempre los mismos tres archivos:

| Archivo | Propósito |
|---|---|
| `api.md` | Contrato de endpoints: rutas, método HTTP, parámetros, esquema de request/response, códigos de error y reglas que el servicio aplica. |
| `requirements.md` | Alcance funcional del módulo: qué debe hacer, qué está fuera de alcance, limitaciones y deuda técnica conocida. |
| `test.csv` | Casos de prueba: pasos, datos de entrada, resultado esperado y verificación del servicio (endpoint, código HTTP, efecto en datos). |

Estos tres archivos son el contrato mínimo de cada módulo. Si un módulo crece con endpoints adicionales, los detalles van en `api.md`; si surgen nuevos requerimientos, van en `requirements.md`; si se agregan casos de prueba, van en `test.csv`.

---

## Estructura actual

```text
docs/
├── README.md
├── ApplicabilityReasons/
│   ├── api.md
│   ├── requirements.md
│   └── test.csv
├── Chat/
│   ├── api.md
│   ├── requirements.md
│   └── test.csv
├── Classification/
│   ├── api.md
│   ├── requirements.md
│   └── test.csv
├── CompanyProfile/
│   ├── api.md
│   ├── requirements.md
│   └── test.csv
└── Embeddings/
    ├── api.md
    ├── requirements.md
    └── test.csv
```

Este servicio no es dueño del esquema de PostgreSQL. Lee `legal_bodies` y `articles`, que define y migra `backend-cumplify`. Por eso no hay un diagrama de base propio. La clasificación y los motivos de aplicabilidad leen esas tablas (los motivos, solo los artículos pedidos). El chat, el perfil de empresa y los embeddings no leen la base: responden con lo que reciben en el pedido. El script manual del set de evaluación del chat (`scripts/chat_eval.py`) no es parte del servicio: arma sus pasajes con una conexión propia de solo lectura a `articles`, `legal_bodies`, `obligations`, `legal_requirements` y `legal_requirement_vinculations`. Tampoco lo es el de evaluación de recuperación de los embeddings (`scripts/retrieval_eval.py`), que lee `ai_embeddings`, `articles` y `legal_bodies` con otra conexión propia de solo lectura.

---

## Carpeta stage/

`stage/` es el área de borrador de `docs/`. Aquí viven archivos `.md` temporales que aún no están listos para integrarse a un módulo específico.

Se usa para:

- Planes de implementación, antes de escribir código.
- Decisiones técnicas: alternativas evaluadas y la opción elegida.
- Notas de investigación previas a un cambio.

Un archivo en `stage/` tiene ciclo de vida corto: una vez que la decisión o el plan se consolida, su contenido se mueve al `api.md`, `requirements.md` o `test.csv` del módulo correspondiente y el borrador se elimina.

`stage/` es de uso personal. Los archivos que viven ahí no requieren revisión ni coordinación con el resto del equipo. Cuando un plan necesita compartirse, se mueve a la carpeta del módulo, por ejemplo `Classification/` o `Embeddings/`.

> Si un `.md` lleva más de un sprint en `stage/` sin moverse a su destino, es candidato a eliminarse o consolidarse.

---

## Convenciones de escritura

- Escribir reglas funcionales en lenguaje explícito y sin ambigüedad.
- Cada `api.md` debe incluir: objetivo del endpoint, contrato (request/response), reglas de negocio, errores posibles y limitaciones.
- Cada `requirements.md` debe incluir: objetivo del módulo, qué está dentro y fuera de alcance, y deuda técnica conocida.
- Cada `test.csv` debe incluir casos con pasos, resultado esperado y verificación del servicio.
  - Columnas: `ID`, `Módulo`, `Caso de Prueba`, `Precondiciones`, `Pasos de Ejecución`, `Datos de Entrada`, `Resultado Esperado`, `Verificación Frontend`, `Verificación Backend`, `Resultado Obtenido`, `Estado`, `Observaciones`.
  - Este repositorio no tiene interfaz. `Verificación Frontend` queda en `N/A`. `Verificación Backend` describe el endpoint, el código HTTP y el efecto sobre los datos.
  - `Estado` es `Pendiente` hasta que alguien ejecuta el caso, `Exitoso` solo cuando se ejecutó y el resultado coincide con lo esperado, y `Fallido` cuando se ejecutó y algo de lo esperado no se cumplió. Un caso que no se volvió a ejecutar después de cambiar el comportamiento vuelve a `Pendiente`.
  - `Resultado Obtenido` se llena solo con lo que se ejecutó de verdad. Si el caso se cubrió a medias, lo que faltó va en `Observaciones`.
  - Si una prueba automática de `tests/` cubre el caso, `Resultado Obtenido` lleva la fecha de la ejecución, la prueba (`archivo::Clase::prueba`) y lo que comprobó. Un caso que necesita el proveedor real, como el set de evaluación del chat, queda `Pendiente` hasta que alguien lo ejecuta.
- Si una decisión de diseño cambia, actualizar el doc correspondiente en el mismo cambio que el código.
