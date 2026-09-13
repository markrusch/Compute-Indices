# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""L4.2: MLPerf results joined to TCI prices, and the joins that must never happen.

The dangerous failure here is not a crash. It is a plausible number: an H200 time to train
multiplied by a B200 price, printed against a named company, with nothing about it looking
wrong. The first version of the fetcher did exactly that, so most of what follows is about
the join rather than the arithmetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tci import mlperf
from tests.conftest import insert_run

FIX = Path(__file__).parent / "fixtures" / "mlperf"
SNAPSHOT = Path(__file__).resolve().parents[1] / "data" / "mlperf" / "training.json"


def _system(**kw) -> mlperf.System:
    base = dict(
        repo="training_results_v5.0", commit="abc123", submitter="Oracle",
        system_key="8xBM.GPU.H200.8", system_name="BM.GPU.H200.8",
        accelerator_model="NVIDIA H200-SXM5-141GB", nodes=8, accelerators_per_node=8,
        status="Available cloud", accelerator_interconnect="", host_networking="",
    )
    base.update(kw)
    return mlperf.System(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the log reader

def test_time_to_train_is_run_stop_minus_run_start() -> None:
    minutes = mlperf.time_to_train_minutes((FIX / "run_success.txt").read_text())
    assert minutes == pytest.approx(10.0)


def test_a_run_that_did_not_converge_is_not_a_time_to_train() -> None:
    """Averaging a failed run in as a slow one would make every median a little wrong and
    nothing would look broken."""
    assert mlperf.time_to_train_minutes((FIX / "run_aborted.txt").read_text()) is None


def test_a_truncated_log_yields_nothing_rather_than_a_guess() -> None:
    assert mlperf.time_to_train_minutes((FIX / "run_truncated.txt").read_text()) is None


def test_non_mllog_lines_are_ignored() -> None:
    text = "build output\n" + (FIX / "run_success.txt").read_text() + "\nTraceback\n"
    assert mlperf.time_to_train_minutes(text) == pytest.approx(10.0)


# ------------------------------------------------------------------ the join

def test_a_results_directory_matches_only_its_own_system() -> None:
    """The bug this replaced: the matcher fell back to node count when the name did not
    match, so Oracle's `8xBM.GPU.H200.8` and `8xBM.GPU.B200.8` each claimed the other's
    logs. That multiplied an H200 time to train by a B200 price and printed it."""
    h200 = _system(system_key="8xBM.GPU.H200.8")
    b200 = _system(system_key="8xBM.GPU.B200.8",
                   accelerator_model="NVIDIA Blackwell GPU (B200-SXM-180GB)")
    assert mlperf._same_system("8xBM.GPU.H200.8", h200)
    assert not mlperf._same_system("8xBM.GPU.H200.8", b200)
    assert not mlperf._same_system("64xBM.GPU.H200.8", h200)


def test_a_framework_suffix_on_the_directory_still_matches() -> None:
    """Nebius names the directory after the system file plus its framework tag."""
    system = _system(submitter="Nebius", system_key="nebius_soperator_8xH200_n128")
    assert mlperf._same_system("nebius_soperator_8xH200_n128_ngc25.01_nemo", system)
    assert not mlperf._same_system("nebius_soperator_8xH200_n64_ngc25.01_nemo", system)


def test_the_snapshot_join_key_is_unique_within_a_round() -> None:
    """`system_name` is not a key: Oracle files four node counts under BM.GPU.GB300.4, and
    joining results on the name merged an 8-GPU run with a 512-GPU one."""
    snapshot = mlperf.load(SNAPSHOT)
    keys = [(s.repo, s.system_key) for s in snapshot.systems]
    assert len(keys) == len(set(keys))


def test_every_result_resolves_to_exactly_one_system() -> None:
    snapshot = mlperf.load(SNAPSHOT)
    systems = {(s.repo, s.system_key) for s in snapshot.systems}
    for result in snapshot.results:
        assert (result.repo, result.system_key) in systems, result


def test_an_accelerator_tci_does_not_price_is_left_unpriced(conn) -> None:
    """Never matched to something adjacent: an MI300X run is not an H200 run."""
    system = _system(accelerator_model="AMD Instinct MI300X 192GB HBM3")
    assert system.tci_model is None


# ------------------------------------------------------------------ comparability

def _row(submitter: str, repo: str, acc: str, bench: str, n_acc: int,
         minutes: float, price: float | None) -> mlperf.Row:
    system = _system(submitter=submitter, repo=repo, accelerator_model=acc,
                     nodes=n_acc // 8, accelerators_per_node=8,
                     system_key=f"{submitter}-{n_acc}")
    result = mlperf.Result(repo=repo, commit="abc123", submitter=submitter,
                           system_key=system.system_key, benchmark=bench,
                           runs=(minutes,), logs=("x.txt",))
    return mlperf.Row(system, result, price, "2026-09-12", ("DE",) if price else ())


B200 = "NVIDIA Blackwell GPU (B200-SXM-180GB)"


def test_two_sellers_on_the_same_thing_are_comparable() -> None:
    rows = [_row("Lambda", "training_results_v5.0", B200, "llama2_70b_lora", 8, 10.9, 6.79),
            _row("Oracle", "training_results_v5.0", B200, "llama2_70b_lora", 8, 11.0, 14.0)]
    cells = mlperf.comparable_cells(rows)
    assert len(cells) == 1
    assert [r.system.submitter for r in next(iter(cells.values()))] == ["Lambda", "Oracle"]
    assert len(mlperf.priced_comparisons(rows)) == 1


def test_different_rounds_are_not_comparable() -> None:
    """MLPerf revises its suite between rounds, so the same benchmark name in v5.1 and
    v6.0 is not the same workload. Comparing them fills the table and empties it of
    meaning."""
    rows = [_row("Lambda", "training_results_v5.1", B200, "llama31_8b", 8, 83.8, 6.79),
            _row("Oracle", "training_results_v6.0", B200, "llama31_8b", 8, 84.1, 14.0)]
    assert mlperf.comparable_cells(rows) == {}


def test_different_scales_are_not_comparable() -> None:
    """Time to train is not linear in accelerator count."""
    rows = [_row("Lambda", "training_results_v5.0", B200, "llama31_8b", 8, 83.8, 6.79),
            _row("Oracle", "training_results_v5.0", B200, "llama31_8b", 64, 18.7, 14.0)]
    assert mlperf.comparable_cells(rows) == {}


def test_different_accelerators_are_not_comparable() -> None:
    rows = [_row("Lambda", "training_results_v5.0", B200, "llama31_8b", 8, 83.8, 6.79),
            _row("Oracle", "training_results_v5.0", "NVIDIA H200-SXM5-141GB",
                 "llama31_8b", 8, 84.1, 14.0)]
    assert mlperf.comparable_cells(rows) == {}


def test_a_cell_with_an_unpriced_seller_is_comparable_but_not_priced() -> None:
    rows = [_row("Nebius", "training_results_v5.1", B200, "llama31_8b", 8, 84.6, None),
            _row("Oracle", "training_results_v5.1", B200, "llama31_8b", 8, 84.1, 14.0)]
    assert len(mlperf.comparable_cells(rows)) == 1
    assert mlperf.priced_comparisons(rows) == {}


def test_run_cost_is_gpu_hours_times_the_price() -> None:
    row = _row("Lambda", "training_results_v5.0", B200, "llama2_70b_lora", 8, 60.0, 6.79)
    assert row.gpu_hours == pytest.approx(8.0)
    assert row.cost_usd == pytest.approx(8.0 * 6.79)


def test_a_row_without_a_price_has_no_cost() -> None:
    row = _row("Nebius", "training_results_v5.1", B200, "llama31_8b", 8, 84.6, None)
    assert row.cost_usd is None


# ------------------------------------------------------------------ the record

def test_the_snapshot_pins_an_upstream_commit_per_round() -> None:
    """Without the pin, a figure here cites a moving target."""
    snapshot = mlperf.load(SNAPSHOT)
    assert snapshot.repos
    for repo, commit in snapshot.repos.items():
        assert repo.startswith("training_results_v")
        assert len(commit) == 40, f"{repo}: {commit!r} is not a commit sha"
    for system in snapshot.systems:
        assert system.commit == snapshot.repos[system.repo]


def test_providers_with_no_submission_are_named_not_estimated() -> None:
    absent = mlperf.missing_submitters(["vast.ai", "runpod", "nebius"], mlperf.load(SNAPSHOT))
    assert "vast.ai" in absent and "runpod" in absent
    assert "nebius" not in absent


def test_no_panel_provider_has_submitted_an_h100_system() -> None:
    """The headline's GPU. If this ever fails, the roadmap's L4.2 finding has changed and
    the page that states it has to be rewritten before the test is."""
    snapshot = mlperf.load(SNAPSHOT)
    h100 = [s for s in snapshot.systems if "H100" in s.accelerator_model]
    assert h100 == [], f"an H100 submission now exists: {h100}"


def test_the_table_reads_prices_from_the_database(conn) -> None:
    insert_run(conn, "r1", "2026-09-12", "gpuhunt")
    for price, country in ((4.0, "FI"), (5.0, "FI"), (99.0, "US")):
        conn.execute(
            "INSERT INTO observations (run_id, ts_utc, source, provider, gpu_model,"
            " gpu_count, price_usd_per_gpu_hr, region, country, interconnect, tier, term,"
            " raw_json) VALUES ('r1', '2026-09-12T11:00:00Z', 'gpuhunt', 'nebius',"
            " 'H200_SXM', 8, ?, 'eu', ?, NULL, 'list', 'on_demand', '{}')",
            (price, country),
        )
    conn.commit()
    price, where = mlperf._eu_price(conn, "nebius", "H200_SXM", "2026-09-12",
                                    frozenset({"FI", "FR"}))
    # The US row is outside the block and must not reach the median.
    assert price == pytest.approx(4.5)
    assert where == ("FI",)
