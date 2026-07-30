"""Long-run stability monitor for the coaching pipeline.

Launches run_coach_only.py (LiveCoach + ai_core consumer + AudioManager,
real Ollama, real TTS - no TORCS needed), then keeps it under sustained
load for the whole test duration by replaying a fixture back-to-back in a
loop, while sampling the coach process's RSS memory / CPU% / thread count
on a fixed interval. At the end it plots the trend, so a slow memory leak
or CPU creep shows up as a visible slope instead of requiring you to eyeball
a CSV.

Each fixture replay drives one full run_coach_only.py session (fresh
LiveCoach + fresh ai_core consumer thread + fresh queue per session - see
run_coach_only.py's run_one_session()), so a long run with many repeats is
also a stress test of that per-session teardown: if old sessions' threads,
queues, or chroma/RAG state don't get released, memory should trend upward
run over run.

Usage:
    # Full 1-hour test, sampling every 15s (default)
    python tests/integration/stability_monitor.py

    # Quick smoke test before committing to the full hour
    python tests/integration/stability_monitor.py --duration 120 --interval 5

    # Different fixture / replay speed
    python tests/integration/stability_monitor.py --fixture many --speed 5

    # Re-plot an existing run's CSV without re-running anything
    python tests/integration/stability_monitor.py --plot-only tests/data/stability_run_20260729_120000/samples.csv
"""

import argparse
import csv
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

THIS_DIR = Path(__file__).resolve().parent          # tests/integration/
TESTS_DIR = THIS_DIR.parent                          # tests/
PROJECT_ROOT = TESTS_DIR.parent                      # repo root
SRC_DIR = PROJECT_ROOT / "src"
FIXTURES_DIR = TESTS_DIR / "fixtures"
RESULTS_ROOT = TESTS_DIR / "data" / "stability_runs"

FIXTURE_SHORTCUTS = {
    "normal": "fixture_normal_lap.csv",
    "many": "fixture_many_errors.csv",
    "final": "fixture_final_lap.csv",
}


def resolve_fixture(name):
    filename = FIXTURE_SHORTCUTS.get(name, name)
    path = FIXTURES_DIR / filename
    if not path.exists():
        raise SystemExit(
            f"Fixture not found: {path}\n"
            f"Run tests/integration/generate_fixtures.py first, or check the filename."
        )
    return path


def replay_loop(fixture_path, speed, pause, stop_event, replay_count):
    """Runs in a background thread: replay the fixture back-to-back until
    stop_event is set, so the coach process stays under sustained load for
    the whole monitoring window instead of just idling."""
    while not stop_event.is_set():
        cmd = [sys.executable, "-m", "analysis.replay", str(fixture_path.resolve()), "--speed", str(speed)]
        result = subprocess.run(cmd, cwd=str(SRC_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            print(f"[warn] replay exited with code {result.returncode}")
        replay_count[0] += 1
        stop_event.wait(pause)


def start_coach(log_path):
    """Launches run_coach_only.py as a real subprocess (not a thread) so its
    RSS/CPU can be sampled from the outside like any other long-running
    service - a thread inside this process would share our own memory."""
    log_f = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(THIS_DIR / "run_coach_only.py")],
        cwd=str(THIS_DIR),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    return proc, log_f


def sample_process(ps_proc):
    """One CPU/memory/thread-count sample. Returns None if the process died."""
    try:
        with ps_proc.oneshot():
            rss_mb = ps_proc.memory_info().rss / (1024 * 1024)
            cpu_pct = ps_proc.cpu_percent(interval=None)
            n_threads = ps_proc.num_threads()
            try:
                n_fds = ps_proc.num_fds()
            except (psutil.AccessDenied, AttributeError):
                n_fds = None
        return rss_mb, cpu_pct, n_threads, n_fds
    except psutil.NoSuchProcess:
        return None


def run_monitor(args):
    fixture_path = resolve_fixture(args.fixture)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_ROOT / f"stability_run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "samples.csv"
    coach_log_path = run_dir / "coach_stdout.log"

    print(f"Run directory : {run_dir}")
    print(f"Fixture       : {fixture_path.name}  (speed={args.speed}x, pause={args.replay_pause}s between replays)")
    print(f"Duration      : {args.duration}s ({args.duration/60:.1f} min), sampling every {args.interval}s")
    print(f"Coach stdout  : {coach_log_path}\n")

    proc, log_f = start_coach(coach_log_path)
    ps_proc = psutil.Process(proc.pid)
    ps_proc.cpu_percent(interval=None)  # prime the internal counter (first read is always 0.0)

    # Give the coach a moment to finish RAG/Ollama warm-up before we start the
    # replay loop and sampling, so the very first samples aren't dominated by
    # one-time startup cost rather than steady-state behaviour.
    print("Waiting 15s for the coach process to finish warming up (RAG + Ollama)...")
    time.sleep(15)

    replay_count = [0]
    stop_event = threading.Event()
    replay_thread = threading.Thread(
        target=replay_loop,
        args=(fixture_path, args.speed, args.replay_pause, stop_event, replay_count),
        daemon=True,
    )
    replay_thread.start()

    samples = []
    start_time = time.time()
    deadline = start_time + args.duration

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["elapsed_s", "wall_clock", "rss_mb", "cpu_percent", "num_threads", "num_fds", "replays_completed"])

            while True:
                now = time.time()
                if now >= deadline:
                    break
                if proc.poll() is not None:
                    print(f"[error] Coach process exited early (code {proc.returncode}). Stopping monitor.")
                    break

                reading = sample_process(ps_proc)
                if reading is None:
                    print("[error] Coach process disappeared. Stopping monitor.")
                    break

                rss_mb, cpu_pct, n_threads, n_fds = reading
                elapsed = now - start_time
                row = [round(elapsed, 1), datetime.now().isoformat(timespec="seconds"),
                       round(rss_mb, 1), round(cpu_pct, 1), n_threads, n_fds, replay_count[0]]
                writer.writerow(row)
                f.flush()
                samples.append((elapsed, rss_mb, cpu_pct, n_threads))

                print(f"  t={elapsed/60:5.1f}min  rss={rss_mb:7.1f}MB  cpu={cpu_pct:5.1f}%  "
                      f"threads={n_threads:3d}  replays={replay_count[0]}")

                time.sleep(max(0.0, args.interval - (time.time() - now)))
    except KeyboardInterrupt:
        print("\nInterrupted by user - stopping early.")
    finally:
        print("\nStopping replay loop and coach process...")
        stop_event.set()
        replay_thread.join(timeout=args.speed_timeout_guard)

        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            print("[warn] Coach process did not exit after SIGTERM; killing it.")
            proc.kill()
            proc.wait(timeout=5)
        log_f.close()

    print(f"\n{len(samples)} sample(s) written to {csv_path}")
    if samples:
        plot_samples(samples, run_dir, args)
    return csv_path


def plot_samples(samples, out_dir, args):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    elapsed = np.array([s[0] for s in samples]) / 60.0  # minutes
    rss = np.array([s[1] for s in samples])
    cpu = np.array([s[2] for s in samples])
    threads = np.array([s[3] for s in samples])

    # Exclude the warm-up window from the trend fit: RAG/embedding model
    # init and Ollama's first real generation call both do one-time lazy
    # allocation on the FIRST replay, which shows up as a step jump in RSS,
    # not a per-iteration leak. Fitting a line across that step massively
    # overstates the growth rate (a one-time +300MB in the first minute of a
    # 90s smoke test extrapolates to tens of GB/hour) and would call a
    # perfectly stable pipeline a "leak". Steady-state slope only comes from
    # samples after the warm-up window.
    warmup_min = args.fit_warmup_exclude / 60.0
    fit_mask = elapsed >= warmup_min
    if fit_mask.sum() < 2:
        # Not enough steady-state samples (e.g. a short smoke test) - fall
        # back to all samples and say so, rather than silently mis-fitting.
        fit_mask = np.ones_like(elapsed, dtype=bool)
        warmup_note = " (warm-up window longer than the run - fit uses all samples)"
    else:
        warmup_note = f" (excluding first {warmup_min:.1f} min warm-up)"

    if fit_mask.sum() >= 2:
        slope_mb_per_min, intercept = np.polyfit(elapsed[fit_mask], rss[fit_mask], 1)
        slope_mb_per_hour = slope_mb_per_min * 60
    else:
        slope_mb_per_min, intercept, slope_mb_per_hour = 0.0, float(rss[0]) if len(rss) else 0.0, 0.0

    fig, (ax_rss, ax_cpu, ax_threads) = plt.subplots(3, 1, figsize=(10, 9), sharex=True)

    ax_rss.plot(elapsed, rss, color="#2563eb", linewidth=1.5)
    if warmup_min > 0 and fit_mask.sum() >= 2 and not fit_mask.all():
        ax_rss.axvspan(0, warmup_min, color="#f59e0b", alpha=0.12, label="warm-up (excluded from fit)")
    if fit_mask.sum() >= 2:
        fit_x = elapsed[fit_mask]
        ax_rss.plot(fit_x, intercept + slope_mb_per_min * fit_x, color="#dc2626",
                    linestyle="--", linewidth=1, label=f"trend: {slope_mb_per_hour:+.1f} MB/hour")
    ax_rss.legend(loc="upper left", fontsize=8)
    ax_rss.set_ylabel("RSS memory (MB)")
    ax_rss.set_title(f"run_coach_only.py stability - {args.duration/60:.0f} min, "
                      f"fixture={args.fixture}, speed={args.speed}x")
    ax_rss.grid(alpha=0.3)

    ax_cpu.plot(elapsed, cpu, color="#059669", linewidth=1.2)
    ax_cpu.set_ylabel("CPU (%)")
    ax_cpu.grid(alpha=0.3)

    ax_threads.plot(elapsed, threads, color="#7c3aed", linewidth=1.2, drawstyle="steps-post")
    ax_threads.set_ylabel("Thread count")
    ax_threads.set_xlabel("Elapsed time (minutes)")
    ax_threads.grid(alpha=0.3)

    fig.tight_layout()
    png_path = out_dir / "trend.png"
    fig.savefig(png_path, dpi=130)
    plt.close(fig)

    print(f"\nPlot written to {png_path}")
    print("=" * 60)
    verdict = "STABLE" if abs(slope_mb_per_hour) < 5.0 else (
        "GROWING - possible leak" if slope_mb_per_hour >= 5.0 else "SHRINKING")
    print(f"Memory trend: {slope_mb_per_hour:+.1f} MB/hour  ->  {verdict}{warmup_note}")
    print(f"RSS range (full run): {rss.min():.1f} - {rss.max():.1f} MB")
    print(f"CPU range: {cpu.min():.1f}% - {cpu.max():.1f}%  (mean {cpu.mean():.1f}%; "
          f"can exceed 100% - psutil reports relative to one core, same as top/Activity Monitor)")
    print(f"Thread count range: {threads.min()} - {threads.max()}")
    if not fit_mask.all():
        print(f"Note: the trend fit above excludes the first {warmup_min:.1f} min "
              f"(RAG/Ollama warm-up causes a one-time RSS step, not a per-iteration leak).")
    print("=" * 60)


def plot_only(csv_path, args):
    samples = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append((float(row["elapsed_s"]) / 1.0, float(row["rss_mb"]), float(row["cpu_percent"]), int(row["num_threads"])))
    # Re-derive elapsed in seconds for plot_samples (it divides by 60 itself)
    if not samples:
        raise SystemExit(f"No samples found in {csv_path}")
    plot_samples(samples, Path(csv_path).parent, args)


def main():
    parser = argparse.ArgumentParser(description="Long-run stability monitor for run_coach_only.py")
    parser.add_argument("--duration", type=float, default=3600.0, help="Total monitoring time in seconds (default: 3600 = 1 hour)")
    parser.add_argument("--interval", type=float, default=15.0, help="Sampling interval in seconds (default: 15)")
    parser.add_argument("--fixture", default="normal", help="normal | many | final, or a filename in tests/fixtures/")
    parser.add_argument("--speed", type=float, default=5.0, help="Replay speed multiplier (default 5x, so more cycles fit in the test window)")
    parser.add_argument("--replay-pause", type=float, default=3.0, help="Seconds between back-to-back replays (default 3)")
    parser.add_argument("--speed-timeout-guard", type=float, default=60.0,
                         help="Max seconds to wait for the in-flight replay to finish when stopping (default 60)")
    parser.add_argument("--plot-only", metavar="CSV_PATH", help="Skip running anything; just re-plot an existing samples.csv")
    parser.add_argument("--fit-warmup-exclude", type=float, default=300.0, dest="fit_warmup_exclude",
                         help="Seconds of initial warm-up to exclude from the memory-trend line fit "
                              "(default 300 = 5 min; RAG/Ollama one-time init otherwise reads as a leak)")
    args = parser.parse_args()

    if args.plot_only:
        plot_only(args.plot_only, args)
        return

    run_monitor(args)


if __name__ == "__main__":
    main()
