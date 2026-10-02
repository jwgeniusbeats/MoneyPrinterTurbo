# Animation service: lead finding + tracker

Why this file exists: my search tool cannot see Reddit threads (reddit.com is
blocked for it, and general web searches only returned Fiverr/Dribbble seller
pages, no buyers). So leads are found by hand, and the work is split like this:
YOU find the thread and paste it to Claude, Claude makes a sample on THEIR topic
and writes the reply.

## Where to look (run these yourself, logged in)
Reddit search, sort by New, past month. Reply only in threads, never cold-post.
- r/forhire and r/HireAnEditor: search `animation`, `explainer`, `animated video`
  (look for posts tagged [HIRING], not sellers)
- r/startups, r/SaaS, r/Entrepreneur, r/smallbusiness: `explainer video`,
  `animated video`, `product video`, `how do I make videos`
- r/podcasting, r/NewTubers: people asking how to make faceless/animated shorts
- r/coaching or r/CoachingBusiness style communities: `short videos`, `Reels`
  (people who explain things and say they hate being on camera)
- LinkedIn / X: search `explainer video` + `looking for`, `need an animator`
Sort by who is ASKING, not who is selling.

## A good lead (all three)
1. They state a concrete topic or product.
2. They ask for or complain about video, in the last 30 days.
3. Their question can be answered with 20-45 s vertical animation.
Skip long-form (5+ min), 3D, character animation, or "free work" requests.

## Workflow per lead
1. Paste thread link + text to Claude.
2. Claude writes a 3-5 scene script on their topic and renders the sample
   (about 15 minutes of work).
3. You send ONE message with the sample (template in animation_service_offer.md).
   Follow-up once after 4 days. Then stop.
4. Log it below.

## Tracker
| # | Date | Where (link) | Topic | Sample sent | Reply | Outcome |
|---|------|--------------|-------|-------------|-------|---------|
|   |      |              |       |             |       |         |

Stop rule: after 5 samples sent, count replies after 7 days. Fewer than 1 reply
in 5 means this offer is not working as it is: change the offer, not the volume.
