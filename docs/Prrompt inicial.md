ROL



Actúa como arquitecto principal de software, ingeniero senior Python, especialista en sistemas multiagente, seguridad OAuth/A2A y testing.



Tu misión es diseñar e implementar por fases un nuevo proyecto llamado provisionalmente "Agora", integrado con los repositorios reales:



\- AI\_Broker: https://github.com/jfelicianoat/AI\_Broker

\- Athena: https://github.com/jfelicianoat/Athena



y basado en la especificación "Atomic Agents" proporcionada como documentación del proyecto.



No implementes a partir de recuerdos, ejemplos genéricos ni supuestos sobre Athena o AI\_Broker: antes de modificar o integrar algo debes inspeccionar el código, ADR, contratos y tests reales de las versiones disponibles.



No afirmes que algo funciona o está verificado si no has podido ejecutarlo o comprobarlo realmente.





OBJETIVO REAL



Construir Agora como infraestructura reutilizable de trabajo autónomo basado en agentes atómicos.



Agora debe permitir que Athena y futuros agentes puedan convertir trabajos autocontenidos en tarjetas que sobrevivan a las sesiones y sean ejecutadas posteriormente por workers efímeros apropiados.



Al mismo tiempo, el proyecto debe crear una fachada desacoplada para AI\_Broker que permita en el futuro consumirlo mediante:



1\. una API estable;

2\. OAuth 2.0;

3\. A2A;



sin introducir estas responsabilidades dentro del core de AI\_Broker.



La arquitectura debe mantener tres responsabilidades separadas:



\- Athena/agentes: deciden QUÉ trabajo delegar.

\- Agora: decide QUÉ perfil/worker puede ejecutarlo y CUÁNDO.

\- AI\_Broker: decide CÓMO realizar la inferencia y qué modelo/proveedor utilizar.



No crear un segundo router de modelos dentro de Agora.





CONTEXTO Y TOPOLOGÍA REAL



Existen dos PCs Windows.



PC PRINCIPAL:

\- permanece encendido habitualmente;

\- es el equipo de trabajo diario;

\- aloja Athena;

\- aloja las aplicaciones/agentes cliente;

\- debe alojar Agora Desktop;

\- debe alojar la fuente de verdad del tablero Atomic;

\- puede alojar runners locales de Athena/CLI/filesystem.



PC IA:

\- sólo se enciende cuando se necesita AI\_Broker/capacidad IA;

\- aloja AI\_Broker;

\- aloja modelos locales/GPU y demás infraestructura de inferencia;

\- deberá ejecutar Agora AI Runner;

\- deberá ejecutar AI Broker Gateway;

\- puede estar apagado durante horas o días.



Consecuencia obligatoria:



Si el PC IA está apagado, las tarjetas que necesitan AI\_Broker deben permanecer tranquilamente en pending.



No deben fallar, consumir intentos, provocar retries continuos ni requerir que el PC principal espere activamente.



Cuando el PC IA vuelva a estar disponible, su runner debe descubrir/reclamar el trabajo compatible.



El paradigma es PULL, no PUSH.





ARQUITECTURA OBJETIVO



El mismo repositorio Agora contendrá componentes separables aproximadamente equivalentes a:



\- agora.core

\- agora.desktop

\- agora.board

\- agora.profiles

\- agora.skills

\- agora.matching

\- agora.dispatcher

\- agora.scheduler

\- agora.artifacts

\- agora.security

\- agora.api

\- agora.runners.local

\- agora.runners.athena

\- agora.runners.broker

\- agora.runners.cli

\- agora.gateway

\- tests



Los nombres exactos pueden ajustarse si el diseño real lo justifica.



Debe ser posible producir al menos dos ejecutables/procesos:



1\. Agora Desktop

&#x20;  - PC principal.

&#x20;  - Aplicación gráfica Windows.

&#x20;  - Preferencia tecnológica: Python + PySide6/Qt.

&#x20;  - No es una aplicación web.

&#x20;  - El core no debe depender de Qt.



2\. Agora AI Runner/Gateway

&#x20;  - PC IA.

&#x20;  - Proceso/servicio sin necesidad de interfaz gráfica.

&#x20;  - Reclama trabajos compatibles.

&#x20;  - Se comunica localmente con AI\_Broker.

&#x20;  - Ofrece posteriormente la fachada estándar de AI\_Broker.





INVARIANTES DE ATOMIC AGENTS



Preserva salvo incompatibilidad demostrada estas invariantes:



1\. Un worker atómico nace para una tarjeta, la atiende y termina.



2\. No tiene contexto ambiental.

&#x20;  Sólo recibe:

&#x20;  - PROFILE;

&#x20;  - skills declaradas;

&#x20;  - tarjeta;

&#x20;  - entradas explícitas por referencia.



3\. PROFILE es contrato, no conocimiento operativo.



4\. SKILL contiene el know-how reutilizable.



5\. CARD es unidad de trabajo y permiso de producción.



6\. El estado reside en el tablero/filesystem, no en una base de datos.



7\. El dispatcher no utiliza LLM.

&#x20;  Matching determinista únicamente.



8\. Cuando el sistema no sabe qué perfil corresponde, no debe adivinar.

&#x20;  La tarjeta permanece pending y el problema debe ser visible.



9\. "La tarjeta pide, no manda".

&#x20;  Una tarjeta no puede ampliar permisos, garantías o capacidades declaradas por PROFILE.



10\. Los secretos nunca deben introducirse en prompts ni quedar almacenados en tarjetas, logs versionados o ficheros accesibles al modelo.



11\. Los errores deben fallar de forma visible.

&#x20;  Nada debe desaparecer silenciosamente.



12\. Todo incidente real que provoque una corrección debe acabar acompañado de una prueba automatizada de regresión.



13\. El dispatcher/código ejecutable de Agora no debe vivir dentro de una bóveda donde los agentes puedan modificarlo libremente.



14\. Debe existir comprobación automática de solapamiento de handles/refuses al crear/modificar perfiles.



15\. No implementar Directors antes de haber demostrado workers reales.



16\. Cuando se implementen Directors:

&#x20;  - crear sólo un hijo cada vez;

&#x20;  - yield sin incrementar attempts;

&#x20;  - esperar dependent\_task;

&#x20;  - verificar exactamente el cableado antes de morir;

&#x20;  - no inventar outputs inexistentes.





TABLERO



Mantén como fuente de verdad un tablero en disco local del PC principal.



Como mínimo:



KANBAN/

\- pending/

\- in-progress/

\- done/

\- blocked/

\- archive/

\- scheduled/



El único propietario directo del filesystem del tablero será Agora en el PC principal.



Los runners remotos NO montarán ni sincronizarán directamente estas carpetas.



Prohibido utilizar para el claim:



\- OneDrive;

\- Dropbox;

\- Google Drive;

\- Syncthing;

\- SMB compartido;

\- sincronización eventual similar.



Los runners reclamarán trabajo mediante la API de Agora.



El servidor realizará localmente el movimiento/compare-and-swap que garantice que una tarjeta sólo pueda pertenecer a un worker.



Una carrera por la misma tarjeta debe terminar con:

\- un único ganador;

\- el resto recibiendo conflicto;

\- ninguna ejecución duplicada.





HARNESSES / CLASES DE WORKER



Soporta progresivamente al menos tres clases.



A. Broker worker



Para trabajos principalmente de inferencia:

\- resumen;

\- clasificación;

\- extracción;

\- traducción;

\- transcripción;

\- research adecuado;

\- generación similar.



El worker utilizará AI\_Broker.



B. Athena worker



Para trabajos que necesitan:

\- repositorios del PC principal;

\- herramientas;

\- filesystem;

\- ejecución;

\- permisos;

\- workspace;

\- checkpoints;

\- verificación de entregables.



Agora NO debe duplicar las capacidades internas de Athena.



Athena debe utilizarse como harness mediante su interfaz/adaptador apropiado.



C. CLI worker



Para futuras herramientas/binarios/CLI.



El diseño debe permitir añadir harnesses sin modificar el algoritmo central de matching.





ATHENA: REGLAS DE INTEGRACIÓN



No introduzcas Agora dentro del AgentLoop ni dentro del core de Athena.



No conviertas Agora en ModelProvider.



No sustituyas los subagentes actuales de Athena.



No confundas:

\- subagente dentro de un run;

\- worker atómico fuera del run.



Mantén inicialmente intacta la ruta:



Athena -> AI\_Broker



para inferencia normal.



Añade una integración opcional de trabajo externo detrás de una abstracción/adaptador coherente con la arquitectura existente de Athena.



Debe permitir como mínimo:



\- crear trabajo en Agora;

\- consultar estado;

\- cancelar si está permitido;

\- recuperar resultados/artefactos.



Además, Agora podrá utilizar Athena como harness a través de un runner local cuando una CARD emparejada requiera herramientas que sólo Athena proporciona.



No mezcles AthenaProfile con PROFILE.md.

Son conceptos distintos.





SKILLS COMPARTIDAS



Investiga el SkillManifest o mecanismo real equivalente de Athena.



Diseña, cuando sea viable, un formato común o adaptador que permita que Athena y Atomic Agents compartan un mismo cuerpo de conocimiento.



Objetivo:



dos puertas, un cuerpo de conocimiento.



Evita mantener dos copias divergentes de la misma skill.



Distingue terminología:



\- skill/habilidad: conocimiento reusable;

\- tool/herramienta: capacidad ejecutable concreta;

\- harness: entorno que ejecuta un worker.





AI\_BROKER: REGLAS DE INTEGRACIÓN



AI\_Broker mantiene su responsabilidad exclusiva sobre:



\- selección de modelos;

\- routing;

\- proveedores;

\- fallback;

\- VRAM;

\- estrategias;

\- mixture of agents;

\- capacidades de inferencia.



Agora no debe duplicar ese router.



Inspecciona siempre GET /api/v1/capabilities o su implementación real cuando corresponda.



Respeta el contrato actual de AI\_Broker en vez de inventarlo.



Actualmente el modelo es aproximadamente asíncrono:

\- crear task;

\- recibir task\_id;

\- sondear estado;

\- obtener resultado.



Compruébalo contra el código real antes de programar.





TOKEN DINÁMICO DE AI\_BROKER



REQUISITO CRÍTICO:



NO fijar permanentemente AI\_BROKER\_ADMIN\_TOKEN.



Debe mantenerse el comportamiento por el cual AI\_Broker genera una credencial diferente en cada arranque.



Antes de implementar Broker Runner o Gateway:



1\. inspecciona exactamente dónde y cómo AI\_Broker genera el token;

2\. descubre si se:

&#x20;  - imprime;

&#x20;  - persiste temporalmente;

&#x20;  - publica mediante IPC;

&#x20;  - almacena mediante sistema operativo;

&#x20;  - devuelve a un launcher;

&#x20;  - obtiene por otro mecanismo;

3\. documenta el comportamiento real con referencias a código;

4\. diseña el mecanismo local más seguro para que Agora AI Runner/Gateway obtenga el token tras cada arranque;

5\. implementa ese mecanismo sólo después de justificarlo.



Restricciones:



\- el X-Admin-Token nunca sale del PC IA;

\- nunca se transmite a Agora Desktop;

\- nunca aparece en CARD;

\- nunca aparece en PROFILE;

\- nunca aparece en SKILL;

\- nunca llega a un LLM;

\- no se guarda en Git;

\- no debe registrarse completo en logs;

\- una rotación/reinicio debe poder recuperarse automáticamente;

\- un 401/403 provocado por reinicio debe provocar renovación local de credencial antes de considerar fallida la tarjeta, según el contrato real del broker.



Si el código actual no proporciona una forma segura de recuperar el token dinámicamente, NO inventes una solución silenciosamente.



Presenta alternativas y detente para decisión humana si la solución requiere modificar AI\_Broker de forma significativa.





AI BROKER GATEWAY



Implementa dentro del repositorio Agora un componente desacoplado denominado provisionalmente AI Broker Gateway.



Se ejecuta en el PC IA.



NO depende del tablero para prestar sus servicios.



Debe ser posible usar:



cliente externo -> Gateway -> AI\_Broker



aunque no exista ninguna CARD.



Responsabilidades:



\- esconder la autenticación interna X-Admin-Token;

\- presentar interfaces externas estables;

\- traducir contratos;

\- autenticar clientes;

\- aplicar scopes/políticas;

\- posteriormente exponer A2A.



No debe:

\- seleccionar modelos por su cuenta;

\- duplicar el router del broker;

\- mantener otra cola de inferencia innecesaria;

\- cambiar semántica silenciosamente.





HTTPS



Aunque Agora sea aplicación de escritorio, la comunicación entre PCs debe usar HTTPS.



No construir interfaz web para Agora.



HTTPS es exclusivamente transporte seguro entre procesos y máquinas.



Evita depender de una VPN propietaria o de una topología concreta.



Diseña una estrategia razonable para certificados en una LAN doméstica/privada:



\- bootstrap controlado;

\- certificate pinning o CA privada según proceda;

\- almacenamiento seguro de claves;

\- renovación;

\- error claro ante certificado inesperado.



No desactivar permanentemente la validación TLS.



Durante tests unitarios puede utilizarse infraestructura efímera.





OAUTH 2.0



La frontera moderna hacia Agora/Gateway utilizará OAuth 2.0 para comunicación máquina-a-máquina.



Preferencia:



grant\_type = client\_credentials.



Cuando se implemente autenticación fuerte de cliente, preferir private\_key\_jwt frente a secretos compartidos si las librerías y el entorno real lo permiten.



No implementar criptografía casera.



Usar librerías OAuth/JWT maduras.



Scopes iniciales de Agora:



\- cards:write

\- cards:read

\- board:claim

\- board:admin



Añadir scopes específicos del Gateway si resultan necesarios.



El servidor debe derivar:



origin = identidad autenticada del cliente



y NO confiar en un campo origin suministrado libremente por el cliente.



Access tokens:

\- corta duración;

\- audience explícita;

\- subject/client identity;

\- scopes explícitos.



Diseña revocación/rotación.



Nunca publiques credenciales en repositorio.





A2A



A2A NO forma parte del núcleo Atomic.



Debe implementarse posteriormente como adaptador/fachada encima del Gateway/Core.



La arquitectura debe permitir:



A2A client

&#x20;   ->

A2A adapter

&#x20;   ->

Gateway/Core

&#x20;   ->

AI\_Broker



sin modificar AI\_Broker.



Antes de implementarlo:

\- comprueba la versión vigente del estándar A2A;

\- utiliza su especificación oficial;

\- no implementes de memoria contratos o endpoints;

\- documenta claramente qué conceptos de A2A se corresponden con tasks/results/artifacts de AI\_Broker.



A2A no sustituye MCP.



Mantén conceptual y técnicamente separados:

\- agente <-> agente;

\- agente <-> tools/datos.





MODELOS Y CAPACIDAD



PROFILE nunca debe escribir nombres concretos de modelos salvo caso explícito de determinismo estricto aprobado por contrato.



Debe existir una tabla versionada, por ejemplo models.yml o equivalente:



model\_capacity -> requisitos/parámetros de AI\_Broker.



Ejemplos conceptuales:

\- minimum;

\- standard;

\- advanced.



Antes de fijar la traducción, inspecciona el contrato real de AI\_Broker.



No conviertas capacity automáticamente en un modelo hardcoded si el broker puede enrutar por requisitos.





DETERMINISMO



Implementa dos políticas explícitas cuando sean necesarias:



determinism: strict



Para:

\- clasificadores;

\- extractores;

\- pasos intermedios reproducibles;

\- tests/regresiones;

\- decisiones que alimentan otro paso.



Cuando el contrato real lo permita, puede fijar:

\- modelo/target;

\- fallback false;

\- seed;

\- temperature 0;

\- parámetros equivalentes.



determinism: routed



Para:

\- redacción;

\- research;

\- análisis abierto;

\- resultados finales para revisión humana.



Permite routing del broker, pero registra el modelo/proveedor/configuración realmente usados.



No prometas reproducibilidad bit-a-bit en modo routed.





PROMPT COMPRESSION



Verifica la configuración real de AI\_Broker.



Para workers atómicos, PROFILE + SKILL + CARD constituyen contrato y no deben sufrir compresión que elimine garantías o instrucciones relevantes.



Si AI\_Broker mantiene prompt\_compression agresivo/global:

\- fuerza "off" para el trabajo atómico si el contrato lo permite;

\- añade prueba específica;

\- si no es posible, presenta el riesgo antes de continuar.





ATTACHMENTS Y ARTEFACTOS



Cuando una CARD tenga inputs que deban subirse a AI\_Broker:



\- súbelos antes de despachar;

\- espera explícitamente el estado ready equivalente;

\- converting/no preparado significa "esperar", no fallo;

\- no incrementar attempts por esperar una entrada legítimamente no preparada.



Los resultados deben materializarse en el destino definido por la CARD o por contrato.



Registra paths exactos.



No cierres una tarjeta con paths vacíos si el contrato requería outputs.





RETRIES, INTENTOS Y ZOMBIES



Distingue:



\- CARD.attempts de Agora;

\- retries internos de AI\_Broker.



No son el mismo contador.



Un intento de CARD puede contener varios retries internos del broker.



Alinea:



zombie\_timeout\_Agora

>

máximo tiempo legítimo de ejecución del Broker

\+

margen de seguridad.



Antes de rescatar una CARD cuyo task remoto aún pueda estar vivo:

\- consulta su estado;

\- intenta cancelar la tarea broker según su API real;

\- sólo después devuelve la CARD a pending.



Añade tests específicos para impedir doble facturación/ejecución por rescate prematuro.





MODELO REAL, COSTE Y TRAZABILIDAD



No supongas que el modelo solicitado es el que finalmente respondió.



Usa la fuente real que AI\_Broker exponga (invocations o equivalente) para registrar:



\- modelo real;

\- proveedor;

\- parámetros efectivos relevantes;

\- estrategia;

\- coste si está disponible;

\- invocation/task IDs útiles para auditoría.



CARD.Record debe ser legible por una persona y suficientemente preciso para reconstruir lo sucedido.





FORMATO DE CARD / PROFILE / SKILL



Parte de la especificación Atomic Agents suministrada.



No inventes campos innecesarios.



Preserva la integridad del front matter.



Una escritura nunca debe dejar YAML/front matter ilegible.



Añade validación de esquema y round-trip tests.



Los logs cronológicos van en Record, no reemplazando campos estructurados.





API DE AGORA



La API existe para comunicación entre procesos/agentes, no para proporcionar una interfaz web al usuario.



Define gradualmente endpoints/operaciones equivalentes a:



\- crear CARD;

\- consultar CARD;

\- consultar BOARD;

\- consultar perfiles;

\- consultar eventos;

\- claim;

\- progress;

\- close;

\- yield;

\- unblock/admin;

\- health.



No fijes rutas definitivas hasta diseñar contrato/versionado.



POST create debe ser idempotente mediante Idempotency-Key o mecanismo equivalente.



Claim remoto debe usar compare-and-swap/operación atómica servidor-side.



SSE puede utilizarse para eventos si es apropiado.



No añadas WebSockets si no aportan una necesidad real.





UI DESKTOP



Agora debe ser una aplicación de escritorio Windows.



Preferencia: PySide6/Qt.



La UI debe ser un cliente del core, no contener la lógica del dominio.



Debe mostrar progresivamente:



\- tablero;

\- estado de tarjetas;

\- blocked;

\- runners disponibles/offline;

\- perfiles;

\- histórico;

\- Record;

\- costes;

\- scheduler;

\- errores;

\- configuración relevante.



No conviertas la UI en requisito para que el dispatcher/scheduler funcionen.



Cerrar la ventana no debe necesariamente destruir el runtime si el usuario configura ejecución en segundo plano/servicio.





PROCESO DE IMPLEMENTACIÓN



Trabaja por fases.



No empieces una fase hasta que:

1\. la anterior compile;

2\. pasen sus tests;

3\. se hayan presentado evidencias;

4\. se haya satisfecho cualquier puerta humana indicada.



Para cada fase entrega:



\- objetivo;

\- decisiones adoptadas;

\- archivos creados/modificados;

\- tests ejecutados;

\- resultado exacto de tests;

\- riesgos/deuda conocida;

\- qué verificaste;

\- qué no pudiste verificar;

\- instrucciones de prueba manual;

\- propuesta de siguiente fase.



No digas únicamente "tests passed".

Presenta comandos y resumen verificable.





FASE F0 — NÚCLEO ATOMIC MÍNIMO



Sin red.

Sin OAuth.

Sin A2A.

Sin UI obligatoria.

Sin Directors.

Sin Wake-on-LAN.



Construir y probar:



\- parser/serializer CARD;

\- integridad front matter;

\- Board filesystem;

\- pending/in-progress/done/blocked/archive/scheduled;

\- PROFILE loader;

\- SKILL loader;

\- matching determinista;

\- handles/refuses;

\- recipient;

\- priorities;

\- claim atómico local;

\- Record;

\- attempts;

\- zombies básicos;

\- dry-run;

\- scheduler básico si no aumenta demasiado el alcance;

\- harness falso/determinista para pruebas.



Añadir herramienta/test de overlap de perfiles desde el primer perfil.



PRUEBAS F0 OBLIGATORIAS



1\. Round-trip CARD:

&#x20;  leer -> modificar permitido -> escribir -> volver a leer sin corrupción.



2\. Dos workers concurrentes intentan reclamar la misma CARD:

&#x20;  exactamente uno gana.



3\. CARD sin PROFILE compatible:

&#x20;  permanece pending.



4\. refuses invalida correctamente un candidato.



5\. descriptor más específico gana.



6\. recipient salta matching sólo cuando es válido.



7\. CARD con input inexistente:

&#x20;  espera sin aumentar attempts.



8\. CARD bloqueada:

&#x20;  nunca se despacha.



9\. zombie:

&#x20;  vuelve correctamente o queda bloqueado según attempts.



10\. dry-run:

&#x20;   no modifica ni un fichero.



11\. overlap checker:

&#x20;   detecta perfiles ambiguos.



12\. end-to-end con harness falso:

&#x20;   colocar CARD en pending;

&#x20;   ejecutarla;

&#x20;   terminar en done;

&#x20;   paths completos;

&#x20;   Record comprensible.



CRITERIO DE ACEPTACIÓN F0



Debe poder demostrarse el paradigma entero sin red ni AI\_Broker.



PUERTA HUMANA OBLIGATORIA.



Detente y presenta resultados.

No continúes F1 hasta aprobación explícita.





FASE F1 — AGORA DESKTOP



Implementar PySide6/Qt sobre el core ya demostrado.



Objetivo:

visualizar y administrar, no reimplementar lógica.



Como mínimo:



\- tablero;

\- detalle CARD;

\- Record;

\- perfiles;

\- blocked;

\- estado del dispatcher;

\- logs/errores básicos.



PRUEBAS F1



\- core funciona sin Qt;

\- UI no modifica directamente ficheros saltándose servicios del dominio;

\- actualización visual refleja movimientos reales;

\- una CARD corrupta produce error visible;

\- cerrar/reabrir UI no pierde estado;

\- tests de UI mínimos sobre flujos críticos.



No introducir todavía complejidad remota innecesaria.





FASE F2 — API SEGURA + RUNNERS REMOTOS



Crear API de Agora para runners/clientes.



Transportar entre PCs mediante HTTPS.



Implementar:

\- contrato versionado;

\- health;

\- lectura;

\- claim;

\- progress;

\- close;

\- events si procede;

\- idempotencia;

\- autenticación provisional segura si OAuth aún no está activo;

\- certificados correctamente validados.



Crear Runner genérico.



PRUEBAS F2



1\. PC/runner remoto obtiene sólo tarjetas compatibles.

2\. Dos runners remotos compiten y uno solo reclama.

3\. caída de red durante claim no duplica ejecución.

4\. retry con Idempotency-Key no duplica CARD.

5\. certificado incorrecto es rechazado.

6\. API no permite path traversal.

7\. runner offline no causa fallo de tarjetas.

8\. runner reconectado vuelve a reclamar trabajos.

9\. permisos de escritura sólo ocurren mediante endpoints autorizados.

10\. ningún cliente remoto toca directamente KANBAN.



No implementar aún A2A.





FASE F3 — AI RUNNER + INTEGRACIÓN REAL CON AI\_BROKER



Ejecutar en PC IA.



Antes de programar la autenticación al broker:



INVESTIGAR Y DOCUMENTAR EL CICLO REAL DEL X-ADMIN-TOKEN.



Mantener token dinámico por arranque.



Diseñar adquisición local segura.



Construir Broker harness/runner.



Traducir PROFILE + CARD -> tarea AI\_Broker.



PROFILE antes que CARD en la composición del contrato.



Implementar:

\- capacidades;

\- attachments;

\- wait-ready;

\- envío asíncrono;

\- polling;

\- cancelación;

\- recuperación;

\- invocations;

\- coste/modelo real;

\- determinism strict/routed;

\- prompt\_compression off para trabajo Atomic;

\- timeouts/zombies alineados.



PRUEBAS F3



1\. PC IA apagado:

&#x20;  CARD broker permanece pending sin error.



2\. PC IA encendido:

&#x20;  runner aparece y reclama.



3\. reinicio AI\_Broker:

&#x20;  token viejo deja de servir;

&#x20;  runner obtiene el nuevo de forma local;

&#x20;  reanuda operación sin exponerlo.



4\. inspección de logs:

&#x20;  token completo no aparece.



5\. captura/red:

&#x20;  token interno del broker nunca cruza al PC principal.



6\. attachment converting:

&#x20;  CARD espera;

&#x20;  attempts no aumenta.



7\. strict:

&#x20;  configuración efectiva coincide con política definida.



8\. routed:

&#x20;  routing permitido y modelo real queda registrado.



9\. timeout legítimo:

&#x20;  Agora no rescata prematuramente.



10\. zombie real:

&#x20;   se cancela tarea remota antes del redispatch cuando sea posible.



11\. retry broker:

&#x20;   no incrementa CARD.attempts.



12\. Idempotency-Key:

&#x20;   redispatch accidental no factura dos veces el mismo intento.



13\. end-to-end:

&#x20;   CARD -> broker -> artifact -> done + paths + Record + modelo/coste.





FASE F4 — INTEGRACIÓN CON ATHENA



Inspeccionar nuevamente ADR, interfaces y tests reales de Athena.



No romper ADR-002 ni introducir dependencias obligatorias de Agora en el core.



Implementar adaptador opcional para:



Athena -> Agora:

\- submit;

\- status;

\- cancel;

\- artifacts.



Implementar Athena harness/runner local para:



Agora -> Athena.



No abrir Athena innecesariamente a la LAN.



Usar loopback cuando el runner esté en el mismo PC.



Aplicar capacidades/permisos desde PROFILE, nunca ampliados por CARD.



En card mode:

\- no utilizar ask;

\- si falta información no inventar;

\- aplicar failure policy;

\- no dejar el run esperando interlocutor.



Convertir verificación de Athena en evidencia real para cerrar CARD.



PRUEBAS F4



1\. Athena sigue funcionando sin Agora.

2\. Athena sigue funcionando con su ModelProvider AI\_Broker actual.

3\. activar adaptador Agora no modifica AgentLoop.

4\. Athena crea CARD correctamente.

5\. origin se deriva de identidad, no del payload.

6\. Athena puede cerrarse después de delegar.

7\. CARD sigue existiendo.

8\. Athena recupera después el resultado.

9\. Agora reclama trabajo harness=athena sólo cuando runner está disponible.

10\. PC principal sin runner compatible -> CARD espera.

11\. CARD no puede ampliar writes/exec del PROFILE.

12\. ask en card mode es rechazado.

13\. deliverables realmente verificados -> done.

14\. verificación inconclusa -> failure policy, nunca falso done.



PUERTA HUMANA OBLIGATORIA.



Detente.

Presenta arquitectura real resultante y pruebas.

No continúes seguridad/protocolos externos hasta aprobación.





FASE F5 — OAUTH 2.0 + AI BROKER GATEWAY API



Construir la frontera moderna.



Usar librerías maduras.



OAuth 2.0 machine-to-machine:

\- client\_credentials;

\- scopes;

\- JWT/access tokens de corta duración;

\- audience;

\- subject;

\- rotación;

\- private\_key\_jwt si resulta adecuado.



Implementar scopes Agora y Gateway.



Derivar origin del cliente autenticado.



Construir API estable del AI Broker Gateway.



El Gateway:

\- vive en PC IA;

\- puede funcionar independientemente del tablero;

\- usa dinámicamente X-Admin-Token sólo de forma local;

\- traduce contratos externos a AI\_Broker.



NO eliminar aún el acceso directo actual Athena -> AI\_Broker.



PRUEBAS F5



1\. cliente sin token -> rechazado.

2\. token expirado -> rechazado.

3\. audience incorrecta -> rechazado.

4\. scope insuficiente -> rechazado.

5\. origin falsificado en payload -> ignorado/rechazado.

6\. origin final == identidad autenticada.

7\. revocar cliente no afecta otros clientes.

8\. reinicio broker/token dinámico:

&#x20;  Gateway recupera nueva credencial localmente.

9\. cliente OAuth nunca conoce X-Admin-Token.

10\. API Gateway devuelve errores semánticos correctos.

11\. Gateway puede usarse sin crear ninguna CARD.

12\. Agora Broker worker también puede consumir Gateway/adapter sin bucle recursivo ni doble cola.



PUERTA HUMANA OBLIGATORIA antes de exponer A2A.





FASE F6 — A2A



Consultar especificación oficial vigente.



Implementar A2A como adaptador independiente.



Publicar capacidades apropiadas.



Mapear:

\- tareas;

\- estados;

\- mensajes cuando proceda;

\- artifacts;

\- errores;

\- cancelación;

\- autenticación.



No reinterpretar trabajo Atomic como conversación innecesaria.



No mezclar A2A con MCP.



PRUEBAS F6



1\. cliente A2A descubre Gateway.

2\. autenticación OAuth funciona.

3\. tarea A2A sencilla -> AI\_Broker -> resultado.

4\. artifact binario/textual conserva identidad.

5\. cancelación se propaga.

6\. fallo broker se traduce sin ocultarlo.

7\. A2A no necesita acceso al tablero.

8\. cliente A2A no aprende X-Admin-Token.

9\. versión A2A incompatible produce error claro.

10\. API REST y A2A producen semántica consistente.





FASE F7 — DIRECTORS, SCHEDULING AVANZADO Y EXPLOTACIÓN



Sólo después de workers probados.



Implementar Directors respetando la especificación Atomic.



Añadir:

\- scheduled cards completas;

\- workflows;

\- costes por tarjeta/flujo;

\- métricas;

\- runners;

\- histórico;

\- dashboard desktop;

\- archivado;

\- reporting.



No construir CEO antes de tener al menos dos Directors reales y útiles.



PRUEBAS F7



1\. director crea sólo un hijo.

2\. yield no aumenta attempts.

3\. dependent\_task incorrecto se detecta antes de morir.

4\. child sin paths -> false close -> director bloqueado.

5\. fallo paso 1 impide crear pasos 2/3.

6\. restart reconstruye flujo desde BOARD.

7\. scheduled materializa una sola instancia por periodo.

8\. no catch-up accidental salvo especificación explícita.

9\. coste total de flujo puede reconstruirse.

10\. modelos/invocations pueden auditarse.





FASE F8 — WAKE-ON-LAN Y CAPACIDAD IA BAJO DEMANDA



ESTA ES LA ÚLTIMA FASE.



No implementarla antes.



Objetivo:



permitir opcionalmente que Agora despierte el PC IA cuando existen tarjetas pendientes que necesitan capacidades alojadas allí.



IMPORTANTE:



Wake-on-LAN sólo enciende la máquina.



NO resuelve autenticación con AI\_Broker.



La secuencia correcta debe ser:



1\. Agora detecta trabajo elegible.

2\. Según política del usuario, decide solicitar wake.

3\. Envía magic packet.

4\. Espera disponibilidad del PC.

5\. Windows inicia automáticamente los componentes configurados.

6\. AI\_Broker arranca.

7\. AI\_Broker genera su nuevo token dinámico.

8\. Agora AI Runner/Gateway obtiene ese token LOCALMENTE mediante el mecanismo validado en F3.

9\. Runner/Gateway realiza healthchecks.

10\. Runner se anuncia como disponible.

11\. Sólo entonces comienza a reclamar CARDs.



Nunca enviar ni predecir el token desde Agora Desktop.



Políticas mínimas:

\- WoL desactivado;

\- WoL manual desde UI;

\- WoL automático cuando existe trabajo compatible.



Apagado automático es una funcionalidad separada y potencialmente peligrosa:

NO implementarla sin aprobación explícita.



PRUEBAS F8



1\. PC IA apagado + WoL off:

&#x20;  nada ocurre; CARD sigue pending.



2\. WoL manual:

&#x20;  magic packet correcto y estado visible.



3\. WoL auto:

&#x20;  sólo se dispara ante trabajo que realmente requiere ese runner.



4\. PC arranca pero Broker falla:

&#x20;  no reclamar tareas.



5\. Broker arranca:

&#x20;  token dinámico se recupera sólo localmente.



6\. token nunca cruza la red hacia Agora.



7\. healthcheck pasa:

&#x20;  runner se declara disponible.



8\. sólo después se reclama trabajo.



9\. wake repetido:

&#x20;  debe ser idempotente/rate-limited.



10\. PC ya encendido:

&#x20;   no enviar wakes innecesarios.



11\. fallo de wake:

&#x20;   tarjeta permanece pending y queda diagnóstico visible, sin aumentar attempts de ejecución.



12\. no implementar apagado automático sin autorización humana.





GUARDRAILS



No:

\- borrar datos;

\- modificar información crítica;

\- publicar;

\- enviar comunicaciones;

\- crear releases;

\- hacer merge;

\- modificar configuraciones de producción;

\- abrir puertos/firewall;

\- instalar servicios permanentes;

\- cambiar secretos;

\- apagar equipos;

\- ejecutar migraciones irreversibles;



sin autorización humana explícita cuando la acción tenga efecto real.



Para trabajo de desarrollo:

\- trabajar en branch;

\- commits pequeños;

\- no sobrescribir cambios humanos;

\- ejecutar tests antes de proponer merge;

\- nunca fusionar automáticamente salvo autorización expresa.



Cuando una decisión implique romper compatibilidad con Athena o AI\_Broker:

DETENERSE y presentar:

\- problema;

\- alternativas;

\- impacto;

\- recomendación;

\- migración.



No maquillar un fallo para superar una fase.





VERIFICACIÓN GLOBAL



Al final del proyecto debe ser demostrable:



1\. Agora funciona como aplicación desktop sin navegador.

2\. El core puede funcionar sin UI.

3\. PC principal es dueño único del BOARD.

4\. PC IA puede desaparecer y volver sin perder tarjetas.

5\. workers usan pull.

6\. ninguna tarjeta se ejecuta dos veces por carrera de claim.

7\. AI\_Broker sigue siendo router exclusivo de modelos.

8\. Athena conserva funcionamiento standalone.

9\. Athena conserva su conexión directa actual al Broker.

10\. Agora permite delegación persistente.

11\. Gateway funciona sin Board.

12\. Gateway soporta API + OAuth.

13\. Gateway soporta A2A tras F6.

14\. X-Admin-Token dinámico nunca sale del PC IA.

15\. reiniciar AI\_Broker renueva la credencial sin intervención manual normal.

16\. secrets nunca llegan al modelo.

17\. Profile + Card + Skills declaradas forman contexto controlado.

18\. los resultados poseen trazabilidad.

19\. los costes/modelos reales pueden auditarse.

20\. los incidentes relevantes tienen tests de regresión.

21\. WoL, en su fase final, no rompe el modelo de token dinámico.





FORMATO DE SALIDA DURANTE LA IMPLEMENTACIÓN



Al comenzar cada fase presenta primero:



FASE

Objetivo

Hipótesis que se intenta demostrar

Código que habrá que inspeccionar

Cambios previstos

Pruebas de aceptación

Riesgos



Después implementa.



Al terminar presenta:



RESULTADO DE FASE

\- Implementado:

\- Archivos:

\- Tests:

\- Resultado:

\- Evidencias:

\- Verificado realmente:

\- No verificado:

\- Deuda/riesgos:

\- Decisiones descubiertas:

\- Siguiente fase:



En las puertas humanas:

DETENTE después del informe.

No continúes hasta recibir aprobación explícita.





PRIMERA ACCIÓN



No empieces modificando código.



1\. Inspecciona Atomic Agents, Athena y AI\_Broker.

2\. Reconstruye únicamente la arquitectura necesaria para F0-F3.

3\. Confirma con evidencia las interfaces actuales relevantes.

4\. Identifica específicamente el ciclo de vida actual del X-Admin-Token.

5\. Presenta un plan de archivos/módulos inicial.

6\. Señala cualquier contradicción entre esta especificación y el código real.

7\. Sólo entonces comienza F0.



Prioridad ante conflictos:



seguridad e integridad

>

invariantes verificadas

>

compatibilidad Athena/AI\_Broker

>

Atomic Agents

>

simplicidad

>

comodidad de implementación.



No inventes hechos, APIs, capacidades, resultados de pruebas ni verificaciones.

