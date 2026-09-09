# A12 — Versionado de perfiles y contratos

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A12_versionado_de_perfiles_y_contratos.md`.

## 1. Resumen

Tres cosas, y las tres van juntas:

1. **Antes de ejecutar**: un cliente puede fijar `perfil@linea`. Si esa línea no
   está, se le dice; **nunca se le da otra en silencio**.
2. **Después de ejecutar**: la tarjeta guarda la versión exacta que la ejecutó,
   para que dentro de seis meses se sepa qué produjo un artefacto.
3. **Para siempre**: un contrato publicado no cambia de forma, y ahora eso es
   una prueba y no una buena intención.

Verificado contra el tablero real (§7).

## 2. Archivos

Creados:

- `profiles/task-planning/CONTRACTS/PUBLISHED.lock` — la huella de cada
  contrato publicado.
- `tests/test_profile_versioning.py` — 22 pruebas.

Modificados:

- `src/agora/profiles.py` — `Profile.major`.
- `src/agora/matching.py` — `parse_recipient` y resolución explícita de línea.
- `src/agora/api/service.py` — la tarjeta registra `profile_version` al
  reclamarse.
- `src/agora/contracts.py` — `fingerprint`, `read_published_lock`.
- `src/agora/api/app.py` — `/profiles` se compone campo a campo (§6).
- `tests/test_contract_registry.py` — 6 pruebas más del candado.
- `pyproject.toml`, `src/agora/__init__.py` — **0.2.7 → 0.2.8**.

## 3. Fijar la línea: `perfil@N`

Se extiende `recipient`, que **ya era** el mecanismo de selección explícita, en
vez de añadir uno paralelo:

```json
POST /api/v1/cards
{
  "filename": "curso.md",
  "function": "decompose",
  "request": "break down work: impartir un curso",
  "recipient": "task-decomposer@1"
}
```

- `task-decomposer` → el perfil que haya, como siempre.
- `task-decomposer@1` → **la línea 1**. Si el tablero tiene la 2, la tarjeta no
  se despacha y el motivo dice qué hay: `se pidió task-decomposer@1 y aquí hay
  task-decomposer@2 (2.0.0)`.

### Por qué la línea y no la versión exacta

Fijar `1.3.0` obligaría al cliente a perseguir cada corrección de erratas, y no
lo protegería de nada: dentro de una misma línea, semver ya promete que la
semántica no cambia. Entre líneas no promete nada, y ahí es donde el cliente
necesita poder plantarse. Hay una prueba de que `1.0.0`, `1.4.9` y `1.12.3`
satisfacen todas `@1`, y otra de que `@1.3.0` se rechaza por no ser un número de
línea.

### El rechazo dice la alternativa

Un error que no dice qué hay disponible obliga a adivinar. `candidates` trae
`("task-decomposer@2",)`, y el `reason` trae la versión completa.

El catálogo publica `major` junto a `version`, para que el cliente no tenga que
partir el semver por su cuenta.

## 4. Saber después qué versión lo hizo

Al reclamar, la tarjeta guarda `profile_version` con la versión completa
resuelta, y la bitácora escribe `Remote profile selected: task-decomposer@1.3.0`.

Sin esto, dentro de seis meses el artefacto no dice quién lo hizo: el fichero
del PROFILE habrá cambiado y el resultado no llevaría rastro. Si el perfil no se
puede resolver, **no se escribe nada** en vez de inventarse una versión.

## 5. Un contrato publicado no cambia

Es la línea de la TAREA que más fácil habría sido dejar como intención. Ahora es
un fichero y una prueba.

`CONTRACTS/PUBLISHED.lock` guarda la huella SHA-256 del esquema canónico de cada
contrato:

```
work-breakdown@1 177a8ea4e74136f423ff9e4fc686871bf5b5a55b9234cd2f064fc3ca1a82ad8c
```

La huella se calcula con las claves ordenadas: reordenar un YAML no es un cambio
de contrato, y el espaciado tampoco. Un cambio real —`type: string` a
`type: integer`— sí lo es, y hay pruebas de las dos cosas.

Dos pruebas lo vigilan:

- ninguna huella publicada ha cambiado — con un mensaje que dice lo que hay que
  preguntarse: *«un contrato publicado no se corrige: se publica una versión
  nueva»*;
- el candado no nombra contratos que ya no existen — **retirar** un contrato
  también rompe a quien lo usaba, y debe verse.

El candado es una guardia del repositorio, no un requisito de ejecución: un
despliegue sin él sigue funcionando (`read_published_lock` devuelve vacío).

## 6. Un cambio deliberado que rompe una respuesta pública

`GET /api/v1/profiles` **ya no devuelve `source`**, la ruta del PROFILE en el
disco del tablero.

Salía por serializar el objeto entero con `asdict`. Le revela a cualquier
cliente la estructura de un PC ajeno y no le permite hacer nada. El campo sigue
en `ProfileSummary` porque lo usa la ventana de escritorio del propio tablero,
que corre en esa máquina; lo que se ha quitado es su publicación por el API,
componiendo la respuesta campo a campo.

Es la deuda que A07 §9 y A11 §9 dejaron apuntada para esta fase, y es el único
cambio de esta serie que quita algo de una respuesta pública. Se hace aquí, en
la fase de versionado, y con una prueba que comprueba que ningún campo del
catálogo contiene una ruta.

## 7. Resultados reales

Contra el tablero real en 0.2.8, con el runner y el broker de verdad.

| Comprobación | Resultado |
|---|---|
| El catálogo publica línea y versión | `task-decomposer@1 (1.3.0)` |
| Ya no filtra la ruta del disco | campos: description, function, handles, major, name, produces, refuses, skills, version |
| Fijar la línea que existe | la tarjeta se crea y **es trabajo elegible** |
| Fijar `@9`, que no existe | **no se ofrece con otra línea** |
| La tarjeta termina | `completed` |
| Y dice qué versión la ejecutó | `1.3.0` |
| La bitácora también | sí |

**Veredicto: CORRECTO**, sin ninguna comprobación en rojo.

El caso 3 es el que importa: la tarjeta con `@9` se quedó sin despachar en vez
de ejecutarse con la línea 1. Eso es lo que significa «resolución explícita».

## 8. Pruebas añadidas

`tests/test_profile_versioning.py`, 22 pruebas:

- **La notación** (4 + 4 parametrizadas): separa nombre y línea; una línea que
  no es un número se rechaza; un semver completo también; todos los perfiles
  reales informan de su línea.
- **Resolución explícita** (7): la línea que existe se selecciona; la que no,
  se rechaza **sin sustituir**; el rechazo dice la alternativa; un parche dentro
  de la línea sigue valiendo; nombrar sin línea funciona como siempre; una
  fijación mal formada no elige perfil; fijar la línea no salta la comprobación
  de `function`; el emparejamiento por handle queda intacto.
- **Trazabilidad** (4): la tarjeta registra la versión exacta; la bitácora la
  dice; el cliente la ve; un perfil desconocido no inventa una versión.
- **El catálogo** (3): publica la versión; publica la línea; **no filtra el
  sistema de ficheros**.

Y 6 más en `tests/test_contract_registry.py`: ninguna huella publicada ha
cambiado; el candado no nombra contratos muertos; la huella ignora el orden de
las claves; la huella nota un cambio real; una línea mal formada del candado se
rechaza; un despliegue sin candado sigue cargando.

Suite completa: **465 pruebas, todas en verde** (eran 437 al cerrar A11).

## 9. Compatibilidad hacia atrás

- `recipient` sin `@` se comporta exactamente como siempre.
- `profile_version` es una clave nueva en la metadata; las CARD antiguas no la
  tienen y no pasa nada.
- `major` es un campo nuevo en `/profiles`.
- **Salvo** la retirada de `source` (§6), que es deliberada y está justificada.

## 10. Riesgos y deuda

- **Solo hay una línea de cada perfil a la vez.** El tablero tiene un
  `PROFILE.md` por carpeta, así que `@1` y `@2` no pueden convivir hoy. Fijar la
  línea sirve para que un cliente **detecte** que le han cambiado el perfil
  debajo, no para elegir entre dos. Servir varias a la vez exigiría carpetas por
  versión, y no hay caso que lo pida.
- **El candado se mantiene a mano.** Añadir un contrato exige añadir su línea.
  Es a propósito: si se regenerara solo, no guardaría nada.
- **El runner del PC de IA sigue en 0.2.2.** Nada de A07–A12 le afecta, pero la
  distancia es ya de seis versiones. Deuda arrastrada desde A10 §9.

## 11. Siguiente fase

A13 — frontera explícita: Agora no es un calendario.
