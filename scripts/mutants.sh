#!/usr/bin/env bash
# Mutation testing, with a verdict.
#
#   scripts/mutants.sh                    fail on anything worse than the baseline
#   scripts/mutants.sh --write-baseline   record what is there now as accepted
#   scripts/mutants.sh --verdict DIR      judge the summed stats of a sharded run
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
# What the remaining survivors are, measured when #146 closed by decoding
# `mutants/*.meta` against the mutated source: roughly 440 of the 491 mutate the
# *text* of a log line or a `Problem` message -- `"XX...XX"`, a case flip, a
# format argument swapped for None or dropped. Killing one means asserting on
# prose, which pins the wording of a message this repo revises deliberately, so
# they are a floor rather than a backlog. The other ~50 are equivalent mutants,
# each named in #146 with the reason no test can tell it from the original.
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
# **Sharding, for CI only.** `VCOWS_MUTANTS_SHARD=k/N` runs the mutants whose
# ordinal ends in a digit `d` with `d % N == k-1`, writes that shard's numbers to
# .cache/mutation-stats/shard-k.json and judges nothing; `--verdict DIR` sums
# those files and applies the gate above to the sum, so the verdict a sharded
# pipeline reaches is the one a single run reaches. `mutmut run` takes mutant
# names and matches them with fnmatch (`collect_source_file_mutation_data` in its
# __main__.py), and every key ends in `__mutmut_<ordinal>`, so a shard is one glob
# and needs no listing step to build. Measured last-digit spread over 4937
# mutants: 8.6% to 11.3% per digit, five shards at 931/1045/1019/984/958.
#
# The shard's numbers reach the verdict by two roads: on GitHub as a job output,
# read back from VCOWS_MUTANTS_STATS_k, and on GitLab as the artifact file this
# writes either way.
#
# An environment variable rather than a flag because `workflows_carry_no_logic`
# in scripts/lint.sh fullmatches every CI command against `just [a-z][a-z-]*`,
# and `just mutants --shard 1/5` is not that. A local run sets nothing and is
# unsharded and unchanged.
#
# The mutation score is deliberately not a gate. It moves when the denominator
# moves -- deleting dead code raises it without a test being written -- so it is
# reported and never asserted on.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BASELINE="$REPO/docs/mutation-baseline.json"
STATS="$REPO/mutants/mutmut-cicd-stats.json"
SHARD_DIR="$REPO/.cache/mutation-stats"

# What a run actually checked, as a jq expression over the stats file. `total`
# counts every mutant mutmut generated, whether or not this run selected it: the
# ones it did not land in `not_checked`, which `export-cicd-stats` computes and
# then does not write. So the eight counters it does write are the only way to
# ask what a run did. In a full run the sum is `total`; across N shards the sums
# add to `total`, which is generation_floor's argument in sharded form.
CHECKED='.killed + .survived + .no_tests + .skipped + .suspicious + .timeout
         + .check_was_interrupted_by_user + .segfault'

# `mutmut run` leaves its verdict in a cache under mutants/ and prints the summary
# as a transient progress line. `export-cicd-stats` is the only machine-readable
# form, and it writes beside the cache rather than to stdout.
# `"$@"` is the shard's glob when there is one and nothing at all otherwise --
# `mutmut run` reads its positional arguments as mutant names.
run_and_export() {
    need_venv
    log "running mutmut over orchestrator/ and container/"
    "$REPO/.venv/bin/mutmut" run "$@"
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

# The summary block, printed by every path that has a full set of numbers: a
# single run, and the verdict over a sharded one.
report() {
    local total="$1" killed="$2" survived="$3" no_tests="$4" score
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
}

# The comparison against docs/mutation-baseline.json. One copy, called by the
# single-run path and by --verdict, so a sharded pipeline cannot drift into
# judging by a different rule than a developer's `just mutants` does.
#
# `hint` is how to see the survivors, and it differs by caller: a single run has
# the mutants/ tree beside it and the verdict job has only the shards' numbers.
judge() {
    local total="$1" survived="$2" no_tests="$3" hint="$4"
    local base_survived base_no_tests base_total failed=0

    [ -f "$BASELINE" ] ||
        die "no docs/mutation-baseline.json yet -- run 'scripts/mutants.sh --write-baseline' once the numbers are settled"

    base_survived="$(jq -r '.survived' "$BASELINE")"
    base_no_tests="$(jq -r '.no_tests' "$BASELINE")"
    base_total="$(jq -r '.total' "$BASELINE")"

    if [ "$survived" -gt "$base_survived" ]; then
        log "  FAIL  survived rose from $base_survived to $survived"
        log "        $(( survived - base_survived )) mutant(s) that a test executes and no test notices."
        log "        $hint"
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

# One shard: run its glob, save its numbers, judge nothing. The verdict is a
# question about all N shards at once, and no shard holds the answer.
run_shard() {
    local spec="$1" k n d digits="" glob stats total checked survived no_tests compact

    [[ "$spec" =~ ^([0-9]+)/([0-9]+)$ ]] ||
        die "VCOWS_MUTANTS_SHARD is '$spec' -- expected 'k/N', as in 1/5"
    k="${BASH_REMATCH[1]}"
    n="${BASH_REMATCH[2]}"
    # N stops at 10 because the glob selects on one decimal digit: an eleventh
    # shard would have no digit left to claim and would check nothing at all.
    { [ "$k" -ge 1 ] && [ "$k" -le "$n" ] && [ "$n" -le 10 ]; } ||
        die "VCOWS_MUTANTS_SHARD is '$spec' -- need 1 <= k <= N <= 10"

    for d in 0 1 2 3 4 5 6 7 8 9; do
        if [ $(( d % n )) -eq $(( k - 1 )) ]; then digits="$digits$d"; fi
    done
    glob="*__mutmut_*[$digits]"

    run_and_export "$glob"

    stats="$(cat "$STATS")"
    total="$(jq -r '.total' <<<"$stats")"
    survived="$(jq -r '.survived' <<<"$stats")"
    no_tests="$(jq -r '.no_tests' <<<"$stats")"
    checked="$(jq -r "$CHECKED" <<<"$stats")"

    generation_floor "$total"
    [ "$checked" -gt 0 ] ||
        die "shard $spec checked nothing -- '$glob' matched none of the $total mutants mutmut generated"
    # A shard that checked everything did not shard. Either the glob stopped
    # narrowing, or mutants/ already held an earlier run's verdicts and they are
    # counted here as this shard's -- a shard wants the cold tree CI gives it,
    # for the reason the no-cache note in .github/workflows/ci.yml gives.
    [ "$n" -eq 1 ] || [ "$checked" -lt "$total" ] ||
        die "shard $spec accounts for all $total mutants -- '$glob' narrowed nothing, or mutants/ carries an earlier run"

    mkdir -p "$SHARD_DIR"
    cp "$STATS" "$SHARD_DIR/shard-$k.json"
    # And, on GitHub, as a job output as well. Artifacts were the first design
    # and every shard's upload failed with "Artifact storage quota has been hit"
    # (job 100822624337): the quota is shared across the account, recalculated
    # only every 6 to 12 hours, and stale-full from delivery bundles already
    # deleted. Step outputs have their own 1 MB-per-job budget, which a hundred
    # bytes of counters will not fill. The file above is still written, because
    # GitLab's side of this seam is the artifact.
    if [ -n "${GITHUB_OUTPUT:-}" ]; then
        # `jq -c` on its own line, and one line of output: a step output is a
        # `name=value` pair that a newline ends.
        compact="$(jq -c . "$STATS")"
        printf 'stats-%s=%s\n' "$k" "$compact" >> "$GITHUB_OUTPUT"
    fi
    log ""
    log "  shard     $k of $n  ($glob)"
    log "  checked   $checked of $total"
    log "  survived  $survived"
    log "  no tests  $no_tests"
    log ""
    log "wrote $SHARD_DIR/shard-$k.json -- 'scripts/mutants.sh --verdict' judges the sum"
}

# Sum a sharded run and judge the sum. The shard jobs share nothing but these
# files, so this is where a sharded pipeline gets its verdict; it needs jq and
# the stats, and neither the venv nor a mutants/ tree.
verdict() {
    local dir="$1" files raw sums distinct total killed survived no_tests checked

    [ -n "$dir" ] || die "--verdict needs the directory the shard stats were collected into"

    # The GitHub side of the seam run_shard writes: the shards' numbers arrive as
    # job outputs rather than as files, because the account's artifact storage
    # quota refused the uploads (job 100822624337). Landing them in $dir first
    # means one code path judges both platforms -- GitLab's shards arrive here as
    # artifact files and set none of these.
    #
    # An unset or empty variable is skipped rather than diagnosed: a cancelled
    # matrix job leaves its output empty, and the sum check below is what has to
    # catch that, since it is also what catches a shard that ran nothing.
    local n var
    for n in 1 2 3 4 5 6 7 8 9 10; do
        var="VCOWS_MUTANTS_STATS_$n"
        if [ -n "${!var:-}" ]; then
            mkdir -p "$dir"
            printf '%s\n' "${!var}" > "$dir/shard-$n.json"
        fi
    done

    shopt -s nullglob
    files=("$dir"/*.json)
    shopt -u nullglob
    [ "${#files[@]}" -gt 0 ] ||
        die "no shard stats in $dir -- every shard's artifact is missing, and that is not a pass"

    # Assigned before it is split, so that a jq failure is this die and not a
    # silently empty array: `mapfile < <(jq ...)` takes mapfile's exit status.
    raw="$(jq -s "
        [ (map(.total) | unique | length),
          .[0].total,
          (map(.killed)   | add),
          (map(.survived) | add),
          (map(.no_tests) | add),
          (map($CHECKED)  | add) ] | .[]" "${files[@]}")" ||
        die "jq could not read the ${#files[@]} shard stats in $dir"
    mapfile -t sums <<<"$raw"
    [ "${#sums[@]}" -eq 6 ] ||
        die "could not read the ${#files[@]} shard stats in $dir as mutmut export-cicd-stats output"

    distinct="${sums[0]}"
    total="${sums[1]}"
    killed="${sums[2]}"
    survived="${sums[3]}"
    no_tests="${sums[4]}"
    checked="${sums[5]}"

    [ "$distinct" -eq 1 ] ||
        die "the ${#files[@]} shard stats in $dir disagree on how many mutants exist -- they did not all run against the same tree"
    generation_floor "$total"
    # generation_floor's argument, sharded. Every mutant belongs to exactly one
    # shard, so the checked counts add up to `total` -- unless a shard checked
    # nothing or its artifact never arrived, and both of those otherwise look
    # like a smaller, and therefore better, set of numbers.
    [ "$checked" -eq "$total" ] ||
        die "the ${#files[@]} shard stats account for $checked of $total mutants -- a shard checked nothing, or its file is missing from $dir"

    log "summed ${#files[@]} shard stats from $dir"
    report "$total" "$killed" "$survived" "$no_tests"
    judge "$total" "$survived" "$no_tests" \
        "rerun 'just mutants' locally to list them: this job has the shards' numbers and no mutants/ tree."
}

main() {
    need jq

    # Before anything that needs a venv: the verdict job installs none.
    if [ "${1:-}" = "--verdict" ]; then
        verdict "${2:-}"
        return
    fi

    local stats total killed survived no_tests
    local shard="${VCOWS_MUTANTS_SHARD:-}"

    if [ -n "$shard" ]; then
        [ "${1:-}" != "--write-baseline" ] ||
            die "--write-baseline with VCOWS_MUTANTS_SHARD set -- a baseline is a full run, and a shard has only its own part of the count"
        run_shard "$shard"
        return
    fi

    run_and_export

    stats="$(cat "$STATS")"
    total="$(jq -r '.total'    <<<"$stats")"
    killed="$(jq -r '.killed'   <<<"$stats")"
    survived="$(jq -r '.survived' <<<"$stats")"
    no_tests="$(jq -r '.no_tests' <<<"$stats")"

    generation_floor "$total"
    report "$total" "$killed" "$survived" "$no_tests"

    if [ "${1:-}" = "--write-baseline" ]; then
        jq -n --arg generated "$(now_utc || true)" \
              --argjson total "$total" --argjson killed "$killed" \
              --argjson survived "$survived" --argjson no_tests "$no_tests" \
              '{generated: $generated,
                note: "Mutation results reviewed and accepted at this tree. `survived` is a mutant some test ran and no test noticed; `no_tests` is one no test reached at all. scripts/mutants.sh fails when either rises. Lowering these is writing a test; raising one deliberately is a hand-edit with a reason in the commit body. Roughly 440 of them mutate the text of a log line or a Problem message rather than a branch -- killing those means asserting on prose, so they are a floor and not a backlog -- and the rest are equivalent mutants named in #146.",
                total: $total, killed: $killed,
                survived: $survived, no_tests: $no_tests}' > "$BASELINE"
        log "wrote $(basename "$BASELINE"): $survived survived, $no_tests with no tests"
        return
    fi

    judge "$total" "$survived" "$no_tests" \
        "'mutmut results' lists them; 'mutmut show <name>' prints the diff."
}

main "$@"
