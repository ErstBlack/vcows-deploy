# Verify — dimension G, the lows and nits · `origin/master` `672a500`

Confirmer, Phase 3. Six findings from `finders/G-unread-list.md` (`RX-G4`–`RX-G9`); the three
mediums went to another verifier. Every `file:line` below was re-read at `672a500` in the
detached worktree; the two reproductions ran against a `cp -a` copy, no tracked file touched,
nothing reached the rig.

---

## RX-G4 — the `chmod` guard catches `PermissionError` only

**DOWNGRADED to nit.** The mechanism is exactly as described; the reason given for caring is not.

Citations hold. `orchestrator/cli.py:155` is `os.chmod(path, 0o700)`, `:156` is
`except PermissionError:`, and the comment stating the intent — "that must not stop a run that
is otherwise fine" — is at `:148-152`. `tests/test_cli.py:766-769` raises `PermissionError`
specifically (the test opens at `:757`).

Reproduced, `_run_dir` with `os.chmod` monkeypatched to raise `OSError(EROFS)` on a 0755
directory:

```
EROFS OSError is PermissionError? False  OSError
EACCES OSError is PermissionError? True  PermissionError
_run_dir raised OSError: [Errno 30] Read-only file system: '.../given'
```

So `EROFS` does escape the guard and does end the run at the chmod. What does not hold is the
consequence: "the message points at the mode rather than the mount" is backwards. The escaping
`OSError` reaches `main`'s catch-all (`cli.py:719-724`) and prints
`error: OSError: [Errno 30] Read-only file system: '/runs'` — which names the mount. The
warning the widened guard would print instead names the *mode*
(`cannot make /runs 0700; it stays 0755`) and then lets the run continue to a second, later
failure at the first write. **The proposed fix makes the diagnosis worse for the only case
anyone has reproduced.**

Every reachable non-`PermissionError` shape found is `EROFS`, where the run is doomed anyway —
`EACCES` and `EPERM` are both `PermissionError`. A filesystem that refuses `chmod` with
`ENOTSUP` while writes succeed is the only "otherwise fine" case, and it is hypothetical here.

Worth filing only as: the comment at `:148-152` claims a breadth the `except` does not have.
Fix the comment, or widen to `OSError` *and* keep the errno in the message. One line either way.
Not `low` — no operator-visible defect was demonstrated.

## RX-G5 — `variables.tf` documents `var.uri` as `qemu+ssh://`

**CONFIRMED, downgraded to nit.** The claim is true and the fix is one word, in `variables.tf`.

`orchestrator/backends/libvirt/tofu/variables.tf:10` reads
`description = "libvirt connection URI. qemu+ssh:// form, carrying no SSH options at all: the
provider runs ssh, …"`. The only writer of that variable is
`orchestrator/backends/libvirt/render.py:61`, `connection_uri(target, "sshcmd")`; the only
reader is `tofu/main.tf:17`, `uri = var.uri`; and `tests/golden/libvirt.tfvars.json:9` pins the
rendered value as `"qemu+sshcmd://vcows@vcows/system"`. There is no other writer. The rest of
the sentence describes `sshcmd`'s behaviour correctly — it is the scheme name that is wrong.

**The fix is the description, not `render.py`.** The two-schemes-from-one-config split is
deliberate and settled: `schema.py:198-211` documents why the C client cannot take `sshcmd` and
the provider cannot take `ssh`, and `docs/findings.md:410` records the same. Touching `render.py`
would reopen a settled decision to fix a sentence. `18-security-adversary`'s F-SEC-04 is a
separate axis — it is about the URI's *userinfo*, which `_check_target` did not inspect — and
does not bear on the scheme name.

Severity `nit` rather than `low`: no runtime consequence. It is still worth the word, because
this file is the module's only in-tree explanation of `var.uri` and it names the one thing about
this backend a reader is most likely to get wrong.

## RX-G6 — `cmd_version` tolerates two of the four failures `_tofu_version` tolerates

**CONFIRMED, downgraded to nit.** Citations exact, consequence smaller than stated.

`cli.py:658` is `except (tofu.TofuError, OSError) as exc:`; `cli.py:335` is
`except (tofu.TofuError, subprocess.SubprocessError, ValueError, OSError) as exc:` for the same
`tofu.version()` call. `tofu._capture` (`tofu.py:238-264`) can raise `TofuError` (non-zero exit),
`subprocess.TimeoutExpired` at `SHORT_TIMEOUT` (`tofu.py:44`, 120), `json.JSONDecodeError` from
`json.loads(completed.stdout or "{}")` at `:264`, and `OSError` from the exec. Two of the four
escape `cmd_version` into `main`'s catch-all: `error: TimeoutExpired: …`, exit 1, instead of
`tofu: unavailable (…)`, exit 0.

The finding overstates the tie to the recorded regression. `_print_manifest()` is called at
`cli.py:655`, *before* the `tofu.version()` call, and its own `except` at `:650` is unrelated —
so the failure the `_print_manifest` docstring (`:634-639`) describes, "answered nothing at all",
does not recur. What is left is the exit code and the trailing line.

Reachability is thin: the image pins the OpenTofu RPM by SHA256 (`Containerfile:62`), so a
`tofu version` that hangs past 120s or emits unparseable JSON is not a shape this delivery
produces. Fix cost is zero — copy the tuple from `:335` — which is why it is still worth filing.

## RX-G7 — the errata covers the doc's commands, not its config sample

**DOWNGRADED to nit; the substantive half is REFUTED.**

The narrow claim is true: `docs/findings.md:417-438` has no row for §4.3, and the §4.3 sample
(`docs/orchestrator-architecture.md:155-191`) is wrong in every way the finder lists. Spot-checked
and holding, with one drift: `backend: proxmox` against `REGISTRY = {"libvirt": …}`
(`orchestrator/backends/__init__.py:21`); top-level `lifecycle:`/`state:`/`defaults:` against
`"additionalProperties": False` in the core schema (`orchestrator/config.py:79`);
`image.distro`/`image.sha256` with no `base_volume_name` against
`"required": ["source_qcow2", "base_volume_name"]` at **`orchestrator/config.py:44`** (the
finder cited `:43`, which is that schema's `additionalProperties`); and
`vms[].cloudinit.user_data: ./file.yaml` against `user_data = vm.get("user_data")` at
`orchestrator/backends/libvirt/prepare.py:50`, a top-level string used verbatim.

None of that makes it a finding. `docs/orchestrator-architecture.md` is archived background
written before any code existed (`_BRIEF.md`: "Never cite it against the code without checking
there first"), and the sample's very first line configures a **`proxmox`** backend that
`findings.md` §5 cut. A sample config for a three-backend tool that does not exist is not
something a reader mistakes for the current schema; adding an errata row for it prices a
line-by-line audit of an archived document.

The second half — `…architecture.md:97`'s "fixed output shape (`vm_name → {ip, hostname,
backend}`) that every backend module must satisfy" — is the item the finder says a
second-backend author could act on, and **it is already corrected in the authoritative
document**, not by the errata table but by the body: `docs/findings.md:271` ("Without this step
your module's output block *is* your public API… `parse_outputs` … is per-backend because each
backend's `.tf` module declares its own `output` blocks, so the raw shape differs"), and
`docs/findings.md:322` ("emit `inventory.json` with a minimal shape … document it as
unstable"). `tofu/outputs.tf:1-3` says the same thing in the module. The errata's own §4.2 row
already routes backend-addition claims to §3. So the reader at risk is served.

The residue, and the only part worth a sentence, is the errata preamble at
`docs/findings.md:419`: "Do not copy **commands** out of it without checking here first." The
table beneath it corrects versions, a licence, and three design claims (§4.2, §10, §5.3), so the
preamble's scope is narrower than the table's. One word there — "commands or claims" — closes
the whole finding. Two errata rows do not.

## RX-G8 — `entrypoint.py:189` cites a `cli.py` line that moved

**CONFIRMED nit · DUPLICATE, already settled in `verify/AB-lows.md:105` as `RX-B5`.**

`container/entrypoint.py:189` reads "``orchestrator/cli.py:670``'s `os.umask(0o077)` cannot close
that"; `os.umask(0o077)` is at `orchestrator/cli.py:705`. The reasoning around it is still
correct. The AB verifier confirmed this and established it is real drift, not an authoring
error — the number was right at `8b24bfb`.

One correction to G's text, matching AB's: `cli.py:670` is `parser.add_argument(`, not the
`ArgumentParser` constructor, which is `:669`. Immaterial.

**Carry it under one id.** `RX-G8` and `RX-B5` are the same one-number edit and must not be
filed twice.

## RX-G9 — a shared MAC can be dropped from the collision index

**CONFIRMED nit.** Exhibited, order-dependent, and the preconditions are compound.

`preflight.py:186` is `by_mac.setdefault(mac, name)` inside `_domains`; `preflight.py:551` is
`by_mac = {mac: owner for mac, owner in by_mac.items() if owner not in ours}`; `by_mac` is
consumed by `address_conflicts` at `preflight.py:502,529-537`.

Built with `tests/fake_libvirt`: two domains sharing `52:54:00:c0:ff:ee` — one carrying our
marker (`deployment: lab-a`, logical name `app01`) under a different hypervisor name, one
unmarked and foreign — plus a config whose `app02` pins that MAC explicitly
(`schema.py:112` allows `nics[].mac`; `mac_of`, `schema.py:186-187`, prefers it over the
derivation). Only `listAllDomains` order changed between the two runs:

```
ours first     -> MAC problems: []
foreign first  -> MAC problems: ["MAC 52:54:00:c0:ff:ee is already configured on domain 'intruder'."]
```

So the claim reproduces: enumeration order decides whether a foreign domain's MAC claim survives,
and `address_conflicts` calls the address free when it does not.

Why it stays a nit rather than a low. For a dropped entry to matter, the VM whose MAC was
dropped must be one vcows is about to **create**. Every domain the `:551` filter removes is one
whose marker names a config VM, and `base.decide` gives every such VM `SKIP`
(`backends/base.py:315-324`, same deployment) or `REFUSE` (`:326-334`, different deployment) —
never `CREATE`. So the derived-MAC path cannot reach a false free at all: distinct
`(deployment, name, index)` inputs give distinct MACs. The reproduction above needs an explicit
`nics[].mac` on a *different* VM, pointing at a MAC our own existing domain already holds, on a
host that already has two domains sharing it — a state nothing in this tool creates.

Fix cost argues for leaving it. `by_mac` would have to become `dict[str, list[str]]`, which
changes `_domains`' return type, the `:551` comprehension, and `address_conflicts`' message
("already configured on domain 'x'" becomes a list). That is three signatures and a message
change for a precondition the tool cannot produce. File it as a nit with the reproduction
attached; a comment at `:186` recording that first-writer-wins is deliberate is the proportionate
fix.
