"""Step 11 — Category sub-cohort breakdown.

Classify each cohort market into {sports, politics, crypto, finance, other}
using slug + question keyword matching. Then decompose:
  - Opus Brier
  - Market Brier (paired)
  - opus_win_rate
  - virtual P&L attribution (sum of net_pnl by category)

This tells us where the alpha actually lives. If 90% of P&L comes from
one category, the strategy is really a category-specific edge.

Output: category_breakdown.json
"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
COHORT = DIR / "cohort.jsonl"
BRIER = DIR / "brier_scores.jsonl"
PNL = DIR / "pnl_log.jsonl"
OUT = DIR / "category_breakdown.json"


CATEGORY_RULES = [
    ("sports", [
        r"\bnba\b", r"\bnfl\b", r"\bnhl\b", r"\bmlb\b", r"\bufc\b",
        r"\bpremier-league\b|premier league", r"\bla-liga\b|la liga", r"\bbundesliga\b",
        r"\bserie-a\b|serie a", r"\bligue-1\b|ligue 1",
        r"\bchampions-league\b|champions league", r"\beuropa\b",
        r"\bcarabao\b", r"\bfa-cup\b|fa cup",
        r"\bsuper-bowl\b|super bowl", r"\bworld-series\b|world series",
        r"\bstanley-cup\b|stanley cup", r"\bgrand-slam\b",
        r"\bf1\b|formula", r"\btennis\b", r"\bgolf\b", r"\bboxing\b",
        r"\b(wins?|beat|defeat|score|goal|match|game|playoff)s?\b",
        r"\bmadrid\b|barcelona|chelsea|liverpool|arsenal|manchester|psg|bayern",
        r"\blakers\b|celtics|warriors|nets|knicks|heat|bulls|sixers",
        r"\bchiefs\b|eagles|cowboys|49ers|patriots|giants",
        r"\bdjokovic\b|alcaraz|sinner|swiatek|sabalenka",
        r"\b(open|wimbledon|us-open|french-open|australian-open)\b",
    ]),
    ("crypto", [
        r"\bbtc\b|bitcoin", r"\beth\b|ethereum", r"\bsol\b|solana",
        r"\bxrp\b|ripple", r"\bdoge\b|dogecoin", r"\bbnb\b",
        r"\bcs2\b",  # observed in cohort
        r"\b(token|coin|defi|dex|cex|stablecoin|usdt|usdc)\b",
        r"\bcrypto\b|cryptocurrency",
        r"\b(ath|all-time high|halving|merge|fork)\b",
        r"\bbinance\b|coinbase|kraken|okx|bybit",
        r"\bvitalik\b|satoshi|saylor",
    ]),
    ("politics", [
        r"\b(trump|biden|harris|musk|putin|xi|netanyahu|zelensky)\b",
        r"\b(president|presidential|congress|senate|house|election|primary)\b",
        r"\b(republican|democrat|gop|dnc)\b",
        r"\b(supreme-court|scotus|impeach|tariff|sanction)\b",
        r"\b(uk|usa|china|russia|israel|iran|ukraine|gaza|nato)\b",
        r"\b(parliament|prime-minister|chancellor|king|queen)\b",
        r"\b(vote|ballot|polls?|approval)\b",
    ]),
    ("finance", [
        r"\b(s&p|sp500|nasdaq|dow|spx|qqq)\b",
        r"\b(fed|fomc|rate-cut|rate-hike|inflation|cpi|ppi)\b",
        r"\b(recession|gdp|unemployment|jobs)\b",
        r"\b(tesla|nvidia|apple|microsoft|google|amazon|meta)\b stock",
        r"\b(ipo|earnings|dividend)\b",
        r"\bgold\b|silver|oil",
    ]),
    ("entertainment", [
        r"\b(oscar|grammy|emmy|cannes|golden-globe)\b",
        r"\b(box-office|movie|film|netflix|disney|spotify)\b",
        r"\b(taylor-swift|beyonce|drake|kanye|bts)\b",
    ]),
    ("tech_ai", [
        r"\b(openai|anthropic|claude|gpt|llm|ai-model)\b",
        r"\b(deepmind|gemini|mistral|llama|sora)\b",
        r"\b(robot|robotaxi|self-driving)\b",
        r"\b(launch|release|announce|reveal)\b",
    ]),
]


def classify(slug: str, question: str) -> str:
    blob = (slug.lower().replace("-", " ") + " " + question.lower())
    for cat, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, blob):
                return cat
    return "other"


def _block(rows: list[dict]) -> dict:
    n = len(rows)
    paired = [r for r in rows if r["brier_market"] is not None]
    n_p = len(paired)
    mean_opus = statistics.mean(r["brier_opus"] for r in rows) if rows else None
    mean_opus_p = statistics.mean(r["brier_opus"] for r in paired) if paired else None
    mean_mkt_p = statistics.mean(r["brier_market"] for r in paired) if paired else None
    opus_wins = sum(1 for r in paired if r["brier_opus"] < r["brier_market"]) if paired else 0
    return {
        "n_total": n,
        "n_paired": n_p,
        "brier_opus_all":      round(mean_opus, 6) if mean_opus is not None else None,
        "brier_opus_paired":   round(mean_opus_p, 6) if mean_opus_p is not None else None,
        "brier_market_paired": round(mean_mkt_p, 6) if mean_mkt_p is not None else None,
        "opus_win_rate":       round(opus_wins / n_p, 4) if n_p else None,
    }


def main() -> int:
    cohort = {str(r["id"]): r for r in (json.loads(l) for l in COHORT.open())}
    brier = [json.loads(l) for l in BRIER.open()]
    pnl = [json.loads(l) for l in PNL.open()]

    # tag each brier row with category
    by_cat_brier = defaultdict(list)
    for r in brier:
        c = cohort[str(r["market_id"])]
        cat = classify(c["slug"], c["question"])
        r2 = dict(r); r2["category"] = cat
        by_cat_brier[cat].append(r2)

    # tag each P&L row with category and sum
    by_cat_pnl = defaultdict(list)
    for r in pnl:
        c = cohort[str(r["market_id"])]
        cat = classify(c["slug"], c["question"])
        by_cat_pnl[cat].append(r)

    out = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "category_breakdown",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "rules": "regex-based slug+question matching (sports → crypto → politics → finance → entertainment → tech_ai → other)",
        "categories": {},
    }

    total_pnl = sum(r["net_pnl"] for r in pnl)
    total_stake = sum(r["stake"] for r in pnl)

    for cat in sorted(by_cat_brier.keys()):
        rows_b = by_cat_brier[cat]
        rows_p = by_cat_pnl.get(cat, [])
        block = _block(rows_b)
        block["n_bets"] = len(rows_p)
        block["n_winners"] = sum(1 for r in rows_p if r["net_pnl"] > 0)
        block["bet_win_rate"] = round(block["n_winners"] / block["n_bets"], 4) if block["n_bets"] else None
        block["total_stake"] = round(sum(r["stake"] for r in rows_p), 2)
        block["total_net_pnl"] = round(sum(r["net_pnl"] for r in rows_p), 2)
        block["pnl_share_of_total"] = round(block["total_net_pnl"] / total_pnl, 4) if total_pnl else None
        block["pnl_per_dollar_staked"] = round(block["total_net_pnl"] / block["total_stake"], 4) if block["total_stake"] else None
        out["categories"][cat] = block

    out["totals"] = {
        "n_markets": len(brier),
        "n_bets": len(pnl),
        "total_stake": round(total_stake, 2),
        "total_net_pnl": round(total_pnl, 2),
    }

    # find dominant alpha source
    cats_sorted = sorted(out["categories"].items(),
                         key=lambda kv: (kv[1].get("total_net_pnl") or 0), reverse=True)
    out["alpha_attribution"] = {
        "ranked_by_net_pnl": [{"category": k, "net_pnl": v["total_net_pnl"],
                                "share": v["pnl_share_of_total"],
                                "n_bets": v["n_bets"]} for k, v in cats_sorted],
        "top_category_concentration": cats_sorted[0][1]["pnl_share_of_total"] if cats_sorted else None,
    }

    out["caveats"] = [
        "Keyword classifier is coarse; misclassifications expected.",
        "Categories not mutually balanced — sport-heavy cohort means sports stats dominate.",
        "Order matters: first matching rule wins (sports tested first to catch league names).",
        "'other' bucket likely contains genuinely unclassified, not all noise.",
    ]

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
