"""Voice-queue-conflict diagnostic report (companion to fixture_many_errors.csv).

Reads the SAME tests/data/latency_log.jsonl the latency test uses, but pulls out
the audio_manager.py queue-state events added for this test:

    audio_enqueued        - every item that entered the priority queue
    audio_interrupt_purge - fires on every interrupt=True enqueue (off_track /
                            brake_now), with each purged item's age and
                            whether it was still within its 2.5s staleness
                            budget when killed (purged_count=0 if the queue
                            was already empty at that moment)
    audio_dropped_stale   - items the 2.5s staleness check dropped on its own
    voice_start           - item actually started speaking (priority + how
                            long it waited in queue)
    voice_done            - item finished speaking (how long TTS held the
                            channel)

Reconstructs a chronological timeline and prints four explicit verdicts so
you don't have to listen for a dropped line by ear:

    1. High-priority preemption  - did urgent/high items get spoken quickly?
    2. Stale-drop mechanism      - did the 2.5s check fire only for items
                                    genuinely older than 2.5s?
    3. Interrupt collateral damage - did any interrupt (off_track/brake_now)
                                    purge an item that was NOT yet stale
                                    (still_valid=True) - i.e. wrongly killed
                                    audio that would otherwise have played?
    4. In-flight speech blocking - interrupt=True / PriorityQueue ordering
                                    only ever affect items still WAITING in
                                    queue. AudioManager.stop_all() only stops
                                    pygame sound playback; _speak_text()'s
                                    subprocess.Popen(["say", ...]) + proc.wait()
                                    is a full, un-interruptible, un-timed
                                    block (SPEECH_TIMEOUT_SECONDS is defined
                                    but never actually applied to it). So a
                                    long-winded normal-priority utterance
                                    already being spoken can starve a
                                    same-or-higher-priority item that arrives
                                    mid-speech, with nothing able to preempt
                                    it - this check looks for exactly that.

Important gotcha this script has to handle: the SAME corner/tag/lap can
legitimately produce two audio events under the IDENTICAL correlation key
(e.g. turn3_poor_corner_exit fires once from the real-time snapshot pass and
again from the on-lap-finish full analysis) - a naive {key: event} dict
would silently keep only the last one and mis-pair everything before it.
Events are matched FIFO per key instead (see build_play_records()).

Usage:
    python tests/integration/analyze_audio_queue.py
    python tests/integration/analyze_audio_queue.py --log tests/data/latency_log.jsonl
"""

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOG_PATH = PROJECT_ROOT / "tests" / "data" / "latency_log.jsonl"

QUEUE_STAGES = {
    "audio_enqueued", "audio_interrupt_purge", "audio_dropped_stale",
    "voice_start", "voice_done", "queued_for_voice",
}

PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3, "slow": 4}


def _parse_detail(rec):
    detail = rec.get("detail", "")
    if isinstance(detail, str) and detail.startswith("{"):
        try:
            return json.loads(detail)
        except json.JSONDecodeError:
            pass
    return {"description": detail}


def load_events(log_path):
    events = []
    if not log_path.exists():
        print(f"No log found at {log_path}. Run the replay test first.")
        return events
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["stage"] in QUEUE_STAGES:
                rec["_detail"] = _parse_detail(rec)
                events.append(rec)
    events.sort(key=lambda r: r["ts"])
    return events


def build_play_records(events):
    """One record per actual enqueue, with its start/done/drop matched up in
    FIFO order per correlation key - NOT a {key: event} dict, which silently
    drops earlier events whenever the same key repeats (see module docstring).
    """
    pending = defaultdict(deque)   # key -> enqueued, not yet started
    in_flight = defaultdict(deque)  # key -> started, not yet done/dropped
    records = []

    for e in events:
        stage = e["stage"]
        key = e.get("key")
        d = e["_detail"]

        if stage == "audio_enqueued":
            rec = {
                "key": key, "enqueue_ts": e["ts"], "priority": d.get("priority"),
                "description": d.get("description"), "interrupt": d.get("interrupt"),
                "start_ts": None, "wait_s": None, "done_ts": None, "duration_s": None,
                "dropped": False, "drop_age_s": None,
            }
            pending[key].append(rec)
            records.append(rec)
        elif stage == "voice_start":
            rec = pending[key].popleft() if pending[key] else {
                "key": key, "enqueue_ts": None, "priority": d.get("priority"),
                "description": d.get("description"), "interrupt": None,
                "start_ts": None, "wait_s": None, "done_ts": None, "duration_s": None,
                "dropped": False, "drop_age_s": None,
            }
            if rec.get("enqueue_ts") is None and rec not in records:
                records.append(rec)
            rec["start_ts"] = e["ts"]
            rec["wait_s"] = d.get("wait_s")
            in_flight[key].append(rec)
        elif stage == "voice_done":
            if in_flight[key]:
                rec = in_flight[key].popleft()
                rec["done_ts"] = e["ts"]
                rec["duration_s"] = d.get("duration_s")
        elif stage == "audio_dropped_stale":
            if in_flight[key]:
                rec = in_flight[key].popleft()
                rec["dropped"] = True
                rec["drop_age_s"] = d.get("age_s")

    return records


def print_timeline(events):
    print("=" * 78)
    print("TIMELINE")
    print("=" * 78)
    t0 = events[0]["ts"] if events else 0.0
    for rec in events:
        t = rec["ts"] - t0
        d = rec["_detail"]
        stage = rec["stage"]
        if stage == "audio_enqueued":
            line = (f"ENQUEUE     prio={d.get('priority','?'):<6} interrupt={d.get('interrupt')!s:<5} "
                    f"qlen_before={d.get('queue_len_before')}  \"{d.get('description','')}\"  key={rec['key']}")
        elif stage == "audio_interrupt_purge":
            line = f"INTERRUPT   purged {d.get('purged_count', 0)} item(s)" + \
                   ("  (queue was already empty)" if d.get("purged_count", 0) == 0 else "")
        elif stage == "audio_dropped_stale":
            line = f"STALE_DROP  age={d.get('age_s')}s  \"{d.get('description','')}\"  key={rec['key']}"
        elif stage == "voice_start":
            line = f"VOICE_START prio={d.get('priority','?'):<6} waited={d.get('wait_s')}s  \"{d.get('description','')}\"  key={rec['key']}"
        elif stage == "voice_done":
            line = f"VOICE_DONE  duration={d.get('duration_s')}s  \"{d.get('description','')}\"  key={rec['key']}"
        else:
            line = f"{stage.upper()}  {d}"
        print(f"[t={t:7.3f}s] {line}")
        if stage == "audio_interrupt_purge":
            for item in d.get("purged", []):
                flag = "** STILL_VALID - WRONGLY KILLED **" if item["still_valid"] else "already stale, fine to drop"
                print(f"              -> \"{item['description']}\" age={item['age_s']}s  {flag}")
    print()


def check_high_priority_preemption(records):
    print("=" * 78)
    print("1. HIGH-PRIORITY PREEMPTION")
    print("=" * 78)
    high_prio = [r for r in records if r["priority"] in ("urgent", "high")]
    if not high_prio:
        print("No urgent/high-priority events were enqueued in this log - nothing to check.")
        print("(fixture_many_errors.csv's off_track injection should produce at least one 'high'")
        print(" priority enqueue; if this is empty, the injection didn't fire - check the fixture.)\n")
        return

    ok = True
    for r in high_prio:
        desc, prio = r["description"], r["priority"]
        if r["start_ts"] is None:
            print(f"  [FAIL] \"{desc}\" (prio={prio}) was enqueued but never reached voice_start.")
            ok = False
            continue
        wait_s = r["wait_s"]
        verdict = "OK" if (wait_s is not None and wait_s < 2.5) else "SLOW"
        dropped_note = "  -> then DROPPED as stale" if r["dropped"] else ""
        print(f"  [{verdict}] \"{desc}\" (prio={prio}) waited {wait_s}s before voice_start.{dropped_note}")
        if verdict != "OK" or r["dropped"]:
            ok = False
    print(f"\n  Overall: {'PASS' if ok else 'FAIL'} - high-priority items "
          f"{'were' if ok else 'were NOT all'} spoken promptly.\n")


def check_stale_drop(records):
    print("=" * 78)
    print("2. 2.5s STALE-DROP MECHANISM")
    print("=" * 78)
    drops = [r for r in records if r["dropped"]]
    if not drops:
        print("No items were dropped for staleness in this run.")
        print("(Not necessarily a problem - it only means nothing sat in queue >2.5s this time.")
        print(" To force it, enqueue several items back-to-back with the voice channel busy.)\n")
        return

    bad = [r for r in drops if (r["drop_age_s"] or 0) <= 2.5]
    for r in drops:
        age = r["drop_age_s"]
        flag = "OK (genuinely stale)" if age is not None and age > 2.5 else "FAIL (dropped before 2.5s!)"
        print(f"  age={age}s  \"{r['description']}\" (prio={r['priority']})  -> {flag}")
    print(f"\n  {len(drops)} item(s) dropped, ages {[r['drop_age_s'] for r in drops]}.")
    print(f"  Overall: {'FAIL' if bad else 'PASS'} - the 2.5s threshold "
          f"{'was violated' if bad else 'was respected'} for every drop.\n")


def check_interrupt_collateral(events):
    print("=" * 78)
    print("3. off_track/brake_now INTERRUPT COLLATERAL DAMAGE")
    print("=" * 78)
    purges = [e for e in events if e["stage"] == "audio_interrupt_purge"]
    if not purges:
        print("No interrupt=True event fired in this run at all - nothing to check.")
        print("(fixture_many_errors.csv's injected off_track excursion should trigger this at least")
        print(" once; if this is empty, check that the off_track injection is actually crossing")
        print(" fast_layer.py's OFF_TRACK_POS threshold.)\n")
        return

    total_purged = 0
    wrongly_killed = []
    empty_hits = 0
    for e in purges:
        items = e["_detail"].get("purged", [])
        if not items:
            empty_hits += 1
        for item in items:
            total_purged += 1
            if item["still_valid"]:
                wrongly_killed.append(item)

    print(f"  {len(purges)} interrupt=True event(s) fired; {empty_hits} found the queue already "
          f"empty (nothing to test collateral damage against), {total_purged} total item(s) purged "
          f"across the rest.")
    if wrongly_killed:
        print(f"\n  ** {len(wrongly_killed)} item(s) were STILL VALID (not yet stale) when an "
              f"interrupt wiped them: **")
        for item in wrongly_killed:
            print(f"     - \"{item['description']}\" (age={item['age_s']}s, budget remaining "
                  f"{2.5 - item['age_s']:.2f}s)")
        print(f"\n  Overall: FAIL - _clear_queue() purges the ENTIRE queue unconditionally on any "
              f"interrupt=True enqueue (audio_manager.py's _clear_queue()), with no check for "
              f"whether a purged item was still within its own 2.5s budget. Confirmed real "
              f"end-to-end, not just in theory.\n")
    else:
        print(f"  Overall: PASS - every purged item was already stale (age > 2.5s) when killed; "
              f"no still-valid content was collaterally dropped in this run.\n")
        print(f"  Note: this only means it didn't happen THIS run, not that it can't - "
              f"_clear_queue() has no still-valid check either way; a shorter gap between the "
              f"prior enqueue and the interrupt would still wipe it.\n")


def check_in_flight_blocking(records):
    print("=" * 78)
    print("4. IN-FLIGHT SPEECH BLOCKING (no preemption of already-playing audio)")
    print("=" * 78)

    # Speaking intervals: only records that actually got a start AND a done
    # (i.e. really occupied the channel for a measurable span).
    intervals = [r for r in records if r["start_ts"] is not None and r["done_ts"] is not None]

    # A record landing during another's speaking interval is only a PROBLEM
    # if it actually asked to preempt (interrupt=True) and still had to wait
    # a non-trivial amount of time anyway. A non-interrupt item landing
    # mid-speech and queueing normally is expected, correct behaviour - it
    # never asked to jump the in-flight audio. And an interrupt=True item
    # that lands mid-speech but still gets voice_start almost immediately
    # (wait_s small) means the in-flight speech WAS cut short for it - i.e.
    # preemption actually worked, not a failure.
    PREEMPT_OK_THRESHOLD_S = 1.0

    findings = []
    for r in records:
        if r["enqueue_ts"] is None:
            continue
        for other in intervals:
            if other is r:
                continue
            if other["start_ts"] <= r["enqueue_ts"] < other["done_ts"]:
                requested_preemption = bool(r.get("interrupt"))
                preempted_ok = requested_preemption and r["wait_s"] is not None and r["wait_s"] < PREEMPT_OK_THRESHOLD_S
                findings.append({
                    "desc": r["description"], "prio": r["priority"],
                    "blocking_desc": other["description"], "blocking_prio": other["priority"],
                    "remaining_speech_s": round(other["done_ts"] - r["enqueue_ts"], 3),
                    "wait_s": r["wait_s"],
                    "outcome": "DROPPED (stale)" if r["dropped"] else (
                        f"eventually played, waited {r['wait_s']}s" if r["wait_s"] is not None else "unresolved"),
                    "requested_preemption": requested_preemption,
                    "preempted_ok": preempted_ok,
                    "notable": requested_preemption and not preempted_ok,
                })

    if not findings:
        print("No enqueue landed while something else was actively speaking in this run - "
              "nothing to check for in-flight blocking.\n")
        return

    notable = [f for f in findings if f["notable"]]
    for f in findings:
        if f["preempted_ok"]:
            flag = "** PREEMPTED SUCCESSFULLY - in-flight speech was cut short for it **"
        elif f["requested_preemption"]:
            flag = "** NOT PREEMPTED - interrupt=True but still had to wait **"
        else:
            flag = "(no interrupt requested - waiting is expected)"
        print(f"  \"{f['desc']}\" (prio={f['prio']}) arrived while \"{f['blocking_desc']}\" "
              f"(prio={f['blocking_prio']}) was still speaking, {f['remaining_speech_s']}s left "
              f"to go -> {f['outcome']}  {flag}")

    print()
    if notable:
        print(f"  Overall: FAIL - {len(notable)} interrupt=True item(s) arrived while a "
              f"lower-or-equal-priority utterance was ALREADY PLAYING and could not preempt it "
              f"within {PREEMPT_OK_THRESHOLD_S}s. interrupt=True and PriorityQueue ordering only "
              f"govern items still waiting in queue; AudioManager.stop_all() only stops pygame "
              f"sounds, and _speak_text()'s subprocess.Popen(['say', ...]) + proc.wait() has no "
              f"timeout despite SPEECH_TIMEOUT_SECONDS being defined - a long utterance fully "
              f"occupies the channel until it finishes speaking, unconditionally.\n")
    else:
        preempted = [f for f in findings if f["preempted_ok"]]
        if preempted:
            print(f"  Overall: PASS - {len(preempted)} interrupt=True item(s) arrived mid-speech "
                  f"and successfully cut the in-flight audio short instead of waiting for it.\n")
        else:
            print("  Overall: PASS for this run - no interrupt=True item happened to arrive "
                  "mid-speech, so preemption itself was never exercised this run.\n")


def main():
    parser = argparse.ArgumentParser(description="Diagnose voice-queue conflict behaviour from the latency log.")
    parser.add_argument("--log", default=str(DEFAULT_LOG_PATH), help="Path to latency_log.jsonl")
    args = parser.parse_args()

    events = load_events(Path(args.log))
    if not events:
        return
    records = build_play_records(events)

    print_timeline(events)
    check_high_priority_preemption(records)
    check_stale_drop(records)
    check_interrupt_collateral(events)
    check_in_flight_blocking(records)


if __name__ == "__main__":
    main()
