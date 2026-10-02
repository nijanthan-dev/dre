# Issue 74 uncached Ubuntu comparison

Run: https://github.com/nijanthan-dev/dre/actions/runs/37014716840

Baseline: `8970f305d3ce5be284bdf69fc1c708ca279a18c1`. Implementation: `f5c642c41fc944f4f9c451d579dca2346b37abde`.

Three jobs ran in parallel. Each job executed both variants on the same Ubuntu VM with separate fresh Cargo, Go, and target directories. Pair 2 ran implementation first; pairs 1 and 3 ran baseline first. Rust 1.99.0, Cargo 1.99.0, Go 1.27.1, Ubuntu 24.04 image 20260927.320.1, four vCPUs, four Cargo build jobs, and CARGO_INCREMENTAL=0 matched across all pairs. CPU and memory matched within each pair. Pair 1 used AMD EPYC 9V74; pairs 2 and 3 used AMD EPYC 7763.

The command sequence mirrored the repository CI at the pinned commits: go test ./..., Go plugin build, cargo build --workspace --bins, and cargo test --workspace --no-fail-fast. Runtime tests used DRE_TEST_PREBUILT_BINS=1 and the built Databricks binary. No Rust or Go caches were restored. Every command completed successfully in every sample.

Peak target usage was sampled with du every two seconds across the command sequence. Final target and debug/deps sizes were recorded before deleting only the exact disposable target created for that sample. Registry/Go caches remained separate between variants; retained first-variant caches slightly reduced the second variant's available disk, but every sample had ample capacity. Initial free space was approximately 82 to 85 GiB. Peak figures exclude those caches and represent the target directory.

| Pair | Variant | Cargo build seconds | Cargo test seconds | Peak target GiB | Final target GiB | debug/deps GiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | baseline | 652.601 | 442.288 | 38.876 | 38.876 | 32.751 |
| 1 | implementation | 528.247 | 374.285 | 18.449 | 18.449 | 15.673 |
| 2 | baseline | 674.192 | 443.516 | 38.876 | 38.876 | 32.751 |
| 2 | implementation | 547.321 | 346.081 | 18.449 | 18.449 | 15.673 |
| 3 | baseline | 685.073 | 449.685 | 38.876 | 38.876 | 32.751 |
| 3 | implementation | 570.031 | 350.218 | 18.449 | 18.449 | 15.673 |

Median test time: baseline 443.516s, implementation 350.218s. Improvement: 21.04%.

Hardware-matched per-pair improvements: 15.38%, 21.97%, 22.12%. Median paired improvement: 21.97%.

Baseline test range: 442.288 to 449.685s. Implementation range: 346.081 to 374.285s. The 25% timing target was not met. Hardware matching removes the CPU-model confound; the remaining run-to-run spread is reported rather than used to claim a passing result.

Median peak target reduction: 52.54%. This exceeds 50% against the measured CI-sequence baseline. The implementation's 18.449 GiB does not meet the historical 13.5 GiB absolute threshold.

These CI-sequence disk figures include a preceding production binary build. They are separate from the earlier clean cargo test --workspace --no-run --locked measurements: failed upstream baseline 28.699 GiB, helper-only 19.293 GiB, final 14.025 GiB. The latter also missed the historical 13.5 GiB threshold by 0.525 GiB.

## Representative binary linkage

| Executable | Baseline MiB | Implementation MiB | DuckDB symbols after |
| --- | ---: | ---: | --- |
| run_xlsx_formats | 725.4 | 67.5 | Absent |
| output | 686.6 | 28.2 | Absent |
| templates_profiles | 686.0 | 26.6 | Absent |

nm -C completed successfully for all recorded binaries. All three representative ordinary CLI test executables contain DuckDB engine/FFI symbols before the change and none after it. Earlier local inspection also checked all 29 ordinary CLI integration executables, with none containing libduckdb_sys or duckdb_open after the change. The private helper remains intentionally linked.

## Other runs and compatibility

The first attempt was rejected by GitHub YAML validation before any build because runner.temp was used at job-env scope. That was corrected.

Six initial fully parallel uncached samples all passed, but GitHub assigned different CPU models. Their comparison validation rejected the hardware mismatch. Those measurements are retained as exploratory evidence; the three same-VM pairs above are the final controlled comparison. No failing benchmark was silently discarded.

Upstream advanced during measurement. Fork master and the implementation worktree/branch were updated to upstream 5f57dae77b9aa66ca1f9175983de8ca2b76ba30a. PR #81 head 1392cc5d3a0a3e1c4f4444a65f0bacef82350d80 completed fresh Actionbook Rust, Principal Engineer Architect, and Code Simplifier reviews with no blocking findings. Their cache-prefix, test-name, and durable-documentation refinements were applied. An initial Windows job failed while the source-transition test downloaded a full compiled fixture from its localhost server. That test never executes the payload, while another test retains the real fixture download and execution coverage. It now uses a small stand-in and preserves the GitHub-to-local-to-GitHub lockfile assertions. The fresh Ubuntu, macOS, Windows, lint, service integration, schema docs, plugin versions, and Skills jobs all passed. @allenhori approved an earlier head, but GitHub dismissed that approval after the review commits; final maintainer re-review remains required. The benchmark commits stayed pinned to isolate the issue change. Later upstream changes to CI cache policy, line-tables-only debug settings, and plugin-manager optimization are not part of these measurements, so the numbers are not a benchmark of the newly merged head against current upstream.

PR CI: https://github.com/get-dre/dre/actions/runs/37031693509
PR Skills: https://github.com/get-dre/dre/actions/runs/37031693730

The targeted libduckdb-sys debug override also enables native NDEBUG in development/tests. Release behavior remains unchanged. This tradeoff is documented and was accepted in maintainer review.

Acceptance status: helper boundary, linkage removal, preserved tests, and fresh-baseline relative disk reduction verified. The 25% timing target and historical absolute disk threshold remain unmet. No merge or release was performed.
