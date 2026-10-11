from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_RUN = "smoke-real-01"
ROOT = Path("experiments/results") / (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN)


def hdr(title: str) -> None:
    print("\n== " + title + " ==")


def main() -> int:
    meta = json.loads((ROOT / "meta.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (ROOT / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    hdr("A. real model")
    print("provider      :", meta["provider"])
    print("model         :", meta["model"])
    print("manifest_sha  :", (meta["manifest_sha256"] or "")[:16])
    print("git           :", meta["git"])
    print(
        "banner in rpt :",
        "PIPELINE VALIDATION" in (ROOT / "report.md").read_text(encoding="utf-8"),
    )
    for row in rows:
        use = row.get("usage") or {}
        req = str(use.get("request_id"))[:18]
        print(
            f"  {row['strategy']:<20} in={use.get('input_tokens')} "
            f"out={use.get('output_tokens')} lat={use.get('latency_ms')}ms "
            f"req={req} att={use.get('attempts')} "
            f"fin={use.get('finish_reason')} model={use.get('model')}"
        )

    hdr("B. controls held")
    print("models identical :", len({(r.get("usage") or {}).get("model") for r in rows}) == 1)
    print("distinct digests :", len({r["prompt_digest"] for r in rows}) == len(rows))
    print("under ceiling    :", all(r["context"]["total_tokens"] <= 6000 for r in rows))
    full = next(r for r in rows if r["strategy"] == "repo_aware")
    ctrl = next(r for r in rows if r["strategy"] == "ctx_random_matched")
    print(
        "H4 sizing        : repo_aware={} control.ceiling={} match={}".format(
            full["context"]["total_tokens"],
            ctrl["context"]["ceiling_tokens"],
            ctrl["context"]["ceiling_tokens"] == full["context"]["total_tokens"],
        )
    )
    print("context per arm  :")
    for row in rows:
        ctx = row["context"]
        print(
            f"    {row['strategy']:<20} {ctx['total_tokens']:>5} tok  "
            f"{len(ctx['items'])} items  {ctx['per_category_tokens']}"
        )

    hdr("C. no secret in artifacts")
    secret = None
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "OPEN_ROUTER_API_KEY" and len(value.strip()) > 20:
                secret = value.strip().strip('"').strip("'")
    leaks = []
    for path in sorted(ROOT.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            if "sk-or-" in text:
                leaks.append(str(path) + " [sk-or- prefix]")
            if secret and secret in text:
                leaks.append(str(path) + " [full key]")
    print("secret scanned   :", "OPEN_ROUTER_API_KEY present" if secret else "no key in .env")
    print("leaks            :", leaks or "NONE")

    hdr("D. evaluation ran")
    for row in rows:
        ev = row.get("evaluation") or {}
        val = row.get("validation") or {}
        print(
            f"  {row['strategy']:<20} outcome={row.get('outcome')!s:<26} "
            f"valid={val.get('valid')} exit={ev.get('exit_codes')} "
            f"timings={ev.get('timings_ms')}"
        )
    print("patches written  :", sorted(p.name for p in (ROOT / "patches").glob("*")))

    hdr("E. one row per arm")
    print("row count        :", len(rows))
    print("strategies       :", sorted(r["strategy"] for r in rows))

    return 0


if __name__ == "__main__":
    sys.exit(main())
