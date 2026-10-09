# Evaluation labels

`labels.jsonl` holds one JSON object per line, for example:

    {"work_id": "w_...", "title": "...", "profile": "selection-inference", "relevant": true, "note": "...", "labeled_at": "2026-10-20"}

Lark uses these to measure screening recall and briefing precision. Label a mix of
relevant and irrelevant items across sources, and keep versions of the same paper
together (preprint and journal version share a work_id).
