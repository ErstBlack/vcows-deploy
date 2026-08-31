# Issue #90 — `variables.tf:10` documents a URI scheme the module is never given

**This lane owns one of #90's eleven items.** #90 is a documentation issue
collecting comment, citation and note drift across all seven dimensions of the
2026-08-31 review. Ten of its eleven items sit in files this lane does not touch
— `docs/cve-baseline.json`, `scripts/lib.sh`, `.github/workflows/image.yml`,
`.gitlab-ci.yml`, `container/entrypoint.py`, `orchestrator/cli.py`,
`orchestrator/backends/libvirt/destroy.py`, `docs/findings.md`, and the stale
baseline row. **Only the `variables.tf` item is planned here.** Do not close #90
from this branch; close the item by name in the commit body and leave the issue
open for the other ten.

Reverified at `aed962d`.

## 1. Reverification verdict

**Reproduced, exact.**

```
$ sed -n '10p' orchestrator/backends/libvirt/tofu/variables.tf
  description = "libvirt connection URI. qemu+ssh:// form, carrying no SSH options at all: …"

$ sed -n '61p' orchestrator/backends/libvirt/render.py
        "uri": connection_uri(target, "sshcmd"),

$ sed -n '9p' tests/golden/libvirt.tfvars.json
  "uri": "qemu+sshcmd://vcows@vcows/system",
```

`render.py:61` is the sole writer of `var.uri`: `grep -rn '"uri"'` over
`orchestrator/` returns the `variable "uri"` declaration itself, the JSON-schema
entries at `schema.py:147,150`, the two reads at `schema.py:229,349`, and this
one write. Nothing else produces the tfvars.

## 2. Anchor table

| anchor | state at `aed962d` |
|---|---|
| `orchestrator/backends/libvirt/tofu/variables.tf:8-11` — `variable "uri"`, description on `:10` | ok, exact |
| `orchestrator/backends/libvirt/render.py:61` `connection_uri(target, "sshcmd")` | ok, exact |
| `tests/golden/libvirt.tfvars.json:9` `qemu+sshcmd://vcows@vcows/system` | ok, exact |
| `orchestrator/backends/libvirt/schema.py:198-211` — `connection_uri`'s docstring, the two-client argument | ok |
| `orchestrator/backends/libvirt/preflight.py:74` `libvirt.open(connection_uri(...))` — the default `ssh` caller | ok |
| `docs/findings.md:410` — "preflight and the apply need different SSH transports" | ok |
| `tests/test_libvirt_render.py:160-161` — asserts the rendered URI is the `sshcmd` one and differs from preflight's | ok |
| `tests/test_libvirt_schema.py:524-525` — both schemes pinned | ok |

## 3. Corrections to the issue body

None. Every anchor #90 names is exact at `aed962d`, and the file is unchanged
since the pin at `672a500`.

## 4. The defect

The description tells a reader that `var.uri` carries the `qemu+ssh://` form. It
never does. `render.py:61` calls `connection_uri(target, "sshcmd")` on every
render, so the module is always handed `qemu+sshcmd://`, and the golden pin
records exactly that.

The sentence is not merely imprecise, it inverts the one distinction this code
exists to hold. `schema.py:198-211` records why: libvirt's own C client does not
recognise `sshcmd` at all, while the provider's `qemu+ssh` dials a hardcoded
monolithic `/var/run/libvirt/libvirt-sock` through a forward SELinux refuses. One
config, two schemes — `preflight.py:74` takes the default `ssh`, the module takes
`sshcmd`. A reader who trusts `variables.tf:10` concludes the opposite of the
thing that was measured on the rig.

The rest of the description is correct and stays: no SSH options in the URI, no
query string, no password, credentials reaching `ssh` through `~/.ssh/config`
written by the container entrypoint.

## 5. The fix

Replace `qemu+ssh://` with `qemu+sshcmd://` in the description at
`variables.tf:10`, and add the one clause that stops the correction from reading
like a typo:

> `libvirt connection URI, always the qemu+sshcmd:// form -- preflight uses
> qemu+ssh:// against the same config, because libvirt's own client does not
> recognise sshcmd and the provider's ssh cannot reach a split-daemon host
> (schema.py:198-211). Carrying no SSH options at all: …`

**`render.py` is settled and is not touched.** `schema.py:198-211` and
`findings.md:410` both record the two-transport decision with the measurement
behind it; `tests/test_libvirt_render.py:160-161` already asserts it in both
directions. This is a description, not behaviour.

### Rejected

* Changing `render.py:61` to emit `qemu+ssh://` — that is the acceptance-run
  defect the `sshcmd` decision exists to fix.
* Dropping the scheme from the description entirely. The scheme is the one thing
  about this variable a reader most needs, and getting it wrong is what filed
  this item.

## 6. Surface cost

One line in one file. No behaviour change, no test change, no new file.

## 7. The failing test

None, and none should be added. The value is already pinned in two places —
`tests/test_libvirt_render.py:160-161` asserts the rendered URI equals
`connection_uri(target, "sshcmd")` and differs from `connection_uri(target)`, and
`tests/golden/libvirt.tfvars.json:9` is the golden pin — so the fact the
description gets wrong is the fact the suite already enforces. Adding a test that
greps a description string would be surface for nothing.

The check that this change is right is the two-command diff in §1: the writer and
the golden pin, read at `aed962d`.

## 8. Verification

1. `just lint` — `tofu fmt` reads `variables.tf`.
2. `just check` — six lint gates ok, `ty` clean, `411 passed, 25 skipped`,
   unchanged.
3. `grep -n 'qemu+ssh' orchestrator/backends/libvirt/tofu/variables.tf` — the
   only remaining bare `qemu+ssh://` must be the one in the new clause that names
   preflight.

## 9. Non-goals

* `render.py:61` and `connection_uri`. Settled, measured, and pinned.
* The other ten items in #90. Different files, different lanes.
* The `preflight` / provider transport split itself.
* `variables.tf`'s other descriptions. Only the `uri` one is wrong.
