\# TAREA



Audita integralmente la aplicación proporcionada y, a continuación, corrige todos los problemas confirmados e implementa todas las mejoras identificadas dentro de su propósito.



Cubre lógica de negocio, funcionamiento técnico, datos, flujos completos, integraciones y usabilidad desde el punto de vista del usuario.



El encargo incluye análisis, diseño, programación, pruebas, revisión y entrega del resultado funcional. El informe de auditoría es un resultado intermedio: debes continuar con la implementación.



No te limites a comprobar que compila, que los tests existentes pasan o que los botones responden. Verifica que el usuario puede completar sus objetivos, que los resultados son correctos y que los datos se conservan.



Utiliza el código, documentación, configuración, pruebas y aplicación ejecutable disponibles. Descubre su tecnología y arquitectura sin presuponerlas.



\## Autonomía y alcance



Queda autorizada la modificación del código, interfaz, pruebas, configuración y documentación necesaria para resolver los problemas e implementar las mejoras.



Continúa entre fases sin solicitar aprobación rutinaria. Resuelve decisiones de implementación razonables y documenta los supuestos relevantes.



Todas las mejoras que incorpores al plan de esta intervención quedan incluidas en la implementación. No las dejes como recomendaciones, ampliaciones futuras o tareas opcionales sin ejecutar.



Fundamenta las mejoras en problemas u oportunidades concretas de la aplicación. Evita ampliar indefinidamente el alcance añadiendo funcionalidades ajenas a su propósito.



Respeta el trabajo previo y los cambios del usuario. No sobrescribas modificaciones ajenas ni elimines funcionalidades útiles sin proporcionar un comportamiento equivalente o mejor y verificar sus dependencias.



Trabaja con datos de prueba y en un entorno aislado. La publicación, el despliegue en producción, las operaciones reales con efectos externos, los gastos y las acciones destructivas sobre datos reales requieren autorización específica, salvo que ya exista.



Si falta una decisión imprescindible, pregunta únicamente por ella y sigue avanzando en las tareas independientes.



\# MÉTODO



\## 1. Comprende la aplicación y establece la situación inicial



Antes de modificar:



\- Lee las instrucciones del proyecto.

\- Identifica versión o commit, tecnología, arquitectura y dependencias.

\- Reconstruye el propósito de la aplicación, sus usuarios y objetivos.

\- Identifica las reglas documentadas y las deducidas del código.

\- Registra contradicciones entre documentación, interfaz y comportamiento.

\- Identifica los medios reales para ejecutar, probar y manejar la aplicación.

\- Ejecuta las comprobaciones existentes pertinentes.

\- Distingue fallos previos de los introducidos durante la intervención.



No confundas el comportamiento implementado con el comportamiento correcto. Si una regla de negocio no está definida, señala la incertidumbre y evita inventarla para justificar un cambio.



\## 2. Construye un inventario funcional y un registro de trabajo



Inventaría:



\- Funcionalidades y módulos.

\- Pantallas, rutas, acciones y puntos de entrada.

\- Entidades, estados y transiciones.

\- Roles y permisos, si existen.

\- Persistencia, procesos en segundo plano e integraciones.

\- Configuración, búsqueda, filtros, importación, exportación y recuperación, cuando existan.



Relaciona:



Objetivo del usuario → flujo → reglas → componentes → pruebas → resultado.



Mantén un registro único de todos los problemas, mejoras y tareas necesarias, con:



\- ID.

\- Descripción y origen.

\- Categoría: defecto, mejora de lógica, mejora de usabilidad o tarea técnica necesaria.

\- Evidencia y grado de certeza.

\- Impacto y prioridad.

\- Criterios de aceptación.

\- Dependencias.

\- Componentes afectados.

\- Estado.

\- Implementación realizada.

\- Pruebas y evidencia de verificación.



Estados permitidos:

IDENTIFICADO, EN ANÁLISIS, PENDIENTE, EN CURSO, IMPLEMENTADO SIN VERIFICAR, VERIFICADO y BLOQUEADO.



Los descartes requieren motivo y evidencia: falso positivo, duplicado o propuesta incompatible con un requisito confirmado. No utilices el descarte para reducir trabajo.



Conserva la trazabilidad de los elementos fusionados o sustituidos.



\## 3. Audita la lógica y los datos



Comprueba, según corresponda:



\- Cálculos, condiciones, prioridades y decisiones.

\- Validaciones en la interfaz y en las capas que procesan los datos.

\- Invariantes y transiciones de estado.

\- Entradas vacías, nulas, duplicadas, extremas o mal formadas.

\- Fechas, zonas horarias, unidades y redondeos.

\- Dependencias entre entidades.

\- Consecuencias de editar, mover, archivar o borrar.

\- Consistencia entre interfaz, lógica y almacenamiento.

\- Persistencia tras cerrar y volver a abrir.

\- Operaciones parciales, repetidas o interrumpidas.

\- Concurrencia, condiciones de carrera y respuestas fuera de orden.

\- Cancelación, reintentos, idempotencia y recuperación.

\- Errores de dependencias y conservación del trabajo.

\- Contratos entre módulos e integraciones.

\- Permisos y aislamiento de datos.

\- Funciones incompletas, código desconectado y acciones sin efecto.



Sigue los datos de extremo a extremo: entrada, validación, transformación, almacenamiento y presentación.



Si utiliza IA, comprueba también validación de sus salidas, respuestas incorrectas, latencia, errores y mecanismos de revisión del usuario.



\## 4. Prueba los flujos completos



Construye escenarios a partir de objetivos reales del usuario.



Para cada flujo verifica:



1\. Cómo descubre dónde comenzar.

2\. Qué necesita saber y si la app lo explica.

3\. Qué pasos y decisiones debe realizar.

4\. Qué respuesta recibe después de cada acción.

5\. Cómo reconoce que ha terminado.

6\. Qué ocurre al equivocarse, cancelar, retroceder o abandonar.

7\. Cómo retoma el trabajo.

8\. Si el resultado visible coincide con el estado guardado.



Incluye, cuando aplique:



\- Primera ejecución y configuración.

\- Tareas principales y uso habitual.

\- Edición y corrección.

\- Búsqueda y recuperación.

\- Acciones destructivas y deshacer.

\- Errores de validación y servicios externos.

\- Pérdida de conexión.

\- Cierre, reinicio y restauración.

\- Doble clic y operaciones duplicadas.

\- Interacción entre funcionalidades.



Detecta y corrige callejones sin salida, pasos innecesarios, dependencias ocultas, pérdida de contexto y estados incoherentes.



Comprueba resultados completos y persistentes; responder a un clic no demuestra que el flujo funcione.



\## 5. Evalúa y mejora la usabilidad



Maneja la interfaz si las herramientas disponibles lo permiten. Contrasta la experiencia visible con el código y los datos.



Evalúa:



\- Navegación y organización.

\- Terminología comprensible.

\- Visibilidad del estado.

\- Respuesta después de las acciones.

\- Consistencia de controles.

\- Número de pasos y esfuerzo.

\- Formularios y valores predeterminados.

\- Prevención y recuperación de errores.

\- Mensajes que expliquen cómo continuar.

\- Estados vacíos, carga, éxito, error y datos parciales.

\- Jerarquía visual, legibilidad y densidad.

\- Teclado, foco, etiquetas y contraste.

\- Tamaños de ventana y dispositivos pertinentes.

\- Operaciones largas y cancelación.

\- Protección del trabajo del usuario.



Relaciona cada mejora con una dificultad concreta y su consecuencia.



Implementa mejoras de comprensión, simplificación y prevención de errores, además de los arreglos técnicos.



Distingue observaciones verificadas de evaluaciones heurísticas. No presentes esta revisión como pruebas con usuarios reales.



\## 6. Convierte la auditoría en un plan ejecutable



Separa:



\- Defectos confirmados.

\- Hipótesis pendientes de comprobar.

\- Mejoras de lógica.

\- Mejoras de usabilidad.

\- Tareas técnicas necesarias para completar lo anterior.



Confirma o descarta las hipótesis antes de realizar cambios basados en ellas.



Para cada mejora define:



\- Problema y evidencia.

\- Usuario y tarea afectados.

\- Comportamiento actual y propuesto.

\- Beneficio esperado.

\- Alcance y componentes.

\- Dependencias y riesgos.

\- Criterios de aceptación.

\- Pruebas necesarias.



Ordena el trabajo por impacto y dependencias. La prioridad determina el orden de ejecución, no qué elementos puedes omitir.



Una vez consolidado el plan, ejecútalo completo. Añade los defectos y trabajos necesarios que descubras durante la implementación.



No conviertas ideas especulativas nuevas en requisitos continuamente. Una propuesta adicional que aceptes dentro del plan pasa a formar parte del compromiso de entrega.



\## 7. Implementa las correcciones y mejoras



Trabaja por unidades coherentes y verificables:



1\. Confirma el comportamiento esperado.

2\. Reproduce el problema cuando sea posible.

3\. Identifica su causa.

4\. Implementa la solución completa.

5\. Actualiza las capas y dependencias afectadas.

6\. Ejecuta las pruebas pertinentes.

7\. Comprueba el flujo real del usuario.

8\. Actualiza el registro y la documentación.



Corrige causas raíz y revisa otros lugares donde se repita el mismo patrón.



Evita soluciones parciales:

\- Una mejora de interfaz debe estar conectada a la lógica y persistencia necesarias.

\- Una corrección de backend debe reflejarse correctamente en la interfaz.

\- Una opción configurable debe tener efecto real.

\- Los errores deben permitir una recuperación útil.

\- Las funciones deben contemplar sus estados y casos límite.



No sustituyas implementaciones por placeholders, botones sin efecto, TODO o datos simulados presentados como reales.



Si necesitas modificar el esquema de datos, prepara la migración, verifica la compatibilidad y prueba la conservación de la información. No apliques cambios destructivos sobre datos reales sin autorización.



Evita reescrituras completas o cambios de tecnología salvo necesidad demostrada.



\## 8. Prueba el resultado y evita regresiones



Utiliza las técnicas que aporten evidencia:



\- Pruebas unitarias de reglas.

\- Integración.

\- Extremo a extremo.

\- Exploración mediante la interfaz.

\- Casos límite y negativos.

\- Transiciones de estado.

\- Fallos de dependencias.

\- Persistencia, repetición y recuperación.



Añade pruebas de regresión significativas para los defectos corregidos y las reglas de riesgo. Evita tests que solo repitan la implementación.



Registra para cada prueba:

ID, objetivo, precondiciones, datos, pasos o comando, resultado esperado, resultado observado y evidencia.



Estados:

PASA, FALLA, BLOQUEADA, NO EJECUTADA o NO APLICA con motivo.



Distingue componentes reales de simulados y explica qué queda sin comprobar.



Ejecuta los controles de compilación, análisis y pruebas exigidos por el proyecto. Tras cambios transversales, comprueba las áreas afectadas.



Un elemento solo pasa a VERIFICADO cuando cumple sus criterios de aceptación y existe evidencia. Compilar o superar un test aislado no basta si el cambio afecta a un recorrido completo.



\## 9. Aplica revisión adversarial



Separa responsabilidades:



\- Constructor: audita, implementa y prueba.

\- Árbitro: busca incumplimientos, regresiones, omisiones y evidencia insuficiente.



Si dispones de un agente independiente y autorizado, úsalo para el arbitraje. Entrégale requisitos, fuentes, cambios y evidencias.



Si no lo hay, realiza una revisión separada por rol y declara que es autoevaluación.



El árbitro debe revisar:

\- Hallazgos incorrectos o basados en supuestos.

\- Reglas alteradas sin justificación.

\- Flujos y estados omitidos.

\- Cambios incompletos.

\- Regresiones.

\- Mejoras que añaden confusión o complejidad.

\- Pruebas insuficientes.

\- Elementos del registro sin resolver.



Cada objeción debe indicar gravedad, evidencia, criterio incumplido, corrección y prueba posterior.



Aplica el ciclo:

planificar → implementar → probar → revisar → corregir → volver a comprobar.



Realiza hasta tres rondas formales de arbitraje. Este límite no permite omitir tareas del plan. Si se agota con problemas pendientes, informa del estado incompleto sin declararlo aprobado.



\## 10. Comprueba que no quede nada sin tratar



Antes de cerrar:



\- Concilia el inventario inicial con el resultado.

\- Revisa todos los IDs del registro.

\- Comprueba que cada defecto confirmado y mejora incluida está implementado y verificado.

\- Comprueba las dependencias entre cambios.

\- Busca implementaciones parciales, TODO introducidos y controles sin comportamiento.

\- Repite los recorridos principales sobre la versión final.

\- Verifica persistencia y compatibilidad.

\- Actualiza las instrucciones de ejecución y uso.



No cierres con tareas ejecutables pendientes por comodidad, extensión del trabajo o por haber redactado ya un informe.



Si un bloqueo real impide completar algo, identifica el elemento, la causa, lo realizado y el requisito exacto para continuar. Prosigue con todo lo que no dependa de ese bloqueo.



Si el entorno obliga a interrumpir, conserva un punto de continuación con versión, estado por ID, pruebas y siguiente acción. No presentes una interrupción como finalización.



\# LISTÓN



El resultado debe cumplir estos criterios obligatorios:



1\. Cobertura trazable.

&#x20;  Evidencia: inventario conectado con flujos, reglas, cambios y pruebas; límites explícitos.



2\. Corrección funcional.

&#x20;  Evidencia: defectos confirmados resueltos y criterios de aceptación comprobados.



3\. Mejoras completas.

&#x20;  Evidencia: todas las mejoras incluidas en el plan implementadas de extremo a extremo.



4\. Usabilidad comprobada.

&#x20;  Evidencia: recorridos que permiten completar las tareas, entender el estado y recuperarse de errores.



5\. Integridad y compatibilidad.

&#x20;  Evidencia: persistencia, migraciones e interacciones entre módulos verificadas cuando correspondan.



6\. Ausencia de regresiones conocidas.

&#x20;  Evidencia: controles del proyecto y pruebas pertinentes sobre la versión final.



7\. Entrega honesta y revisada.

&#x20;  Evidencia: revisión crítica, registro conciliado y separación entre comprobado y NO VERIFICADO.



No apruebes por promedio ni por porcentaje de tareas completadas.



Solo declara COMPLETADO Y VERIFICADO cuando todos los elementos del alcance estén resueltos y comprobados, sin bloqueos ni verificaciones esenciales pendientes.



Si queda algún elemento sin comprobar, declara IMPLEMENTADO CON VERIFICACIONES PENDIENTES o INCOMPLETO/BLOQUEADO, según corresponda. Indica exactamente qué falta.



\# ENTREGABLE



Entrega el código modificado y funcional, junto con las pruebas y documentación pertinentes.



Incluye:



1\. Resumen breve de los problemas resueltos y las mejoras implementadas.

2\. Registro completo por ID, con estado final y evidencias.

3\. Mapa actualizado de funcionalidades y flujos.

4\. Relación de cambios relevantes y su motivo.

5\. Pruebas ejecutadas, resultados y limitaciones.

6\. Instrucciones para ejecutar y comprobar la versión resultante.

7\. Migraciones o pasos de actualización, si existen.

8\. Resultado del arbitraje, modalidad real y rondas realizadas.

9\. Bloqueos o pendientes reales, si los hubiera.



No sustituyas el código por recomendaciones ni presentes propuestas como implementaciones.



No afirmes haber ejecutado pruebas que no realizaste.



Empieza inspeccionando la aplicación y construyendo el inventario. Continúa con la auditoría, implementa el plan completo y verifica el resultado sin detenerte entre fases para solicitar confirmaciones rutinarias.

