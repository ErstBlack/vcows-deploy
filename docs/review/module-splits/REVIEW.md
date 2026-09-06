# Module splits — issue #306, measured and rejected

Tree: `master` at `1b215a8`, 2026-09-05. Evidence:
`docs/review/module-splits/reverify/co-change.txt`, produced by the script at the
end of this file.

#306 asked for a split-or-keep verdict on the seven largest production modules.
Its own test: a split is beneficial only if it holds on at least one of three
criteria, measured rather than asserted.

1. **Distinct concerns in one file** that a session edits separately.
2. **Read cost**, after #302's outline-first rule has had its week.
3. **The test file follows**: a split that leaves the test file its old size has
   moved the problem.

No module passes criterion 1, criterion 3 rescues none, and criterion 2 cannot
change the answer for a module whose concerns co-change. Verdict: keep all seven.

## Lens 1 — do the concerns change independently

Method: for every commit that touched the file, take the symbol in each hunk
header of `git show -U0`, drop prose words, and bucket each symbol into the seam
under consideration or `rest`. A commit is then seam-only, rest-only, or both. A
seam that is worth a module is one that commits touch on its own; a seam that
only ever moves with the rest is one boundary a session would have to read
across.

| module | lines | seam considered | seam-only / rest-only / both | verdict |
|---|---|---|---|---|
| `orchestrator/cli.py` | 732 | four groups: parser and entry (`_parser`, `main`, `cmd_version`, `_print_manifest`); output (`_problem`, `_row`, `_report`); run record (`_Run`, `_decision`, `_record`, `_run_dir`, `_write_json`); `_guard`; the verbs | 15 commits carry a named hunk; **11 touch more than one group.** The record group is touched in 10, and 9 of those also touch a verb | **keep** |
| `orchestrator/backends/libvirt/preflight.py` | 653 | ssh transport: `connect`, `ssh_files`, `_write`, `_chatter`, `WRAPPER`, about 97 lines with one caller, `LibvirtBackend.connect` | 2 / 3 / 2 | **keep.** The seam is real and stable since the wrapper landed in `5800207`, and 97 lines does not pay for a module |
| `orchestrator/backends/libvirt/destroy.py` | 557 | none. Every def is on the `destroy()` call chain | `c854e49` touched 9 of 12 defs in one commit | **keep** |
| `orchestrator/backends/base.py` | 440 | policy (`decide`, `_named`, `Action`, `Decision`) vs the carriers and `Backend` | 0 / 7 / 4 | **keep.** Policy has never changed on its own |
| `orchestrator/cloudinit.py` | 418 | seed ISO (`seed_files` through `build_all`) vs NIC checks (`check_vm_structure`, `nic_checks_are_safe`, `check_addressing`, `_parse_interface`, `_parse_address`) | 1 / 2 / 1 | **keep.** The checks validate the values `_network_config` consumes and call `mac_of`; they were co-located deliberately in `e215104` |
| `orchestrator/backends/libvirt/schema.py` | 384 | none. `validate` and five `_check_*` functions are one concern | 18 commits, the highest churn alongside `cli.py` | **keep.** The read count is a question about how it is read, which is #302's |
| `orchestrator/backends/proxmox/schema.py` | 347 | none | 8 commits | **keep.** Its duplication with the libvirt schema is #195, deferred to a third backend |

The one split with any support is the transport in `preflight.py`: two commits
touched it alone. Both were the credential-handling changes (`421cbf1`,
`5800207`), one feature landing in two steps. A module for a seam that has moved
once is surface with no second customer.

## Lens 2 — does the test file follow

| module | its tests | what a split would move |
|---|---|---|
| `cli.py` | `tests/test_cli.py`, 1237 lines, 58 tests, no class or section structure | nothing separable: run-record, guard and verb tests exercise `main` end to end |
| `preflight.py` | `tests/test_libvirt_preflight.py`, 809 lines | 5 transport tests, about 75 lines |
| `base.py` | `tests/test_policy.py`, 139 lines, already its own file | nothing: the policy tests are already apart |
| `cloudinit.py` | `tests/test_seed_iso.py` 216 lines; the check tests sit in `tests/test_libvirt_schema.py`, `tests/test_proxmox_schema.py`, `tests/test_config.py`, `tests/test_properties.py` | nothing: the test side is already split along exactly the seam considered |

Criterion 3 is met only where the tests were already separate, which is no
argument for moving the module.

## Lens 3 — the cost side, corrected

#306 listed the costs of a split. Two of its claims were checked.

**"The mutation baseline is per-module, so a split rewrites those counts" is
wrong.** `docs/mutation-baseline.json` holds four tree-wide numbers, `total`,
`killed`, `survived` and `no_tests`. `scripts/mutants.sh --survivors` lists
survivors per module, but the gate compares totals. A split rewrites nothing in
the baseline. This weakens the case against splitting and does not change the
verdict, because the case for splitting fails on its own.

**The anchor cost is real and concentrated.** Symbols under `orchestrator.cli`
are named on 61 lines of `tests/test_cli.py` and 6 of `docs/findings.md`;
`cloudinit.` on 17 lines each of `tests/test_seed_iso.py` and
`tests/test_libvirt_schema.py`. A split of either renames those.

## Recheck

Criterion 2 is measured by the `session-audit.py` rerun on or about 2026-09-12,
already scheduled for #302. Reopen #306 only if both hold:

* the whole-file read rows for `orchestrator/cli.py` or
  `orchestrator/backends/libvirt/schema.py` have not moved, **and**
* a transcript shows a session that needed one seam named above and nothing
  else in the file.

The co-change data says that session has not happened yet. Without the second
condition, a split gives a session two files to read instead of one.

## Ledger

**Accepted**

| id | item | disposition |
|---|---|---|
| A1 | `preflight.py`'s transport is a nameable seam with one caller | accepted, not split. Two commits alone, both one feature. Revisit if a second transport variant arrives |
| A2 | `cloudinit.py` holds two halves | accepted, not split. The halves share `mac_of` and the `_network_config` contract; the tests are already apart, so a split moves no test weight |
| A3 | `cli.py` is read whole more than any other file | accepted as a reading problem. #302's outline-first rule is the remedy under trial |

**Refuted**

| id | item |
|---|---|
| R1 | #306: the mutation baseline is per-module. It is four tree-wide counts |
| R2 | #306: `cli.py`'s parsing, dispatch, output and exit-code mapping are concerns a session works on separately. 11 of 15 commits crossed those groups |

## Co-change script

```python
import re
import subprocess

NOISE = {"from", "import", "Three", "Two", "Everything", "Adding", "core", "nothing",
         "container", "hypervisor", "them", "with", "differ", "directly", "be"}
cases = {
    "orchestrator/cli.py": {
        "parse": {"_parser", "main", "UsageError", "MANIFEST", "cmd_version", "_print_manifest"},
        "format": {"_problem", "_row", "_report", "_NAME_W", "_VERB_W"},
        "record": {"_Run", "_decision", "_record", "_write_json", "_run_dir", "_timestamp"},
        "guard": {"_guard"},
        "verbs": {"cmd_validate", "_look", "cmd_preflight", "cmd_deploy", "_deploy",
                  "cmd_destroy", "_destroy", "_confirm"}},
    "orchestrator/backends/libvirt/preflight.py": {
        "transport": {"connect", "ssh_files", "_write", "_chatter", "WRAPPER"}},
    "orchestrator/backends/libvirt/destroy.py": {},
    "orchestrator/backends/base.py": {
        "policy": {"decide", "_named", "Action", "Decision"}},
    "orchestrator/cloudinit.py": {
        "checks": {"check_vm_structure", "nic_checks_are_safe", "check_addressing",
                   "_parse_interface", "_parse_address"}},
    "orchestrator/backends/libvirt/schema.py": {},
    "orchestrator/backends/proxmox/schema.py": {},
}
for f, groups in cases.items():
    inv = {n: g for g, ns in groups.items() for n in ns}
    hs = subprocess.run(["git", "log", "--format=%h", "--", f],
                        capture_output=True, text=True).stdout.split()
    print(f"== {f}: {len(hs)} commits")
    for h in hs:
        d = subprocess.run(["git", "show", "-U0", "--format=", h, "--", f],
                           capture_output=True, text=True).stdout
        fns = set(re.findall(r"^@@.*?@@ *(?:def |class )?([A-Za-z_]\w*)", d, re.M)) - NOISE
        gs = sorted({inv.get(n, "rest") for n in fns})
        print(f"{h} groups={gs} names={sorted(fns)}")
```
