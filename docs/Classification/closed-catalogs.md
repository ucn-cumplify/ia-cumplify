# Clasificación normativa con catálogos cerrados

La versión `classify-v3-closed-catalogs` restringe las respuestas nuevas a las listas oficiales de Ámbito normativo, Sector productivo y Cobertura territorial, conservando su escritura literal. Las tres dimensiones restantes mantienen texto libre. El perfil empresarial conserva su contrato de etiquetas. `classify-v4-closed-catalogs` mantiene esos catálogos y abre el prompt diciendo que el modelo trabaja para Cumplify. `classify-v5-closed-catalogs` dice que la norma, las etiquetas y los artículos son datos, que un delimitador ocupa una línea entera, y neutraliza las etiquetas y las líneas `Article ID:` escritas dentro del texto.

## `classify-v6-closed-catalogs`: Región Metropolitana de Santiago

`classify-v6-closed-catalogs` agrega una sola opción al catálogo de Cobertura territorial: `Región Metropolitana de Santiago`, entre `Región de Valparaiso` y `Región del Libertador General Bernardo O'Higgins`. El catálogo pasa de 16 a 17 opciones: `Nacional` y las 16 regiones.

- No se elimina ni se renombra ninguna opción anterior. Las otras 16 conservan su escritura literal, incluidas `Region de Atacama`, `Región de Valparaiso` y `Región del Biobio`. Ámbito normativo (5 opciones) y Sector productivo (15) no cambian.
- No se agregan alias al catálogo cerrado. `RM`, `Región Metropolitana`, `Metropolitana`, `Santiago` y la misma región escrita sin tilde o en minúsculas siguen siendo etiquetas inválidas, y una respuesta que las trae se rechaza como cualquier otra etiqueta fuera del catálogo.
- `SYSTEM_PROMPT` es el mismo de v5: solo cambia el bloque de etiquetas existentes, que trae la opción nueva. La versión sube porque el backend distingue las respuestas por ella.
- Una respuesta válida en v5 sigue siendo válida con el catálogo de v6, porque este contiene al anterior. Las normas clasificadas con v5 no cambian: si una de ellas corresponde a la Región Metropolitana, solo una nueva clasificación puede darle la opción.
- Las equivalencias del catálogo territorial del CUT (alias como `RM` o `Metropolitana` de la región 13) viven en el backend, en su taxonomía y en la correspondencia con las opciones de los Filtros. No forman parte de este catálogo y no cambian las salidas oficiales del modelo, que solo puede devolver el nombre completo.
- El perfil empresarial no cambia: su Cobertura territorial sigue siendo texto libre y `profile-v3` sigue igual.

### Compatibilidad con el backend

El backend valida las respuestas con su propia copia del catálogo y solo cuando reconoce la versión. Por eso:
- Su integración debe reconocer `classify-v5-closed-catalogs` y `classify-v6-closed-catalogs`, y validar las dos con el catálogo de 17 opciones. Una respuesta v5 no puede traer la opción nueva, así que validarla con el catálogo ampliado no cambia su resultado.
- El backend debe actualizarse y verificarse antes de ejecutar esta versión del microservicio con él. Con un backend que solo reconoce v5, las respuestas v6 no pasarían por la validación del catálogo. Con un backend que reconoce v6 pero conserva el catálogo de 16, toda respuesta que traiga la región se marcaría inválida.
- La copia del contrato `tests/contracts/legal-bodies-classify.json` en el backend se actualiza desde este repositorio, con su huella SHA-256.

`No especificado` es un centinela técnico exclusivo dentro de su dimensión: no integra el catálogo comercial ni se persiste como categoría. Una etiqueta inválida se rechaza; no se corrige por similitud ni se sustituye silenciosamente por el centinela.

## Etiqueta oficial y representación interna

La respuesta nueva y las futuras opciones de Filtros deben usar, por ejemplo, `Region de Atacama`. La taxonomía puede conservar `Región de Atacama` y su ID: ambas cadenas tienen la misma forma normalizada con las reglas existentes. Esto no autoriza aceptar la segunda escritura en una respuesta nueva del clasificador.

No se renombran categorías, no se cambian migraciones anteriores y no se modifica la normalización ni la deduplicación. En backend, la validación estricta del contrato nuevo ocurre antes de reemplazar clasificaciones. Las respuestas pendientes de versiones anteriores conservan su contrato original.

Actualmente hay rutas que exponen nombres internos:
- `GET /ai/taxonomy/values`: devuelve `value`, `displayValue` y etiquetas de familias.
- `GET /ai/catalog/mappings`: las etiquetas de familias usan `DisplayValue ?? Value`; las opciones usan `ParameterOption.Name`.
- `GET /ai/profile`: devuelve valores internos y etiquetas visibles de familias; el frontend puede mostrar `displayValue || value`.

Estas rutas no constituyen el nuevo catálogo comercial. La futura integración con Filtros debe conservar la distinción entre IDs o familias internas y etiquetas oficiales, sin copiar los nombres internos directamente como opciones cerradas. No se modifican esas interfaces en esta etapa.

La integración con Filtros, los cambios de `AiCatalogSync` y los estados Sugerido/Validado siguen pendientes.

## Pruebas y límites

Las pruebas unitarias distinguen validación literal de equivalencia interna. La prueba PostgreSQL `An_official_territorial_label_reuses_the_internal_category_without_renaming_it` comprueba la reutilización del ID existente, la conservación de su nombre y la separación de las candidatas oficiales. Las pruebas de persistencia se ejecutaron y aprobaron en una base aislada mediante Testcontainers: tres casos específicos (3/3), incluidos los de preservación de clasificaciones ante respuestas nuevas inválidas, y regresión PostgreSQL (37/37).

La regresión IA realizada sin la marca `disconnect` tuvo 915 pruebas aprobadas, tres fallidas y seis excluidas. Las tres fallas se reprodujeron también en una copia del commit base `96178882e73f9b7425ed8d8155bb3cfc83f807bd`:
- `test_chat_settings.py::TestFakeResponderOnlyInDevelopment::test_the_process_ends_with_the_error`: el subproceso falla al inicializar componentes de Windows (`WinError 10106`) antes de comprobar el error esperado.
- `test_openai_chat_responder.py::TestProviderFailures::test_connection_cut_halfway`: no recibe el fragmento esperado del proveedor simulado.
- `test_openai_chat_responder.py::TestProviderFailures::test_a_cut_after_finish_reason_closes_normally_without_usage`: recibe un evento de error en lugar del cierre esperado.

No se cambian esas pruebas para ocultar los fallos. Ninguna de estas comprobaciones necesita llamadas a proveedores reales.
