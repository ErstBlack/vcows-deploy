#!/usr/bin/env bash
# Mutation testing, with a verdict.
#
#   scripts/mutants.sh                    fail on anything worse than the baseline
#   scripts/mutants.sh --write-baseline   record what is there now as accepted
#
# **`mutmut run` exits 0 whatever it finds.** Measured: 3835 mutants, 964 of them
# surviving, exit code 0. So a pipeline step that just called it would be green
# forever -- the vacuous pass this repo already has a name for, arriving through
# the front door. Everything below exists to turn that run into a pass or a fail.
#
# **Differential, not absolute**, for the same reason `scripts/image-scan.sh` is.
# A gate demanding zero survivors would be red from the first run and stay red, and
# no pipeline can fix a survivor -- only someone writing tests can. An always-red
# gate gets muted within a month and is then green by neglect. So
# docs/mutation-baseline.json holds what has been looked at and accepted, and this
# fails only on what is *new*. Red means the change under review made it worse.
#
# What the remaining survivors are, measured at #146 by decoding `mutants/*.meta`:
# roughly 330 of the 676 mutate the *text* of a log line or a `Problem` message --
# `"XX...XX"`, a case flip, a format argument swapped for None. Killing one means
# asserting on prose, which pins the wording of a message this repo revises
# deliberately, so they are a floor rather than a backlog. The rest are branches
# and constants a test executes and no test notices, and those are worth writing.
#
# **Do not suppress the message-text ones.** mutmut 3.7.0 offers three ways and
# all three are rejected here: `do_not_mutate` (a file-path glob),
# `do_not_mutate_patterns` (regexes matched per source line) and
# `# pragma: no mutate` in its bare, `block` and `start`/`end` forms. Two reasons.
# Suppressing a mutant removes it from the denominator without a test being
# written, which is the same defect as hand-editing the baseline below. And the
# instrument is too blunt for this target: a pattern matching `log.debug(` also
# hides the real mutants that share those lines -- `preflight.py` has a surviving
# `len(err) > 2` -> `> 3` inside one. A floor that is honest about its size beats
# a smaller number that stopped counting.
#
# Two counts are tracked rather than one, because they are different defects.
# `survived` is a mutant some test executed and no test noticed. `no_tests` is a
# mutant no test reached at all, which coverage should have caught first and is
# the more serious of the two.
#
# The mutation score is deliberately not a gate. It moves when the denominator
# moves -- deleting dead code raises it without a test being written -- so it is
# reported and never asserted on.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BASELINE="$REPO/docs/mutation-baseline.json"
STATS="$REPO/mutants/mutmut-cicd-stats.json"

# `mutmut run` leaves its verdict in a cache under mutants/ and prints the summary
# as a transient progress line. `export-cicd-stats` is the only machine-readable
# form, and it writes beside the cache rather than to stdout.
run_and_export() {
    need_venv
    log "running mutmut over orchestrator/ and container/"
    "$REPO/.venv/bin/mutmut" run
    "$REPO/.venv/bin/mutmut" export-cicd-stats >/dev/null
    [ -f "$STATS" ] || die "mutmut wrote no $STATS -- nothing to judge this run by"
}

# The floor, and it is the check that matters most. If mutant *generation* broke --
# a syntax error mutmut cannot parse, a source_paths typo, an empty tree -- then
# `total` is 0, `survived` is 0, 0 is not greater than the baseline, and this
# script would report an improvement. That is the one failure a subset check
# cannot see, so assert the run happened before trusting its silence.
#
# Measured at the current tree: 3835 mutants over 17 files.
generation_floor() {
    local total="$1"
    [ "$total" -gt 0 ] ||
        die "mutmut generated no mutants -- source_paths in pyproject.toml, or a file it could not parse. An empty run is not a clean one."
}

main() {
    need jq

    local stats total killed survived no_tests score
    local base_survived base_no_tests base_total

    run_and_export

    stats="$(cat "$STATS")"
    total="$(jq -r '.total'    <<<"$stats")"
    killed="$(jq -r '.killed'   <<<"$stats")"
    survived="$(jq -r '.survived' <<<"$stats")"
    no_tests="$(jq -r '.no_tests' <<<"$stats")"

    generation_floor "$total"

    # Integer percent, and only ever printed. `killed * 100 / total` in bash is
    # truncating division, which is the right direction for a figure nobody
    # asserts on: it never flatters.
    score=$(( killed * 100 / total ))
    log ""
    log "  mutants   $total"
    log "  killed    $killed  (${score}%)"
    log "  survived  $survived"
    log "  no tests  $no_tests"
    log ""

    if [ "${1:-}" = "--write-baseline" ]; then
        jq -n --arg generated "$(now_utc || true)" \
              --argjson total "$total" --argjson killed "$killed" \
              --argjson survived "$survived" --argjson no_tests "$no_tests" \
              '{generated: $generated,
                note: "Mutation results reviewed and accepted at this tree. `survived` is a mutant some test ran and no test noticed; `no_tests` is one no test reached at all. scripts/mutants.sh fails when either rises. Lowering these is writing a test; raising one deliberately is a hand-edit with a reason in the commit body. Roughly 330 of them mutate the text of a log line or a Problem message rather than a branch -- killing those means asserting on prose, so they are a floor and not a backlog. See #146.",
                total: $total, killed: $killed,
                survived: $survived, no_tests: $no_tests}' > "$BASELINE"
        log "wrote $(basename "$BASELINE"): $survived survived, $no_tests with no tests"
        return
    fi

    [ -f "$BASELINE" ] ||
        die "no docs/mutation-baseline.json yet -- run 'scripts/mutants.sh --write-baseline' once the numbers are settled"

    base_survived="$(jq -r '.survived' "$BASELINE")"
    base_no_tests="$(jq -r '.no_tests' "$BASELINE")"
    base_total="$(jq -r '.total' "$BASELINE")"

    local failed=0
    if [ "$survived" -gt "$base_survived" ]; then
        log "  FAIL  survived rose from $base_survived to $survived"
        log "        $(( survived - base_survived )) mutant(s) that a test executes and no test notices."
        log "        'mutmut results' lists them; 'mutmut show <name>' prints the diff."
        failed=1
    fi
    if [ "$no_tests" -gt "$base_no_tests" ]; then
        log "  FAIL  no-tests rose from $base_no_tests to $no_tests"
        log "        $(( no_tests - base_no_tests )) mutant(s) no test reaches at all."
        failed=1
    fi
    [ "$failed" -eq 0 ] || die "mutation testing regressed against docs/mutation-baseline.json"

    # The other direction. Not a failure -- somebody wrote a test -- but the
    # baseline is now loose by exactly that much, and a ceiling nobody tightens
    # stops being a ceiling. Same argument as image-scan.sh's `gone` reporting.
    if [ "$survived" -lt "$base_survived" ] || [ "$no_tests" -lt "$base_no_tests" ]; then
        log "  baseline is now loose: survived $base_survived -> $survived, no-tests $base_no_tests -> $no_tests"
        log "  re-run with --write-baseline to tighten it"
    fi
    # Reported rather than gated: the count moves whenever code is added or
    # deleted, so it is context for the two numbers above and not a verdict.
    [ "$total" -eq "$base_total" ] ||
        log "  (mutant count moved $base_total -> $total; the tree changed size)"

    log "no mutation regression against the baseline"
}

main "$@"
