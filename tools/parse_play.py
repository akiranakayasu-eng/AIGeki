#!/usr/bin/env python3
"""戯曲のテキスト（青空文庫の txt / 普通の UTF-8 テキスト）を script.json の下書きにする。

使い方:
  python3 tools/parse_play.py 入力.txt plays/<id>/script.json --title 題 --author 作者 --source URL \
      [--roles 夫,妻] [--alias 奏=妻]

- 青空文庫の注記（《ルビ》、｜、［＃…］）とヘッダー・フッター（底本：以降）は外す。
- 「役名＋空白＋台詞」の行を台詞、それ以外をト書きとみなす。役名の行のすぐ下の（　）で始まらない行は、同じ役の台詞の続きとみなす。役名は --roles で渡すか、行頭の語の出現回数から推定する。
- 台詞の中の（　）はト書きとして分け、台詞からは外す。
- 間（pause_before_ms）は tools/pauses.py のきまりで入れる。感情（emotion）は calm で入れるので、あとで人か Claude が直す。
"""
import argparse, collections, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pauses import apply_pauses  # noqa: E402

LINE_RE = re.compile(r"^[ 　]*([^\s　（(「」]{1,8})[ 　]+(.+)$")
PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")
PAUSE_ONLY_RE = re.compile(r"[ 　―─-]*(長い間|長き間|長い沈黙|間|沈黙|しばらく|幕)[。 　―─-]*")


def read_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def clean_aozora(text):
    lines = text.replace("\r\n", "\n").split("\n")
    # ヘッダー: 「-------」で囲まれた記号の説明を外す
    dash = [i for i, l in enumerate(lines) if re.match(r"^-{10,}", l)]
    if len(dash) >= 2:
        lines = lines[:dash[0]] + lines[dash[1] + 1:]
    # フッター: 「底本：」以降を外す
    for i, l in enumerate(lines):
        if l.startswith("底本："):
            lines = lines[:i]
            break
    out = []
    for l in lines:
        if l.strip() and not re.sub(r"［＃[^］]*］", "", l).strip(" 　"):
            continue  # 注記だけの行（［＃ここから１字下げ］など）は行ごと外す
        l = re.sub(r"《[^》]*》", "", l)
        l = l.replace("｜", "")
        l = re.sub(r"［＃[^］]*］", "", l)
        out.append(l.rstrip())
    return out


def guess_roles(lines, min_count=3):
    c = collections.Counter()
    for l in lines:
        m = LINE_RE.match(l)
        if m:
            c[m.group(1)] += 1
    return [r for r, n in c.most_common() if n >= min_count]


def to_events(lines, roles, aliases=None):
    aliases = aliases or {}
    events = []
    body_started = False
    prev_role = None  # 直前の行が台詞の行ならその役（次の行が続きの台詞かを見る）
    for l in lines:
        s = l.strip(" 　")
        if not s:
            prev_role = None
            continue
        m = LINE_RE.match(l)
        name = aliases.get(m.group(1), m.group(1)) if m else None
        if m and name in roles:
            body_started = True
            role, text = name, m.group(2).strip(" 　")
            # （　）の位置を保って、台詞とト書きを順に並べる
            pos = 0
            for pm in PAREN_RE.finditer(text):
                add_line(events, role, text[pos:pm.start()])
                events.append({"type": "direction", "text": pm.group(1), "actions": [], "inline": True, "role": role})
                pos = pm.end()
            add_line(events, role, text[pos:])
            prev_role = role
        elif prev_role and not s.startswith(("（", "(")):
            # 役名の行のすぐ下で字下げされた行（新聞を読み上げるなど）は、同じ役の台詞の続き
            add_line(events, prev_role, PAREN_RE.sub("", s))
        elif body_started:
            d = {"type": "direction", "text": PAREN_RE.sub(r"\1", s), "actions": []}
            if PAUSE_ONLY_RE.fullmatch(d["text"]):
                d["silent"] = True  # 「間。」「――幕――」などは読まずに間だけ取る
            events.append(d)
        else:
            # 題名・人物表・場の説明など、最初の台詞より前の行は前書き。人物表（役名だけの行）は読まない
            d = {"type": "direction", "text": s, "actions": [], "preface": True}
            if s in roles or PAUSE_ONLY_RE.fullmatch(s):
                d["silent"] = True
            events.append(d)
    return events


def add_line(events, role, text):
    text = text.strip(" 　")
    if text.strip("。、…―"):
        events.append({"type": "line", "role": role, "text": text, "emotion": "calm"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--roles", default="", help="役名をカンマ区切りで（省略時は推定）")
    ap.add_argument("--alias", action="append", default=[],
                    help="本文の誤記などを役名に読み替える。例: --alias 奏=妻（くり返し可）")
    a = ap.parse_args()

    lines = clean_aozora(read_text(a.src))
    roles = [r for r in a.roles.split(",") if r] or guess_roles(lines)
    if not roles:
        sys.exit("役名が見つかりません。--roles で渡してください。")
    role_objs = [{"id": f"r{i+1}", "name": r, "voice": {}} for i, r in enumerate(roles)]
    name2id = {r["name"]: r["id"] for r in role_objs}
    aliases = dict(x.split("=", 1) for x in a.alias)
    events = to_events(lines, roles, aliases)
    for e in events:
        if "role" in e:
            e["role"] = name2id[e["role"]]
    script = {
        "title": a.title, "author": a.author, "source": a.source,
        "read_directions": True,
        "narrator": {"voice": {}},
        "roles": role_objs,
        "events": apply_pauses(events),
    }
    Path(a.dst).parent.mkdir(parents=True, exist_ok=True)
    Path(a.dst).write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n_lines = sum(1 for e in events if e["type"] == "line")
    print(f"roles={roles} lines={n_lines} directions={len(events) - n_lines} -> {a.dst}")


if __name__ == "__main__":
    main()
