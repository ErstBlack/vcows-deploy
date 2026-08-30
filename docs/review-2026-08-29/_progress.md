# Overnight review — progress and budget ledger

Orchestrator: this file is your state. Read it before every batch; skip anything
already marked `done`. Keep the context estimate honest — it is what stops the
run from either starving or overrunning.

Agent line:  `NN-<slug> | done | S1:n S2:n S3:n S4:n S5:n S6:n | <=15-word headline>`
        or:  `NN-<slug> | failed | <one-line reason>`
Phase line:  `PHASE <x> COMPLETE | est. context used: <n>k | <note>`

Target: finish at 200k-250k. Phase budgets are in _PLAN.md.

## Phase A — broad coverage (01-08)

## Phase A' — orchestrator reads findings.md, acceptance.md, README.md

Read ahead of Phase A returns, during batch-1 wait. All three read in full:
acceptance.md (5 defects, A1-A7 run), README.md, findings.md (D-table context,
sections 1-6 + errata appendix).
PHASE A' COMPLETE | est. context used: 40k

## Phase B — depth (09-12) + derived (13-16)

Record what prompted each derived agent; the review's methodology section needs it.

## Phase C — adversarial (17-19)

## Phase D — orchestrator reads all files, verifies, writes docs/2026-08-29-review.md

02-libvirt-connected | done | S1:0 S2:2 S3:5 S4:0 S5:1 S6:0 | destroy() per-object Outcome discarded by cmd_destroy; failed teardown reports success
04-tofu-module | done | S1:0 S2:1 S3:4 S4:1 S5:0 S6:0 | os firmware='efi' emitted alongside pinned loader/nvram; RHEL9 autoselect may override
01-core-seam | done | S1:0 S2:0 S3:4 S4:0 S5:2 S6:2 | decide() by_logical dict collapses duplicate logical names; verdict depends on enum order
03-libvirt-offline | done | S1:0 S2:2 S3:8 S4:0 S5:0 S6:0 | URI userinfo passes validate into tfvars/state; derive_mac collides across deployments
07-decision-compliance | done | S1:0 S2:0 S3:2 S4:0 S5:4 S6:0 | 44 HELD 2 STALE 2 UNVERIFIABLE 0 VIOLATED; R5 manifest never copied to run dir
06-container-supplychain | done | S1:0 S2:0 S3:6 S4:1 S5:1 S6:0 | https:// source_qcow2 reaches network mid-apply; distro RPMs unversioned; manifest not copied
05-driver-cli | done | S1:0 S2:1 S3:5 S4:1 S5:1 S6:0 | README's own --run-dir invocation crashes 2nd deploy FileExistsError; Ctrl-C SIGKILLs tofu
09-comment-accuracy | done | S1:0 S2:0 S3:0 S4:0 S5:6 S6:1 | marker.py still claims destroy is host-wide; 3 comments describe creds in URI
08-silent-failure | done | S1:1 S2:1 S3:7 S4:0 S5:0 S6:0 | destroy.py:243 treats ANY libvirtError as "already gone" then deletes that VM's disks
PHASE A COMPLETE | est. context used: 62k | 8/8 done, 0 failed. Totals S1:1 S2:7 S3:36 S4:2 S5:14 S6:3

## Derived agents commissioned (what prompted each)

13-run-dir-artifact  <- named independently by 05 (4 of its 8 findings are this
   one shape), 06, 08, 02 and 03. Run dir is both the delivered support artifact
   and a secret store; manifest.json never copied (also 07's R5), run.json absent
   on failure paths, tfvars carry URI userinfo.
14-destroy-disk-safety <- 02 and 08 directly disagree. 02: "numeric-code matching
   yes in destroy.py". 08: destroy.py:243 catches ANY libvirtError as "already
   gone" then deletes the disks (S1). Settle it; this is the plan's own example.
15-cross-deployment-collision <- 03 (derive_mac + instance-id keyed on VM name
   alone), 01 (overlay_name/seed_name are undecorated logical names in one flat
   pool), 04 (orphan_volumes iterates cfg["vms"] only), 02 (base image has no
   refcount across deployments). Same shape: flat per-host namespace vs the
   marker's `deployment` field, which exists because that assumption was rejected.
16-warning-severity <- 01 (config WARNINGs vanish on all three verbs), 02
   (per-object Outcome discarded), 05 (problems never recorded), 08 (F-SILENT-03:
   run.json, inventory.json and the success line can contradict each other).
   Audit Severity.WARNING end to end: every construction site vs every consumer.

10-seam-second-backend | done | S1:0 S2:0 S3:2 S4:1 S5:2 S6:1 | seam claim false by 1 core file: config.py IMAGE_SCHEMA forces qcow2 on every backend

NOTE — a peer Claude session proposed its own Phase B roster of four agents,
numbered 12-15, colliding with this roster's filenames. Declined the duplicate
launches and warned it off the directory. Its leads were more specific than mine
and were folded into slots 15 and 16 (see those entries). Recorded here because
the review's methodology section has to say the roster was influenced from
outside this session.
11-lifecycle-recovery | done | S1:1 S2:1 S3:4 S4:0 S5:2 S6:0 | unresolvable volume dropped silently, resurfaces later as wrong-cause orphan error; D30 remedy destroys backing chains
14-destroy-disk-safety | done | S1:0 S2:0 S3:1 S4:0 S5:0 S6:1 | 08's S1 CONFIRMED as mechanism, REFUTED as S1: destroy.py:244 bare catch reaches disk delete, but named triggers die 2 lines later
   -> ADJUSTMENT: 08-silent-failure's S1 demotes to S3. 02's "numeric codes everywhere in destroy.py" is wrong (10 catches: 3 numeric, 2 fatal-by-type, 1 WARNING, 1 bare).
13-run-dir-artifact | done | S1:0 S2:2 S3:3 S4:0 S5:2 S6:0 | mid-apply failure writes no run.json/inventory.json; manifest.json written by no path at all
   -> CORRECTION: URI userinfo reaches main.auto.tfvars.json and plan.bin but NOT the state. Revises 03's claim and findings.md F12's premise (conclusion still holds).
   -> CONFIRMS 06 and 07 independently: manifest.json never copied. Three agents, one defect.
16-warning-severity | done | S1:2 S2:1 S3:1 S4:0 S5:1 S6:1 | destroy() Outcome ignored at cli.py:294 leaks every overlay at exit 0; stale-UUID trigger found
   -> ARBITRATION CHAIN on destroy.py:244, to be settled by me in D2:
      08 filed S1 (mechanism right, named triggers wrong)
      -> 14 reproduced the mechanism, refuted to S3 (no reachable live trigger)
      -> 16 supplied the trigger: destroy+redeploy during the unbounded input()
         pause -> libvirt-assigned UUID misses -> bare-except "already gone" ->
         deletes the NEW running VM's overlay and seed, by deterministic path.
      Provisionally back to S1. This is the strongest finding of the run.
   -> NOTE: file is 206 lines, 6 over the 200-line contract. Accepted, not retried.
   -> argparse exits 2, contradicting the documented "exit codes are 0 and 1".
15-cross-deployment-collision | done | S1:1 S2:2 S3:3 S4:0 S5:0 S6:0 | D30 remedy message tells operator to delete shared base, unlinking every overlay's backing file
   -> CONFIRMS 11 independently (two agents, same conclusion). Adds: the non-destructive
      procedure already works in code and is documented nowhere; MAC and instance-id are
      the open one-way doors, volume/domain names are not.
PHASE B COMPLETE (pending 12) | est. context used: 88k | 09,10,11,13,14,15,16 done; 12 in flight
18-security-adversary | done | S1:1 S2:1 S3:2 S4:0 S5:0 S6:0 | newline in ssh_keyfile/known_hosts injects ssh directives; ProxyCommand = RCE as key holder
   -> Verified clean: no creds in argv/env/image ENV, no shell=True, both YAML safe_load,
      marker XML injection closed, D13 docstring still matches all three ET.fromstring sites.
   -> container/entrypoint.py has ZERO tests (test_image.py always overrides --entrypoint)
      and acts on an unvalidated config before cli.py runs.
12-test-teeth | done | S1:1 S2:2 S3:4 S4:0 S5:1 S6:0 | main.tf has no behavioural test: 12/12 module mutations pass, incl. deleting the marker
   -> Gates pass by SKIPPING: bare `pytest -q` = 210 passed 25 skipped, exit 0, rig and
      image gates never run and nothing asserts they did. Same for the tofu mirror.
   -> Golden file IS load-bearing (caught 4 mutations alone).
   -> Acceptance defects 1,2,3,4 still untested; only defect 5 is pinned.
   -> conftest.py:47-56 `direct` block confirmed; shipped air-gap tofurc only exercised
      by the VCOSW_IMAGE gate, which skips by default.
   -> test_the_build_is_reproducible is flaky (1/25) AND the property is false: seed ISO
      embeds wall-clock timestamps, two builds 1.2s apart differ in 33 bytes.
PHASE B COMPLETE | est. context used: 96k | 09-16 all done, 0 failed

## Phase C

17-verify-severe | done | CONFIRMED 21 · REFUTED 0 · NEEDS-EVIDENCE 2 (of 23 S1/S2 claims)
   -> destroy.py:244 chain SETTLED, against 16: the stale UUID misses with ERR_NO_DOMAIN
      (42), so it fires on the CORRECT branch and is NOT evidence for the bare catch.
      14 was right -> :244 alone is S3. The cmd_destroy pause staleness is a SEPARATE
      defect and stands at S1, needing its own recheck loop. Two defects, not one.
   -> Six severity corrections: F-WARN-01, F-LIFE-01, F-XDEP-01, F-TEETH-01 all S1->S2;
      F-SILENT-01 S1->S3.
   -> F-SEC-01 is the strongest single result of the run and was verified against real
      OpenSSH 9.9p1: `ssh -G` returns `stricthostkeychecking false` and
      `proxycommand /bin/sh -c 'id>/tmp/pwned'`. The injection defeats the check
      schema.py:222 calls the most important one it makes.
   -> Reproduced 6 of 12's mutations incl. acceptance defects 1, 3, 4 verbatim: all pass.
   -> Handed to me for D2: F-TOFU-01 (needs a Rocky 9 host, cannot settle by reading),
      F-WARN-02 severity (my call), F-SEC-02 (needs one rig execution).
18-security-adversary | done | (recorded above)
19-completeness-critic | done | High 5 (G1-G5) Medium 7 Low 5 | 17 gaps, all 12 pre-surfaced confirmed uncovered, 2 new
   -> NEW G1: the shipped image's own provenance is wrong. manifest.json reports
      git_sha e5d5a2c, a commit that does not contain container/entrypoint.py, yet the
      image ships one byte-identical to HEAD. The build ran from a dirty tree. R5 exists
      to answer "which build produced this" and its only artefact answers it wrongly.
      test_the_build_manifest_records_what_shipped asserts six keys and not git_sha.
   -> NEW: findings.md:87 is false. Disk paths are read once at preflight.py:137;
      destroy.py contains no XMLDesc call. That false sentence is the design premise
      F-WARN-02 (the pause-staleness S1) exploits. Nobody else traced it to its root.
   -> OVERTURNS a "checked and sound": 08 and 04 both list run-dir reuse as failing
      loudly. True of deploy, false of destroy - cmd_destroy calls _run_dir with
      exist_ok=True and silently overwrites the earlier deploy's run.json.
   -> Measured: image gate passes (220 passed, 15 skipped). Every image test overrides
      --entrypoint, so container/entrypoint.py executes in NO test at all.
PHASE C COMPLETE | est. context used: 100k | 17,18,19 done, 0 failed
ROSTER COMPLETE: 19/19 agents, 0 failed, 0 retried.
19-completeness-critic | done | (recorded above)

## Phase D
D1 COMPLETE | all 19 agent files read in full | est. context used: 145k
D2 COMPLETE | verified A-N personally against source | est. context used: 165k
   Confirmed: disk loop sits outside destroy.py's try/else; FLOOR ORs NVRAM
   unconditionally; destroy.py has NO XMLDesc (findings.md:87 is false); D30 message
   text verbatim; cmd_destroy has no revalidation across _confirm; destroy is -> None
   at all 3 layers; ssh_config f-string interpolation + both fields unpatterned;
   source_qcow2 unpatterned; main.tf firmware ternary unconditional; _run_dir
   exist_ok=True vs seed.mkdir() bare; MANIFEST read only by cmd_version;
   container/entrypoint.py ABSENT from e5d5a2c which the image's manifest names.
   Severity calls made by me: F-WARN-02 held at S1 (frequency is the weak leg, fix is
   ~12 lines); destroy.py:244 restored to S2 from 14/17's S3; G2 filed S2.
D3 COMPLETE | docs/2026-08-29-review.md written, 8 sections
PHASE D COMPLETE | est. context used: 178k

RUN COMPLETE | 19/19 agents, 0 failed, 0 retried | S1:2 S2:13 S3:~45 S4:6 S5:14 S6:8
   (deduplicated: 15 distinct S1/S2 defects; 5 multi-agent duplicates collapsed)
   Final context: ~178k of the 250k budget.
