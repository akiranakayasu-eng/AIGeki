# roudokugeki（朗読劇）

戯曲のテキストから、役ごとに VOICEVOX の声を割り当て、間を付けて朗読し、ブラウザで再生する。最終的には VRM のアバターで立ち稽古風の朗読劇にする（構成案は claude-shared-docs の `projects/tool-develop/rodoku-geki.md`）。

いまできること（段階 1・2 の最初の版）:

1. 戯曲のテキスト（青空文庫の txt など）を台本データ `script.json` にする（役名・台詞・ト書き・間）。
2. VOICEVOX で台詞ごとの音声と、時間の流れ `timeline.json`（各台詞の開始時刻・長さ・口パク用の母音の時刻）を作る。
3. `web/index.html` で、台詞とト書きを表示しながら順に再生する。台詞を押すとそこから再生。話している役の口が母音に合わせて動く（VRM の口パクの試し）。

## 動かし方

```sh
# 1. VOICEVOX ENGINE を起動（Docker。Mac なら VOICEVOX のアプリを起動するだけでも同じ http://localhost:50021 が使える）
docker run -d --name voicevox -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu22.04-latest

# 2. テキストを台本データにする（役名は推定されるが、--roles で渡すと確実）
python3 tools/parse_play.py 入力.txt plays/<id>/script.json --title 題 --author 作者 --source 出典URL --roles 夫,妻

# 3. script.json を手で直す（下の「台本データ」）。間を入れ直すときは
python3 tools/pauses.py plays/<id>/script.json --force

# 4. 音声と timeline.json を作る（変わった台詞だけ作り直す）
pip install requests
python3 tools/synthesize.py plays/<id>/script.json

# 5. ページを開く
python3 -m http.server 8000
# → http://localhost:8000/web/?play=<id>
```

作品:

- `plays/kamifusen/` … 岸田國士「紙風船」（青空文庫、図書カード https://www.aozora.gr.jp/cards/001154/card52082.html ）。`source.txt` は青空文庫のファイルのまま（Shift_JIS）。台詞 256、音声まで作ってある。`http://localhost:8000/web/?play=kamifusen`
- `plays/sample/` … 試しの台本（このリポジトリで書いた短い会話）。

旧仮名づかいの作品（青空文庫の新字旧仮名など）は、script.json に `"kana": "historical"` を入れると、`tools/yomi.py` が「行つて→行って」「いゝ→いい」「だらう→だろう」のように読みに直してから合成する（表示は元のまま）。規則で直らない台詞は、その event に `"read": "読み"` を書く。`python3 tools/yomi.py plays/<id>/script.json` で読みの一覧を確かめられる。

本文の役名に誤記があるときは `--alias 奏=妻` のように読み替える（紙風船に 1 か所ある）。

## 台本データ（script.json）

- `roles[].voice`: `speaker`（VOICEVOX の style id）、`styles`（感情 → style id）、`speedScale`・`pitchScale`・`intonationScale`・`volumeScale`。
- `narrator.voice`: ト書きを読む声。`read_directions: false` でト書きを読まない。
- `events[]`: `type` は `line`（台詞）か `direction`（ト書き）。
  - `emotion`: calm / happy / sad / angry / soft など。`styles` にあればそのスタイルで読む。
  - `pause_before_ms`: その前の間。`tools/pauses.py` が初期値を入れる（人が直した値は `--force` を付けない限り残る）。
  - `silent: true`: 読まずに間だけ取るト書き（「間。」「――幕――」、人物表）。
  - `inline: true`: 台詞の中の（　）から分けたト書き。読まない。
  - `actions`: 段階 3（VRM）で使う出入り・立ち位置・身振り（いまは空）。

### 間の初期値（ms）

| 場面 | 間 |
|---|---|
| 同じ役の続き | 200 |
| 話者の交代 | 400 |
| ト書きの「間」 | 1500 |
| 「長い間」「沈黙」「しばらく」 | 3000 |
| 場の変わり目・幕 | 3000 |
| 感情が前の台詞から変わる | +300 |
| 台詞の中の「……」「――」 | 600（台詞を分けて無音を入れる） |

## 権利

- 公開するのは著作権の切れた戯曲か、許可を得た戯曲だけ。
- VOICEVOX の声は話者ごとの規約に従い、クレジットを出す（`script.json` の `credits`、ページの下に表示）。
