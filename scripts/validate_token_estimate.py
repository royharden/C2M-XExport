"""Dev-only: check xexport's token estimate against real token counts.

xexport sizes HTML pages with `xexport.render.html.estimate_tokens`, a weight per
character class, because no real tokenizer can ship with the installed CLI. This
script is how those weights were set and how to re-check them.

Two references, and they disagree with each other:

* **Claude Code's Read tool**, the reader the page budget exists for. Its
  tokenizer cannot be run locally, but the tool reports an exact count whenever a
  read is refused: "File content (N tokens) exceeds maximum allowed tokens
  (25000)". READ_TOOL_TOKENS below holds the counts it reported on 2026-10-02
  (Claude Fable 5.1) for the synthetic files this script generates. This is the
  reference the estimate is held to.
* **tiktoken** (o200k_base, cl100k_base), OpenAI's tokenizer. On real exports the
  Read tool counted 1.40-1.46 times what tiktoken does, so a page that is safe by
  tiktoken is not thereby safe to read. Shown for comparison only.

Usage:

    uv run python scripts/validate_token_estimate.py --synthetic
    uv run --with tiktoken python scripts/validate_token_estimate.py <export-folder>...
    uv run python scripts/validate_token_estimate.py --write-synthetic <dir>

`--synthetic` exits 1 if the estimate is more than 10% under the Read tool on any
class. A folder run reports every .html file and exits 1 if a page-NNN.html that
is not marked oversize is over a reader cap by tiktoken or by line or byte count.

To re-calibrate: `--write-synthetic <dir>`, then in Claude Code Read each file
with an explicit offset and limit (offset 1, limit 5000) and copy the refused
count into READ_TOOL_TOKENS. To measure a real page the same way, concatenate it
with itself until it is over 25,000 tokens and divide the reported count.
"""

from __future__ import annotations

import base64
import math
import random
import re
import sys
from pathlib import Path

from xexport.render.html import estimate_tokens

CAP_TOKENS, CAP_LINES, CAP_BYTES = 25_000, 2_000, 256_000
UNDERCOUNT_LIMIT = 0.10
SYNTHETIC_BYTES = 110_000

# One repeated unit per class. The four random classes are generated, seeded.
_UNITS = {
    "english": "The quick brown fox jumps over the lazy dog. Agents that open an "
               "exported page with a file-read tool hit a per-call cap.\n",
    "chinese": "快速的棕色狐狸跳过了懒狗。这是一个用来测试分词器的句子。\n",
    "japanese": "素早い茶色の狐がのろまな犬を飛び越えた。トークナイザーの試験です。\n",
    "russian": "Быстрая коричневая лиса перепрыгивает через ленивую собаку.\n",
    "arabic": "الثعلب البني السريع يقفز فوق الكلب الكسول.\n",
    "emoji": "😀🎉🚀👍🏽👨‍👩‍👧‍👦🔥✨🧪📦🛠️ \n",
    "escjson": '{&#34;command&#34;: &#34;ls -la&#34;, &#34;path&#34;: '
               '&#34;C:\\Users\\x\\y&#34;, &#34;n&#34;: 12345}\n',
    "markup": '<div class="tool-result"><div class="truncatable">'
              '<div class="truncatable-content"><pre>ok</pre></div></div></div>\n',
    "code": "    def fits(self, nbytes: int, newlines: int, count: int, "
            "limits: Limits) -> bool:\n"
            "        total_bytes = self.shell_bytes + self.body_bytes + nbytes + count\n"
            "        return total_bytes <= limits.bytes\n",
}

# Tokens the Claude Code Read tool reported for each synthetic file, 2026-10-02.
READ_TOOL_TOKENS = {
    "english": 40_047,
    "chinese": 45_332,
    "japanese": 39_607,
    "russian": 28_514,
    "arabic": 38_104,
    "emoji": 70_185,
    "escjson": 75_248,
    "markup": 44_029,
    "code": 46_039,
    "hex": 72_830,
    "base64": 106_304,
    "digits": 47_507,
    "uuid": 51_728,
}


def synthetic() -> dict[str, str]:
    """The calibration files, byte for byte as they were measured."""
    texts = {}
    for name, unit in _UNITS.items():
        texts[name] = unit * math.ceil(SYNTHETIC_BYTES / len(unit.encode("utf-8")))
    rng = random.Random(7)
    texts["hex"] = "\n".join(
        "%064x" % rng.getrandbits(256) for _ in range(1700)) + "\n"
    texts["base64"] = "\n".join(
        base64.b64encode(rng.randbytes(57)).decode() for _ in range(1450)) + "\n"
    texts["digits"] = "\n".join(
        " ".join(str(rng.randrange(10**9, 10**10)) for _ in range(5))
        for _ in range(1900)) + "\n"
    texts["uuid"] = "\n".join(
        "%08x-%04x-%04x-%04x-%012x" % (
            rng.getrandbits(32), rng.getrandbits(16), rng.getrandbits(16),
            rng.getrandbits(16), rng.getrandbits(48))
        for _ in range(1900)) + "\n"
    return texts


def _tiktoken_encodings() -> list:
    try:
        import tiktoken
    except ImportError:
        return []
    return [tiktoken.get_encoding(name) for name in ("o200k_base", "cl100k_base")]


def check_synthetic() -> bool:
    print(f"{'class':<10} {'bytes':>8} {'estimate':>9} {'read tool':>10} "
          f"{'est/real':>8} {'bytes/token':>11}")
    ok = True
    for name, text in synthetic().items():
        nbytes = len(text.encode("utf-8"))
        estimate = estimate_tokens(text)
        real = READ_TOOL_TOKENS[name]
        ratio = estimate / real
        good = ratio >= 1 - UNDERCOUNT_LIMIT
        ok &= good
        print(f"{name:<10} {nbytes:>8} {estimate:>9} {real:>10} {ratio:>8.2f} "
              f"{nbytes / real:>11.2f}{'' if good else '  UNDER'}")
    return ok


def check_folder(folder: Path, encodings: list) -> bool:
    print(f"-- {folder}")
    print(f"{'file':<16} {'lines':>6} {'bytes':>9} {'estimate':>9} {'o200k':>8} "
          f"{'cl100k':>8} {'longest line':>12}")
    ok = True
    for path in sorted(folder.glob("*.html"), key=lambda p: (len(p.stem), p.stem)):
        text = path.read_text(encoding="utf-8", errors="replace")
        nbytes = len(text.encode("utf-8"))
        lines = text.count("\n") + 1
        longest = max((len(line) for line in text.split("\n")), default=0)
        real = [len(enc.encode(text, disallowed_special=())) for enc in encodings]
        columns = "".join(f" {count:>8}" for count in real) or f" {'-':>8} {'-':>8}"
        note = ""
        # index.html and full.html are not sized to a cap; only report them.
        if re.fullmatch(r"page-\d+\.html", path.name):
            oversize = 'class="page-note oversize"' in text
            over = (lines > CAP_LINES or nbytes > CAP_BYTES
                    or any(count > CAP_TOKENS for count in real))
            if oversize:
                note = "  oversize (flagged)"
            elif over:
                note = "  OVER CAP"
                ok = False
        print(f"{path.name:<16} {lines:>6} {nbytes:>9} {estimate_tokens(text):>9}"
              f"{columns} {longest:>12}{note}")
    return ok


def main(argv: list[str]) -> int:
    if "--write-synthetic" in argv:
        out = Path(argv[argv.index("--write-synthetic") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for name, text in synthetic().items():
            with open(out / f"syn_{name}.txt", "w", encoding="utf-8", newline="") as f:
                f.write(text)
        print(f"wrote {len(READ_TOOL_TOKENS)} files to {out}")
        return 0

    ok = True
    if "--synthetic" in argv:
        ok &= check_synthetic()
    folders = [Path(a) for a in argv if not a.startswith("--")]
    if folders:
        encodings = _tiktoken_encodings()
        if not encodings:
            print("tiktoken is not installed: run with `uv run --with tiktoken` "
                  "for the tokenizer columns")
        for folder in folders:
            ok &= check_folder(folder, encodings)
    print("OK" if ok else "FAILED: see rows marked UNDER or OVER CAP")
    return 0 if ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
