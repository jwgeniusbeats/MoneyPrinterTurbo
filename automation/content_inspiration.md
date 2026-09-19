# Content inspiration

Running list of genuinely good psychology/facts content formats seen on other
accounts during engagement rounds (liking/following/commenting). Not
competitor data dumped wholesale -- just formats/angles worth adapting in our
own words for future `automation/topic_backlog.json` entries or
`daily_pipeline.py` prompt inspiration. Standing practice every engagement
round, not a one-off: log a new bullet whenever something genuinely reusable
is spotted, skip logging if nothing new this round.

## Formats worth stealing (structure, not the content itself)

- "X things humans physically can't do (and why)" — @neuromatrix_, 43.8M likes. Numbered list of surprising body/brain limitations, each with a one-line neuroscience reason. Very shareable, matches our own style (short reason per item).
- "N brain facts so you can fight brain rot" — @emersynrrose. Frames a facts-listicle as a weekly self-improvement roundup rather than a bare list — gives it a personal, ongoing-series feel instead of a one-off.
- "How to know if someone is lying to you, instantly" — @psych.scenes (130K followers). Direct practical-skill framing (you can DO something with this fact) outperforms pure trivia framing.
- "6 psychology tricks that make you instantly more powerful" — @ellvado. Numbered + "instantly" + power/confidence framing; strong for anything about persuasion/confidence topics.
- Consistent visual branding across posts (a recurring motif, e.g. @psych.index's eye imagery on every thumbnail) — makes an account instantly recognizable in a feed/grid even before reading the title. We already have the brain-lightbulb logo; worth keeping it in every title-card background/overlay consistently rather than varying stock footage styles wildly.
- "Limits aren't weaknesses. They're safeguards." style closing line (@neuromatrix_) — a short reframing punchline at the end lands well and is very close to what FOLLOW_CTA_INSTRUCTION already tries to do naturally.
- "That urge to squeeze/bite your partner isn't violence, it's biology — Evolutionary psychology calls this 'Cute Aggression'" — @thebluebulb, 2M likes, 15.2K comments. Names an obscure real psychology term for a universally-relatable everyday feeling, then explains it in one line. Top comments were people riffing with their own funny examples — naming-a-feeling format is strong bait for quotable/funny replies, which drives comment count.
- "5 Questions that can Hijack [someone's] Brain" — @mindshadowlabs (39.9K followers, bio: "I teach you what others hide. Dark Psychology • Silent Tactics • Social Power"). Numbered-questions-as-hook format, framed as secret/hidden knowledge. "Dark psychology" framing (manipulation-awareness angle) draws heavy engagement in this niche — worth an occasional topic in that vein (e.g. "signs someone is manipulating you"), kept ethical/defensive in framing rather than teaching manipulation.
- "How to Trick Your Brain Into Doing Hard Things" — @carlytalks2, 53K likes, 516 comments, unusually long-form (7:22, not a typical short). Top comment (744 likes) distilled the whole video into a memorable acronym: **LOTUS** — Lower the difficulty, Outsmart yourself, Ten minutes only, Use rewards, Stack small wins. Acronym-of-the-technique format is very shareable/saveable on its own — worth building a script around a coined acronym when a topic has ~5 discrete steps, rather than just listing them.
- "Strong Empathy" / "A Cluttered Desk" / "Sign Of Creativity" — Untold Facts (Facebook, 66K likes/post). Simple hand-drawn stick-figure whiteboard-style animation illustrating one personality/behavior trait per short clip, bold yellow-highlighted keyword in the caption text overlay. Very different visual language from our stock-footage style but performs strongly -- worth considering an occasional simple-illustration or animated-text-only video as a format experiment, not a wholesale switch.
- Poetic/reflective micro-essay captions (not just a single explainer line) — @robthebank (Instagram, 1M likes on one post about emotional intelligence/forgiveness). Short punchy paragraphs building to a quotable closing line ("That's not cold. That's clarity."). Contrasts with our current punchy-single-fact captions; worth trying this longer, more reflective caption style on an occasional post about relationships/boundaries/emotional topics rather than pure trivia.

## Accounts worth periodically checking for fresh angles

Not to copy verbatim, just to see what's landing: @psych.index, @neuromatrix_, @emersynrrose, @psych.scenes, @ellvado, @thebluebulb, @mindshadowlabs, @carlytalks2, Untold Facts (FB), @robthebank (IG), @psy_facto (YT).

## How to apply

When `daily_pipeline.py`'s topic backlog runs low, or when picking new `HOOK_PATTERNS`-style additions, check this list for a structural angle not yet tried, rather than only generating from scratch.
