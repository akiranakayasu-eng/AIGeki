#!/usr/bin/env python3
"""script.json から VOICEVOX で台詞ごとの音声と timeline.json を作る。

使い方:
  python3 tools/synthesize.py plays/<id>/script.json [--engine http://localhost:50021]

- 出力: plays/<id>/audio/*.mp3 と plays/<id>/timeline.json
- 同じ文・同じ声の音声はファイル名のハッシュで再利用する（script.json を直して再実行すると、変わった台詞だけ作り直す）。
- 台詞の中の「……」「――」で台詞を分け、間に 600 ms の無音を入れる。
- script.json に "kana": "historical" があれば、旧仮名を tools/yomi.py で読みにしてから合成する（event の "read" が優先）。
- 口パク用に、モーラごとの母音の時刻（mouth: [[ms, "a"], ...]）を timeline.json に入れる。
"""
import argparse, hashlib, io, json, re, subprocess, sys, wave
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from yomi import spoken_text  # noqa: E402

INNER_PAUSE_MS = 600
SPLIT_RE = re.compile(r"(?:…{2,}|―{2,}|─{2,}|-{2,})")
VOWELS = {"a": "a", "i": "i", "u": "u", "e": "e", "o": "o", "A": "a", "I": "i", "U": "u", "E": "e", "O": "o", "N": "n", "cl": "n"}


def voice_for(script, e):
    if e["type"] == "line":
        role = next(r for r in script["roles"] if r["id"] == e["role"])
        v = role.get("voice", {})
    else:
        v = script.get("narrator", {}).get("voice", {})
    if "speaker" not in v:
        sys.exit(f"声が決まっていません: {e.get('role', 'narrator')}（script.json の voice.speaker を入れる）")
    sid = v.get("styles", {}).get(e.get("emotion", "calm"), v["speaker"])
    return sid, v


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


def synth_event(engine, text, sid, v, out_mp3):
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
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setparams(params)
        wf.writeframes(pcm)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-b:a", "64k", str(out_mp3)],
                   input=buf.getvalue(), check=True)
    return round(offset * 1000), mouth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--engine", default="http://localhost:50021")
    a = ap.parse_args()
    sp = Path(a.script)
    script = json.loads(sp.read_text(encoding="utf-8"))
    audio_dir = sp.parent / "audio"
    audio_dir.mkdir(exist_ok=True)
    meta_path = audio_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    items, t, used = [], 0, set()
    for i, e in enumerate(script["events"]):
        t += e.get("pause_before_ms", 0)
        item = {"i": i, "type": e["type"], "text": e["text"], "start_ms": t, "dur_ms": 0}
        for k in ("role", "emotion", "actions", "preface", "inline", "silent"):
            if k in e:
                item[k] = e[k]
        speak = e["type"] == "line" or (script.get("read_directions") and not e.get("inline") and not e.get("silent"))
        if speak:
            sid, v = voice_for(script, e)
            said = spoken_text(script, e)  # 旧仮名の作品は読みにしてから合成する（tools/yomi.py）
            key = hashlib.sha1(json.dumps([said, sid, v], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]
            name = f"{key}.mp3"
            if name in meta and (audio_dir / name).exists():
                dur, mouth = meta[name]["dur_ms"], meta[name]["mouth"]
            else:
                r = synth_event(a.engine, said, sid, v, audio_dir / name)
                if r is None:
                    speak = False
                else:
                    dur, mouth = r
                    meta[name] = {"dur_ms": dur, "mouth": mouth}
                    print(f"[{i}] {dur} ms {e['text'][:30]}")
            if speak:
                item.update({"audio": f"audio/{name}", "dur_ms": dur, "mouth": mouth, "speaker": sid})
                used.add(name)
        if not speak and e["type"] == "direction":
            item["dur_ms"] = 0
        t += item["dur_ms"]
        items.append(item)

    for f in audio_dir.glob("*.mp3"):
        if f.name not in used:
            f.unlink()
            meta.pop(f.name, None)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False) + "\n")
    timeline = {"title": script["title"], "author": script["author"], "source": script.get("source", ""),
                "credits": script.get("credits", []), "roles": script["roles"], "total_ms": t, "items": items}
    (sp.parent / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"total {t/1000:.1f} s, {len(used)} audio files -> {sp.parent / 'timeline.json'}")


if __name__ == "__main__":
    main()
