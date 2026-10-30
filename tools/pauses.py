#!/usr/bin/env python3
"""間（pause_before_ms）の初期値を入れる。人が script.json で直した値は上書きしない（--force で上書き）。

きまり（ms）:
  同じ役の続き 200 / 話者の交代 400 / ト書きの「間」1500 / 「長い間」「沈黙」3000 / 場の変わり目 3000
  感情が前の台詞から変わるとき +300
台詞の中の「……」「――」の間は tools/synthesize.py が台詞を分けて 600 ms 入れる。
"""
import json, re, sys

SAME_ROLE, SWITCH, MA, LONG_MA, SCENE, EMOTION_SHIFT = 200, 400, 1500, 3000, 3000, 300


def direction_pause(text):
    if re.search(r"長い間|長き間|沈黙|しばらく", text):
        return LONG_MA
    if re.search(r"幕|暗転|場$|第.場", text):
        return SCENE
    if re.fullmatch(r"[ 　]*間[。、 　]*", text) or re.search(r"(^|[、。 　])間([。、]|$)", text):
        return MA
    return 0


def apply_pauses(events, force=False):
    prev_role, prev_emotion, carry = None, None, 0
    for e in events:
        if e["type"] == "direction":
            carry = max(carry, direction_pause(e["text"]))
            if force or "pause_before_ms" not in e:
                e["pause_before_ms"] = 300 if not e.get("inline") else 100
            continue
        p = SWITCH if prev_role not in (None, e["role"]) else SAME_ROLE
        if prev_role is None:
            p = 0
        p = max(p, carry)
        if prev_emotion and e.get("emotion") != prev_emotion:
            p += EMOTION_SHIFT
        if force or "pause_before_ms" not in e:
            e["pause_before_ms"] = p
        prev_role, prev_emotion, carry = e["role"], e.get("emotion"), 0
    return events


if __name__ == "__main__":
    path = sys.argv[1]
    s = json.load(open(path, encoding="utf-8"))
    apply_pauses(s["events"], force="--force" in sys.argv)
    open(path, "w", encoding="utf-8").write(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
    print("pauses updated:", path)
