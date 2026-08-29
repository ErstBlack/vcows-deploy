# Destroy disk safety — adjudication

Agent: 14-destroy-disk-safety · Scope: `orchestrator/backends/libvirt/destroy.py`, `tests/fake_libvirt.py`, `tests/test_libvirt_destroy.py`, `orchestrator/cli.py:cmd_destroy` (caller only) · Date: 2026-08-29

## Summary

* **08's mechanism is CONFIRMED, its severity is not.** `destroy.py:244` is a bare
  `except libvirt.libvirtError:` and control reaches `_delete_volume` for *every*
  error code — I reproduced all eight I tried, including 45. But the triggers 08
  names (transport drop, daemon restart mid-loop) do **not** reach the harm: the
  same dead connection breaks `storageVolLookupByPath` two lines later, which is a
  fatal `Problem`. Those runs raise `DestroyError` and delete nothing. Reproduced.
* The branch only causes silent data loss when the **domain** driver fails while
  the **storage** driver still answers. Server-side `virDomainLookupByUUID` reaches
  exactly two codes: `NO_DOMAIN` (42) and `ACCESS_DENIED` (88) from the ACL check —
  and the same ACL framework gates `storage_vol.delete`. Filed **S3**, not S1: real
  defect, one-line fix, no trigger anyone has or is likely to configure.
* **02's blanket claim is wrong.** "Destroy matches libvirt errors numerically
  everywhere" is false at `:244`, and two more catches match by type only (they are
  safe because every branch is fatal, but the summary as written is not accurate).
  On this point 08's reading is the correct one.
* **Q3, answered directly: exactly one path deletes a disk belonging to a domain
  vcows did not successfully undefine, and it is `:244`.** `_stop` and `_undefine`
  both `continue` on failure. No vcows-created domain can ever carry the golden base
  in `target.disks`, from either end — `main.tf` reaches it only via `backing_store`,
  and `disks_of` never reads one.
* **02's other two claims hold.** `<backingStore>` exclusion survives nested chains,
  reversed element order, and block/volume/network/floppy/lun disks. `NVRAM` cannot
  be shed: `FLOOR` includes bit 4 unconditionally and the single retry passes `FLOOR`
  literally.

## Findings

### F-DSK-01 — the one unqualified libvirt catch in destroy.py deletes disks on a domain it never resolved
- **Severity:** S3
- **Confidence:** high on the mechanism; low on any trigger reaching it
- **Location:** `orchestrator/backends/libvirt/destroy.py:244-246`, falling through to `:254-255`
- **What:** every other catch in the file either matches a numeric code or makes
  every branch fatal. This one does neither:

  ```python
  except libvirt.libvirtError:
      # Already gone. Its disks may not be, so they are still resolved below.
      out.skipped.append(target.name)
  ```

  No code is checked, no `Problem` is appended, and the `for path in target.disks`
  loop runs regardless. Only `VIR_ERR_NO_DOMAIN` (42) justifies the comment.
  Combined with `cli.py:296-303` — which ignores the returned `Outcome` and prints
  `destroyed {len(targets)} VM(s)` — a live domain keeps running while its overlay
  and seed ISO are unlinked, exit 0.

  Full enumeration of every catch, guard and early return in the file:

  | line | construct | classification | on failure |
  |---|---|---|---|
  | `:103` | `if not dom.isActive(): return True` | guard, call unguarded | escapes the loop |
  | `:107-115` | `except libvirtError` | **numeric** (55 + re-check `isActive`) | fatal `Problem`, `False` |
  | `:133-142` | `except libvirtError` | **numeric** (8, and `mask == FLOOR`) | fatal `Problem`, `False` |
  | `:154-162` | `except libvirtError` (retry) | by type | always fatal — safe by construction |
  | `:185-199` | `except libvirtError` | **numeric** (50) | fatal `Problem` |
  | `:218-219` | `if not pool.isActive(): continue` | guard | silent; no `Problem` |
  | `:222-230` | `except libvirtError` | by type | WARNING only, non-fatal |
  | `:244-246` | `except libvirtError` | **bare** | silent, and proceeds to delete |
  | `:248`, `:250` | `continue` after `_stop`/`_undefine` | guard | correct — disks untouched |
  | `:257-258` | `if out.failed: raise` | terminal | ERROR only |

  Unguarded calls that can abort the whole loop: `getLibVersion()` `:238`,
  `listAllStoragePools()` `:217`, `pool.isActive()`/`pool.name()` `:218`/`:226`,
  `dom.isActive()` `:103` and `:110`. (That last set is 02's F-LVC-03; not re-filed.)
- **Why it matters here:** the file's own docstring calls `vol.delete` protection-free
  and names the `<backingStore>` exclusion as the only thing between it and the shared
  golden image. This branch is the one place where the domain-side safety interlock
  (`_stop` → `_undefine` → only then delete) is bypassed rather than failed closed.
  The harm is bounded to the target's own overlay and seed, not the base.
- **Evidence:** two scripts against `tests/fake_libvirt`, run with
  `.venv/bin/python <script>`; sources under the session scratchpad.

  Injecting each code into `lookupByUUIDString` with the storage driver healthy —
  every one takes the "already gone" branch, the domain is never touched, both
  volumes are deleted, and `destroy()` returns normally:

  ```
   code name                              raised  dom active  deleted
     42 NO_DOMAIN (really gone)           -       True        ['app01-seed.iso', 'app01.qcow2']
     45 AUTH_FAILED                       -       True        ['app01-seed.iso', 'app01.qcow2']
      6 INVALID_CONN (daemon restarted)   -       True        ['app01-seed.iso', 'app01.qcow2']
     38 SYSTEM_ERROR (transport drop)     -       True        ['app01-seed.iso', 'app01.qcow2']
      1 INTERNAL_ERROR                    -       True        ['app01-seed.iso', 'app01.qcow2']
     88 ACCESS_DENIED (polkit)            -       True        ['app01-seed.iso', 'app01.qcow2']
     39 RPC (protocol error)              -       True        ['app01-seed.iso', 'app01.qcow2']
     68 OPERATION_TIMEOUT                 -       True        ['app01-seed.iso', 'app01.qcow2']
  ```

  That is 08's S1, reproduced exactly. But making the *connection* dead rather than
  just the lookup — the actual shape of a transport drop or a daemon restart, where
  `storageVolLookupByPath` fails too — is loud and destroys nothing:

  ```
  38 SYSTEM_ERROR:  DestroyError -> error [app01]: could not delete /pool/app01.qcow2: End of file while reading data  deleted=[]
   1 INTERNAL_ERROR: DestroyError -> ...                                                                               deleted=[]
   6 INVALID_CONN:   DestroyError -> ...                                                                               deleted=[]
  88 ACCESS_DENIED (domain driver only): returned normally  running=True  domlog=[]  deleted=['app01-seed.iso','app01.qcow2']
  ```

  The coverage gap that let this through:
  `test_a_domain_already_gone_still_has_its_disks_collected`
  (`tests/test_libvirt_destroy.py:130-142`) is the only test on this branch, and it
  reaches it by passing a UUID absent from the fake's domain list — so it exercises
  code 42 and nothing else. `23 passed` on the file as it stands.
- **Fix:** add `ERR_NO_DOMAIN = 42` beside the three constants at `:55-57`, pin it in
  `test_error_codes_match_the_installed_binding`, and re-raise or append a fatal
  `Problem` (and `continue`) for any other code. The disk loop must not run for a
  target whose domain did not resolve.
- **Cost of the fix:** one constant, one `if`, one assertion. It makes this catch
  match the three above it rather than adding a new shape. No new surface.

### F-DSK-02 — the fake resolves volume paths by basename, so no test can prove pool scoping
- **Severity:** S6
- **Confidence:** high
- **Location:** `tests/fake_libvirt.py:161-165`
- **What:** `FakeConnection.storageVolLookupByPath` matches `path.endswith("/" + name)`
  across every pool. Real libvirt matches the exact path. `/poolA/app01.qcow2` and
  `/poolB/app01.qcow2` are indistinguishable to the fixture, so a test written to
  prove destroy deletes the right pool's volume would pass whether or not it does.
- **Why it matters here:** volume names are the undecorated logical name (D16), so
  short names collide across pools easily, and `_refresh_pools` deliberately refreshes
  *every* pool. This is the one fixture looseness in a file whose docstring argues it
  has hypervisor semantics on purpose.
- **Evidence:** the line, against `_delete_volume`'s `conn.storageVolLookupByPath(path)`.
- **Fix:** key on the full path the pool would report rather than the suffix.
- **Cost of the fix:** the fake would need a `path` per pool. Two lines; justified only
  if a pool-scoping test is ever written, which is why this is S6 and not S4.

## Checked and sound

* **`<backingStore>` is never followed — confirmed independently of 02.** Hand-built
  a domain with a two-level backing chain, a `<backingStore>` serialised *before* the
  disk's own `<source>`, a disk with only a `<backingStore>`, an empty cdrom tray, and
  block / volume-type / network / floppy / lun disks. `disks_of` returned
  `('/pool/app01.qcow2', '/pool/app01-seed.iso')` and `('/pool/app02.qcow2',)` —
  `GOLDEN-BASE.qcow2` and `GRANDPARENT.qcow2` never appear. `disk.find("source")`
  matches direct children only, so element order is irrelevant.
* **The base cannot enter `target.disks` from the other end either.**
  `main.tf:158,165` give each domain exactly its own overlay and its own seed as
  `source.file`; the base is reached only through `backing_store` (`:49-52`). A
  marked domain therefore cannot carry the shared image, so F-DSK-01's blast radius
  is one VM's own two volumes.
* **The NVRAM floor cannot be shed.** `FLOOR` ORs bit 4 unconditionally (`:47`),
  `undefine_mask` only ever ORs onto it (`:71-75`), the retry passes the literal
  `FLOOR` (`:153`), and the retry is entered only on `ERR_INVALID_ARG` with
  `mask != FLOOR` — one attempt, no loop, and a genuine `OPERATION_INVALID` refusal
  cannot be swallowed. `undefineFlags` appears nowhere else in `orchestrator/` or
  `container/`. 02 is right.
* **`Existing.id` is the real domain UUID** (`preflight.py:135`, `dom.UUIDString()`),
  not the marker's derived `uuid5`. So `lookupByUUIDString` cannot resolve the wrong
  domain, and `VIR_ERR_INVALID_ARG` from an unparseable UUID string — which *would*
  hit F-DSK-01's branch with a healthy connection — is unreachable.
* **`_stop` and `_undefine` fail closed.** Both return `False` and both call sites
  `continue`, so a domain that would not stop or would not undefine keeps its disks.
  Verified by reading and by `test_a_real_stop_failure_aborts_that_domain_and_is_fatal`.
* **Corroborated, already filed — do not double-count.** The inactive-pool skip at
  `:218-219` reading as "already gone" (02 F-LVC-01 / 08 F-SILENT-02); the discarded
  `Outcome` at `cli.py:296-303` (02 F-LVC-01); the unguarded `dom.isActive()` at
  `:103` (02 F-LVC-03); the `# since 1.2.9` / "predate 1.2.9" contradiction at
  `:41` vs `:46` (02 F-LVC-08). All four confirmed; none re-filed here.

## Not checked

* No connection to the rig, per my instructions. The claim that
  `virDomainLookupByUUID` reaches only `NO_DOMAIN` and `ACCESS_DENIED` server-side
  is from libvirt's dispatch path, not from observation on RHEL 9.
* `preflight.py` beyond `disks_of`, `_domains` and `Existing` construction — 02 and
  08 both cover it and I had no reason to disagree with either there.
* Whether a split `virtproxyd` deployment can present a domain-driver failure with a
  live storage driver. I could not settle it without a host; if it can, F-DSK-01
  moves to S2 or S1 on trigger, and the fix is unchanged either way.

## Deserves its own agent

* **The `--yes` window.** `cmd_destroy` holds the connection open across an
  interactive `input()` prompt (`cli.py:288`, `:334-341`) with no bound. Everything
  preflight found — names, UUIDs, disk paths — is acted on afterwards, however long
  that is. Whether the staleness of that data across an unbounded pause matters is a
  question neither 02, 08 nor I looked at.
