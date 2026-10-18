# Buzz

Buzz answers "what is being discussed about X right now?" The answer is about attention, so
separate three things clearly: **what the work is** (primary source), **who is discussing it**
(posts and threads), and **why it matters to the user** (your inference).

## 1. Start from what Aster already knows

```
aster candidates --days 14 --json [--field <id>]
```

Items with `signals:` in their reasons already have recorded discussion (for example Hugging
Face Daily Papers upvotes or posts recorded earlier). Use them as leads, not conclusions.

## 2. Search where discussion happens

Follow the platform recipes in [Web search](web-search.md). Cover, as relevant to the field:

- **X** and **Bluesky**: posts by researchers and labs about the topic, within the window.
- **Hacker News**: its search API returns stories with points and comment counts.
- **Reddit**: subreddits such as r/MachineLearning, r/econometrics, r/quant.
- **Blogs and newsletters**: lab blogs, field newsletters, Substack posts.

Collect candidate posts, then group them by the work or release they discuss. A work discussed
by several independent people across platforms has more attention than one post with many
likes; a lab announcing its own release is an announcement, not independent attention.

## 3. Verify and record

Open the primary source for each work you will report (paper, model card, release page). Record
the work and its posts with `aster add`, attaching each post to the work as a signal with the
metrics shown on the page, if any.

## 4. Report

Order by the breadth of independent discussion, not by raw counts. For each work: what it is
(linked), who is discussing it and where (linked posts), the main points of agreement or
criticism, and one line on relevance to the user. Say plainly when the discussion is thin or
when coverage of a platform was limited.
