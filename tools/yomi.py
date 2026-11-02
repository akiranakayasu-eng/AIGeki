#!/usr/bin/env python3
"""旧仮名づかいの文を、VOICEVOX が読み違えにくい現代かなづかいの「読み」にする（表示はもとの文のまま）。

script.json に "kana": "historical" があると tools/synthesize.py がこれを通してから合成する。
規則で直しきれない台詞は、その event に "read": "読み" を書けば、そちらを優先する。

確かめ方:
  python3 tools/yomi.py plays/<id>/script.json        # 読みが変わる台詞を一覧する
  python3 tools/yomi.py "行つてらつしやいよ"          # 1 文だけ
"""
import json, re, sys
from pathlib import Path

HIRA = "ぁ-ゖ"
KATA = "ァ-ヺー"
KANJI = "一-龥々〆"
VOICED = str.maketrans("かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホ",
                       "がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボ")
SMALL = str.maketrans("やゆよヤユヨ", "ゃゅょャュョ")

# 漢字の読み違えを避ける置き換え（読みだけに効く）
WORDS = [
    ("云つ", "言っ"), ("云ひ", "言い"), ("云ふ", "言う"), ("云は", "言わ"), ("云へ", "言え"), ("云", "言"),
    ("何ん", "なん"), ("何時", "いつ"), ("此の", "この"), ("其の", "その"), ("先づ", "まず"), ("一寸", "ちょっと"),
    ("喋舌", "しゃべ"), ("お茶潰", "お茶漬"), ("見たいに", "みたいに"), ("なほ", "なお"), ("一と", "ひと"),
    ("附合", "つきあ"), ("草臥", "くたび"), ("註文", "注文"), ("坐", "座"), ("呉れ", "くれ"), ("曳い", "ひい"),
    ("長閑", "のどか"), ("独言", "ひとりごと"), ("起ち", "立ち"), ("溜息を吐", "ためいきをつ"), ("欠伸", "あくび"),
    ("若し", "もし"), ("為め", "ため"), ("何処", "どこ"), ("今迄", "今まで"), ("倚り", "より"),
    ("もつと", "もっと"), ("はつきり", "はっきり"), ("でもつて", "でもって"), ("ぎは", "ぎわ"),
    ("おつしや", "おっしゃ"), ("をかし", "おかし"), ("をば", "おば"), ("程ヶ谷", "ほどがや"),
    ("それや", "そりゃ"), ("あれや", "ありゃ"), ("これや", "こりゃ"), ("なけれや", "なけりゃ"),
]


def iteration_marks(s):
    out = []
    for ch in s:
        if ch in "ゝヽ" and out:
            ch = out[-1]
        elif ch in "ゞヾ" and out:
            ch = out[-1].translate(VOICED)
        out.append(ch)
    return "".join(out)


def modernize(s):
    s = iteration_marks(s)
    for a, b in WORDS:
        s = s.replace(a, b)
    s = s.replace("ゐ", "い").replace("ゑ", "え").replace("ヰ", "ィ").replace("ヱ", "ェ")
    s = s.replace("ぢ", "じ").replace("づ", "ず")
    # 拗音: し・ち… のあとの や を小さく（新字旧仮名の本文は拗音を大きく書く）。ゆ・よ は「しよう」「りよ」と紛れるので触らない
    s = re.sub(r"([しじちりキギシジチニヒビピミリ])([やヤ])", lambda m: m.group(1) + m.group(2).translate(SMALL), s)
    # 促音: 語の中の つ・ツ のあとに か・さ・た・ぱ 行が続けば小さく（を・と・は・が・に・へ のあとは語の頭なので触らない）
    s = re.sub(rf"(?<=[{HIRA}{KANJI}])(?<![をとはがにへ])つ(?=[かきくけこさしすせそたちてとぱぴぷぺぽ])", "っ", s)
    s = re.sub(rf"(?<=[{KATA}])ツ(?=[カキクケコサシスセソタチツテトパピプペポ])", "ッ", s)
    s = re.sub(rf"(?<=[{HIRA}{KATA}])[つツ](?=[。、！？!?…―　 」』）]|$)", lambda m: "っ" if m.group(0) == "つ" else "ッ", s)
    # 長音: あ段＋う → お段＋う（だらう→だろう、ありがたう→ありがとう）。ふ→う より先に行う
    s = re.sub(r"([かさたなはまやらがざだばぱ])う", lambda m: "こそとのおもよろごぞどぼぽ"["かさたなはまやらがざだばぱ".index(m.group(1))] + "う", s)
    s = re.sub(r"([しち])ゃう", r"\1ょう", s)
    s = re.sub(r"([きしち])う", lambda m: m.group(1) + "ゅう", s)
    s = s.replace("せう", "しょう").replace("てう", "ちょう").replace("けう", "きょう").replace("れう", "りょう")
    # 語中・語尾のハ行 → ワ行（思ひ→思い、云ふ→いう、考へ→考え、なほ→なお、言はない→言わない）
    # は は動詞の活用（言はない・合はせ・食はして・言はう）だけ。「はいって」「はじめ」などの語頭の は は残す
    s = re.sub(rf"(?<=[{HIRA}{KANJI}])は(?=[なうせれずぬし])", "わ", s)
    s = s.replace("わう", "おう")
    s = re.sub(rf"(?<=[{HIRA}{KANJI}])ひ", "い", s)
    s = re.sub(rf"(?<=[{HIRA}{KANJI}])ふ(?![つっ])", "う", s)
    s = re.sub(rf"(?<=[{HIRA}{KANJI}])へ", "え", s)
    return s


def spoken_text(script, e):
    if e.get("read"):
        return e["read"]
    return modernize(e["text"]) if script.get("kana") == "historical" else e["text"]


if __name__ == "__main__":
    arg = sys.argv[1]
    if Path(arg).exists():
        script = json.loads(Path(arg).read_text(encoding="utf-8"))
        for i, e in enumerate(script["events"]):
            r = spoken_text(script, e)
            if r != e["text"]:
                print(f"[{i}] {e['text']}\n   → {r}{'（read）' if e.get('read') else ''}")
    else:
        print(modernize(arg))
