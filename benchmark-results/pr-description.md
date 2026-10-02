## What this changes

Closes #74. CLI integration tests previously seeded databases in-process through the
bundled `duckdb` dev dependency. The shared test module caused the native DuckDB
archive to appear in ordinary test executables, including targets that did not need
it. A clean upstream build exhausted this 32 GB runner while linking CLI tests.

Seeding now lives in the unpublished `tests/duckdb-seed` workspace package. No
production package depends on it; default workspace members and explicit release
builds continue selecting production packages. `TestProject::duckdb(path, sql)`
retains its API and all existing call sites and assertions.

The caller passes a native database-path argument and file-backed stdin for SQL,
without shell quoting or pipe deadlocks. Failures include helper path, database
path, SQL, exit status, stderr, and stdout. Each invocation seeds a unique sibling
staging database, closes it, and renames it only after success. Existing databases
are copied first; a SQL failure cannot publish partially seeded state, even after
an explicit COMMIT. Databases with an existing WAL are rejected rather than copied
incompletely. TestProject's isolated temporary directories remain unchanged.

After measuring the helper-only change, a targeted development/test debug-info
override for `libduckdb-sys` was added with contributor approval. This keeps other
Rust packages and the release profile unchanged. The upstream native build script
also defines NDEBUG when debug info is disabled, so native DuckDB debug assertions
use release behavior in development/tests. No integration-test assertions or cases
were removed or weakened.

### Measurements

Same Linux cloud runner, Rust/Cargo 1.99.0, four build jobs, and
`cargo test --workspace --no-run --locked`. Disposable target directories started
empty and were measured sequentially with `du` every two seconds. The first two
rows use the original default test configuration; the final row additionally
includes the documented `libduckdb-sys` package debug-info override.

| Build | Wall time | Sampled peak target disk | Final target | debug/deps | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| Unmodified upstream | 8m06s | 28.699 GiB | 28.699 GiB | 23.652 GiB | Failed while linking at disk limit |
| Helper only | 7m46s | 19.293 GiB | 19.293 GiB | 14.183 GiB | Passed |
| Helper plus native debug override | 6m22s | 14.025 GiB | 14.025 GiB | 11.872 GiB | Passed |

| Representative executable | Upstream | Final | DuckDB engine/FFI symbols after |
| --- | ---: | ---: | --- |
| run_xlsx_formats | 728.2 MiB | 70.0 MiB | Absent |
| output | 687.2 MiB | 28.4 MiB | Absent |
| templates_profiles | Not produced before baseline failure | 26.7 MiB | Absent |
| dre-test-duckdb-seed | Not present | 234.0 MiB | Present, intentionally |

`nm -C` inspected all 29 CLI integration-test executables: none contain
`libduckdb_sys` or `duckdb_open` symbols. The dedicated helper contains both.
`cargo tree -p dre-cli --edges normal,dev --locked` contains no DuckDB dependency.
The production-only CLI dependency graph is identical before and after.

The final build reduces peak target disk by **51.1% against the fresh upstream
baseline**. Against the issue's historical 27 GiB figure the reduction is **48.1%**,
so the historical 13.5 GiB absolute threshold is missed by **0.525 GiB**. This is an
explicit acceptance limitation, not a claim that every historical threshold passed.

The original local baseline duration is time to failure. A separate Ubuntu benchmark now completed three hardware-matched uncached pairs successfully. Median cargo test time fell from 443.516s to 350.218s, a 21.04% improvement, below the requested 25%. Per-pair gains were 15.38%, 21.97%, and 22.12%; the median paired gain was 21.97%. Baseline test times ranged from 442.288 to 449.685s; implementation times ranged from 346.081 to 374.285s.

Median peak target usage across the CI build/test sequence fell from 38.876 to 18.449 GiB, a 52.54% reduction against that measured baseline. The historical 13.5 GiB absolute threshold remains unmet. These measurements include the preceding binary build and are separate from the clean no-run table above.

Each pair ran both pinned commits on the same Ubuntu 24.04 VM with Rust 1.99.0, Go 1.27.1, four Cargo build jobs, CARGO_INCREMENTAL=0, separate fresh caches and targets, and no cache restoration. The three pairs ran in parallel, with implementation first in pair 2. All Go tests/builds and Rust builds/tests passed. An initial six-job run also passed all commands, but different CPU models invalidated its strict hardware comparison; it was replaced by the matched pairs rather than silently treated as comparable.

Benchmark run: https://github.com/nijanthan-dev/dre/actions/runs/37014716840
Full measurements and methodology: https://github.com/nijanthan-dev/dre/blob/bench/74-uncached-comparison/benchmark-results/issue-74.md

Later upstream CI cache/debug changes were merged into the PR after choosing the benchmark SHAs. The benchmark remains pinned to the pre-merge commits to isolate this change, and does not claim a current-head comparison.

An initial native-debug measurement was invalidated by the disk monitor racing
Cargo temporary-file deletion; the monitor was corrected and the reported final
measurement restarted from an empty target directory.

Strict Clippy took 5m30s on its first post-build attempt and found one helper test
documentation warning. After correcting it, the incremental strict check passed
in 0.36s. Baseline Clippy was not attempted after baseline disk exhaustion; this
is not a Clippy speedup comparison.

Only the exact task-created disposable target directories were deleted, after
recording measurements and verification. No unrelated caches or worktrees were
deleted. PR #79 and its worktree were untouched.

## How it was tested

Added six helper subprocess tests covering successful/repeated seeding, quoted SQL,
spaces and Unicode in paths, committed statements followed by invalid SQL, unchanged
existing databases after failure, relative paths, malformed invocations, WAL refusal,
and eight parallel isolated databases. Added three CLI helper tests covering the
TestProject API, useful exit/stderr/SQL diagnostics, missing executables, and helper
open failure. Existing integration files, assertions, and coverage are retained.

Verification used `CARGO_TARGET_DIR=/workspace/scratch/dre-74-implementation-target`
and `CARGO_BUILD_JOBS=4`. Runtime tests used the repository's documented
`DRE_TEST_PREBUILT_BINS=1` after the workspace binary build. Go protocol tests used
`DRE_TEST_GO_PLUGINS=/workspace/scratch/dre-74-go-bins` with a built Go 1.27.1 binary.

- `cargo fmt --all -- --check`: passed.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`: passed after fixing the documentation warning.
- `cargo build --workspace --bins --locked`: passed, 31.0s.
- `cargo test --workspace --locked`: passed, 463 reported passing tests, no failures or ignored tests, 3m31s.
- Direct affected CLI suites: passed, 155 tests in 19 suites, 56.1s. Exact command:

```sh
cargo test -p dre-cli --locked --test compile --test duckdb_seed --test lookups --test manifest --test output --test packages --test run_delivery --test run_destinations --test run_execution --test run_formats --test run_rendering --test run_sets --test run_tracer --test run_xlsx_formats --test run_xlsx_formulas --test schedules --test target_path --test templates_profiles --test validate_live
```

- `cargo test --workspace --test seeding --locked`: passed, all six helper process tests including failure and parallelism cases, 4m28s including its selected-target build.
- `git diff --check`: passed.
- `go build`: passed for the Databricks binary.
- `go vet ./...`: passed.
- `go test ./...`: failed locally in unchanged `TestAnUnreachableWorkspaceFailsAtOnce`. Earlier tests cache the runner's proxy configuration before that test clears HTTPS_PROXY. The same test passed in isolation with `go test -count=1 -run '^TestAnUnreachableWorkspaceFailsAtOnce$' ./...`. Go files are unchanged; the normal CI runner should verify the full Go command.

Local service-backed tests retain their existing environment gates. Real warehouse
credentials and external emulator services were not configured locally; GitHub's
existing integration job and OS matrix remain required validation.

Independent Actionbook Rust, Principal Engineer Architect, and Code Simplifier
reviews completed with no remaining blocking findings. The profile's NDEBUG
consequence and existing local plugin-build fallback were explicitly reviewed.

### Risks and compatibility

The helper requires exclusive access to its database while seeding and rejects a
WAL; existing TestProject usage already owns isolated, closed databases. SQL side
effects outside the database are outside the staged-publication guarantee. On
Windows a destination held open by another process can prevent replacement; the
helper reports failure instead of deleting the destination. Paths use native OS
arguments; SQL stays on stdin. Without DRE_TEST_PREBUILT_BINS, the established local
plugin-builds fallback can compile native artifacts in another target directory;
CI's prebuilt path avoids that duplication. Production dependency paths, SQL APIs,
plugin protocol, package versions, and release profile are unchanged.

Upstream and fork default branches are named master, not main. This PR targets
get-dre/dre:master from nijanthan-dev:fix/74-duckdb-test-helper, originally based on
8970f305d3ce5be284bdf69fc1c708ca279a18c1 and now merged with upstream
c9d9f3795e169adf858b05279c5125898809951f. Fork/master matches that latest upstream
SHA. Current task head is bac330813a6745c9c6300c4c1d8b29241bf37690. Maintainer @allenhori approved the helper boundary. The recorded acceptance
limitations remain explicit. All CI jobs reached success on PR #81 at head bac330813a6745c9c6300c4c1d8b29241bf37690: Ubuntu, macOS, Windows, strict lint, service integration, schema docs, plugin versions, Skills, and CLA. CI run: https://github.com/get-dre/dre/actions/runs/37015614019. Skills run: https://github.com/get-dre/dre/actions/runs/37015614020. @allenhori approved the PR. No blocking review threads remain.

## Checklist

- [x] One focused change, discussed in upstream issue #74
- [x] Tests added without merging or weakening existing suites
- [x] Formatting and strict workspace Clippy pass with the commands above
- [x] Workspace tests pass with the documented prebuilt-binary path
- [x] Developer documentation and measurements added in tests/duckdb-seed/README.md; no end-user behavior changed
- [x] Plugin version bump: n/a, no plugin source or release behavior changed
- [x] Existing CLA signature confirmed by the upstream CLA check; no new signature made by this task

Latest merged-head Ubuntu CI reported 473 passing tests, zero failures, and zero ignored tests. Full PR CI passed on Ubuntu, macOS, and Windows. The synced fork master CI also passed.
