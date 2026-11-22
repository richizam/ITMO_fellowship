"""Evaluate stage 2 on the example applications.

Each application names the ITMO unit that agreed to host the candidate. That unit is a reasonable (if not
the only correct) answer, so we check where it lands in our ranking: hit@1 / hit@3 / hit@5 and MRR.
Matching is re-run on the saved dossiers, so the external sources are not queried again.

    uv run python scripts/eval_matching.py [--save]    # --save writes the new matching into the dossiers
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.matching import catalog, match, resolve_unit  # noqa: E402
from app.pipeline import list_dossiers, save  # noqa: E402


def main() -> None:
    units = catalog()
    rows, ranks = [], []
    for d in sorted(list_dossiers(), key=lambda x: x.basic.full_name):
        if not d.example:
            continue
        m = match(d)
        if "--save" in sys.argv:
            d.matching = m
            save(d)
        idx, score = resolve_unit(d.basic.host_unit)
        resolved = f"{units[idx]['name_en']} ({score:.2f})" if idx is not None else f"— ({score:.2f})"
        rank = m.applied_unit_rank
        if idx is not None:
            ranks.append(rank)
        top = " | ".join(r.name for r in m.recommendations[:3])
        rows.append((d.basic.full_name, d.basic.host_unit, resolved, rank, top))
    for name, host, resolved, rank, top in rows:
        print(f"\n{name}\n  в заявке:  {host}\n  в каталоге: {resolved}\n  место: {rank}\n  топ-3: {top}")
    n = len(ranks)
    if n:
        hit = lambda k: sum(1 for r in ranks if r and r <= k) / n  # noqa: E731
        mrr = sum(1 / r for r in ranks if r) / n
        print(f"\nПодразделение из заявки найдено в каталоге: {n}/{len(rows)}")
        print(f"hit@1 {hit(1):.0%}   hit@3 {hit(3):.0%}   hit@5 {hit(5):.0%}   hit@10 {hit(10):.0%}   MRR {mrr:.2f}")


if __name__ == "__main__":
    main()
