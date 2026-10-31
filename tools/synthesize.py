#!/usr/bin/env python3
"""script.json から VOICEVOX で音声と timeline.json を作る。

使い方:
  python3 tools/synthesize.py plays/<id>/script.json [--engine http://localhost:50021 ...]

- 台詞は、その役の声のスタイル（roles[].voice.styles）すべてで合成する。ページで台詞ごとに声を選び直せるようにするため。
  どのスタイルで鳴らすかの初期値は event の "style"、無ければ voice.emotion_styles[emotion]、無ければ emotion と同じ名前のスタイル、無ければ最初のスタイル。
- 出力: plays/<id>/audio/<役>_<style id>.mp3（その役・スタイルの全台詞を無音をはさんでつないだもの）、narrator.mp3（ト書き）と plays/<id>/timeline.json。
  ファイルの数を少なくするため（Web の公開先にはファイル数の上限がある）。各台詞の位置は timeline.json の variants[スタイル].offset_ms。
- 合成した台詞は plays/<id>/.cache/ に置き、同じ文・同じ声なら作り直さない（script.json を直して再実行すると、変わった台詞だけ作る）。
- --engine を複数渡すと、並べて合成する（ENGINE を複数起動したとき）。
- 台詞の中の「……」「――」で台詞を分け、間に 600 ms の無音を入れる。
- script.json に "kana": "historical" があれば、旧仮名を tools/yomi.py で読みにしてから合成する（event の "read" が優先）。
- 口パク用に、モーラごとの母音の時刻（mouth: [[ms, "a"], ...]）を timeline.json に入れる。
"""
import argparse, concurrent.futures, hashlib, io, json, re, subprocess, sys, threading, wave
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from yomi import spoken_text  # noqa: E402

INNER_PAUSE_MS = 600
BANK_GAP_MS = 400  # つないだ音声の台詞と台詞の間の無音（再生の止め遅れを吸収する）
SPLIT_RE = re.compile(r"(?:…{2,}|―{2,}|─{2,}|-{2,})")
VOWELS = {"a": "a", "i": "i", "u": "u", "e": "e", "o": "o", "A": "a", "I": "i", "U": "u", "E": "e", "O": "o", "N": "n", "cl": "n"}


def role_of(script, e):
    return next(r for r in script["roles"] if r["id"] == e["role"])


def style_choices(script, e):
    """{スタイル名: style id} と初期のスタイル名"""
    if e["type"] != "line":
        v = script.get("narrator", {}).get("voice", {})
        return {"": v["speaker"]}, "", v
    v = role_of(script, e).get("voice", {})
    if "speaker" not in v:
        sys.exit(f"声が決まっていません: {e['role']}（script.json の voice.speaker を入れる）")
    styles = v.get("styles") or {"": v["speaker"]}
    emo = e.get("emotion", "calm")
    default = e.get("style") or v.get("emotion_styles", {}).get(emo) or (emo if emo in styles else next(iter(styles)))
    if default not in styles:
        sys.exit(f"スタイルがありません: {default}（{e['role']} の voice.styles にある名前を入れる）")
    return styles, default, v


def synth_segment(engine, text, sid, v):
    q = requests.post(f"{engine}/audio_query", params={"text": text, "speaker": sid}, timeout=120)
    q.raise_for_status()
    q = q.json()
    for k in ("speedScale", "pitchScale", "intonationScale", "volumeScale"):
        if k in v:
            q[k] = v[k]
    q["prePhonemeLength"], q["postPhonemeLength"] = 0.05, 0.05
    w = requests.post(f"{engine}/synthesis", params={"speaker": sid}, json=q, timeout=300)
    w.raise_for_status()
    with wave.open(io.BytesIO(w.content)) as wf:
        rate, frames = wf.getframerate(), wf.readframes(wf.getnframes())
        params = wf.getparams()
    dur = len(frames) / (params.sampwidth * params.nchannels) / rate
    # モーラの時刻（音声の長さに合わせて伸縮）
    marks, t = [], q["prePhonemeLength"]
    for ap in q["accent_phrases"]:
        for m in ap["moras"]:
            t += m.get("consonant_length") or 0
            marks.append([t, VOWELS.get(m["vowel"], "n")])
            t += m["vowel_length"]
        if ap.get("pause_mora"):
            marks.append([t, "-"])
            t += ap["pause_mora"]["vowel_length"]
    t += q["postPhonemeLength"]
    scale = dur / t if t else 1
    marks = [[round(x * scale * 1000), vw] for x, vw in marks]
    return frames, params, dur, marks


def synth_event(engine, text, sid, v, cache_wav):
    parts = [p for p in SPLIT_RE.split(text) if p.strip(" 　。、")]
    if not parts:
        return None
    pcm, params, offset, mouth = b"", None, 0.0, []
    for i, p in enumerate(parts):
        frames, params, dur, marks = synth_segment(engine, p, sid, v)
        if i:
            gap = int(params.framerate * INNER_PAUSE_MS / 1000) * params.sampwidth * params.nchannels
            pcm += b"\0" * gap
            offset += INNER_PAUSE_MS / 1000
        mouth += [[m + round(offset * 1000), vw] for m, vw in marks]
        mouth.append([round((offset + dur) * 1000), "-"])
        pcm += frames
        offset += dur
    with wave.open(str(cache_wav), "wb") as wf:
        wf.setparams(params)
        wf.writeframes(pcm)
    return {"dur_ms": round(offset * 1000), "mouth": mouth}


def write_bank(path, wavs):
    """台詞の wav を無音をはさんでつなぎ、mp3 にする。各台詞の開始位置（ms）を返す"""
    pcm, params, offsets = b"", None, []
    for w in wavs:
        with wave.open(str(w)) as wf:
            if params is None:
                params = wf.getparams()
            frames = wf.readframes(wf.getnframes())
        rate, width = params.framerate, params.sampwidth * params.nchannels
        offsets.append(round(len(pcm) / width / rate * 1000))
        pcm += frames + b"\0" * (int(rate * BANK_GAP_MS / 1000) * width)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setparams(params)
        wf.writeframes(pcm)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-b:a", "64k", str(path)],
                   input=buf.getvalue(), check=True)
    return offsets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--engine", action="append", help="VOICEVOX ENGINE の URL（くり返すと並べて合成）")
    a = ap.parse_args()
    engines = a.engine or ["http://localhost:50021"]
    sp = Path(a.script)
    script = json.loads(sp.read_text(encoding="utf-8"))
    audio_dir, cache_dir = sp.parent / "audio", sp.parent / ".cache"
    audio_dir.mkdir(exist_ok=True)
    cache_dir.mkdir(exist_ok=True)

    # 1. 合成する台詞とスタイルの組を集める
    jobs, plan = {}, []
    for i, e in enumerate(script["events"]):
        speak = e["type"] == "line" or (script.get("read_directions") and not e.get("inline") and not e.get("silent"))
        if not speak:
            plan.append(None)
            continue
        styles, default, v = style_choices(script, e)
        said = spoken_text(script, e)  # 旧仮名の作品は読みにしてから合成する（tools/yomi.py）
        var = {}
        for name, sid in styles.items():
            key = hashlib.sha1(json.dumps([said, sid, v.get("speedScale"), v.get("pitchScale"), v.get("intonationScale"),
                                           v.get("volumeScale")], ensure_ascii=False).encode()).hexdigest()[:12]
            var[name] = (key, sid)
            jobs[key] = (said, sid, v, i)
        plan.append((default, var, e.get("role", "narrator")))

    # 2. まだ無いものを合成する
    todo = [k for k in jobs if not (cache_dir / f"{k}.json").exists()]
    print(f"{len(jobs)} 本のうち {len(todo)} 本を合成します（ENGINE {len(engines)} 台）")
    lock, done = threading.Lock(), [0]

    def work(n_key):
        n, key = n_key
        said, sid, v, i = jobs[key]
        r = synth_event(engines[n % len(engines)], said, sid, v, cache_dir / f"{key}.wav")
        (cache_dir / f"{key}.json").write_text(json.dumps(r or {"empty": True}))
        with lock:
            done[0] += 1
            if done[0] % 20 == 0 or done[0] == len(todo):
                print(f"  {done[0]}/{len(todo)}  [{i}] {said[:20]}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(len(engines)) as ex:
        list(ex.map(work, enumerate(todo)))

    # 3. 役・スタイルごとにつないで mp3 にする
    meta = {k: json.loads((cache_dir / f"{k}.json").read_text()) for k in jobs}
    banks = {}
    for p in plan:
        if not p:
            continue
        for name, (key, sid) in p[1].items():
            if not meta[key].get("empty"):
                bank = "narrator" if p[2] == "narrator" else f"{p[2]}_{sid}"
                banks.setdefault(bank, [])
                if key not in banks[bank]:
                    banks[bank].append(key)
    pos = {}
    for bank, keys in banks.items():
        offs = write_bank(audio_dir / f"{bank}.mp3", [cache_dir / f"{k}.wav" for k in keys])
        pos.update({(bank, k): o for k, o in zip(keys, offs)})
    for f in audio_dir.glob("*.mp3"):
        if f.stem not in banks:
            f.unlink()  # 前の形（台詞ごとの mp3）や使わなくなったスタイルの音声を消す
    (audio_dir / "meta.json").unlink(missing_ok=True)

    # 4. timeline.json（時刻は初期のスタイルで計算。ページは選んだスタイルで計算し直す）
    items, t = [], 0
    for i, (e, p) in enumerate(zip(script["events"], plan)):
        t += e.get("pause_before_ms", 0)
        item = {"i": i, "type": e["type"], "text": e["text"], "start_ms": t, "dur_ms": 0, "pause_before_ms": e.get("pause_before_ms", 0)}
        for k in ("role", "emotion", "actions", "preface", "inline", "silent"):
            if k in e:
                item[k] = e[k]
        if p:
            default, var, who = p
            variants = {}
            for name, (key, sid) in var.items():
                if meta[key].get("empty"):
                    continue
                bank = "narrator" if who == "narrator" else f"{who}_{sid}"
                variants[name] = {"audio": f"audio/{bank}.mp3", "offset_ms": pos[(bank, key)], "speaker": sid,
                                  "dur_ms": meta[key]["dur_ms"], "mouth": meta[key]["mouth"]}
            if variants:
                item["style"] = default
                item["variants"] = variants
                item["dur_ms"] = variants[default]["dur_ms"]
        t += item["dur_ms"]
        items.append(item)

    timeline = {"version": 2, "title": script["title"], "author": script["author"], "source": script.get("source", ""),
                "credits": script.get("credits", []), "roles": script["roles"], "narrator": script.get("narrator", {}),
                "total_ms": t, "items": items}
    (sp.parent / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in audio_dir.glob("*.mp3")) / 1e6
    print(f"total {t/1000:.1f} s, {len(banks)} audio files ({size:.1f} MB) -> {sp.parent / 'timeline.json'}")


if __name__ == "__main__":
    main()
