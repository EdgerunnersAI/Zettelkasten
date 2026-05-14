# User Pricing Reference

This file keeps the current pricing structure and constraints handy for humans. The executable source of truth is `config.py`; update `config.py` first when changing prices, quotas, packs, or recommendations.

## Subscriptions

### Free
- Zettels: 2/day, 10/week, 30/month
- Kastens: 1 total
- RAG questions: 30/month
- Price: ₹0

### Basic
- Zettels: 5/day, 30/week, 50/month
- Kastens: 5 total
- RAG questions: 100/month
- Monthly: list ₹299, launch ₹149
- Quarterly: list ₹849, launch ₹399
- Yearly: list ₹2999, launch ₹1499

### Max
- Zettels: 30/day, 100/week, 200/month
- Kastens: 5/week, 50 total
- RAG questions: 500/month
- Monthly: list ₹499, launch ₹349
- Quarterly: list ₹1449, launch ₹999
- Yearly: list ₹4999, launch ₹3499

## One-Time Packs

### Zettels
- 1: list ₹25, launch ₹15
- 5: list ₹99, launch ₹69
- 10: list ₹179, launch ₹99
- 20: list ₹329, launch ₹169
- 30: list ₹508, launch ₹268
- 40: list ₹658, launch ₹338
- 50: list ₹837, launch ₹437

### Kastens
- 1: list ₹129, launch ₹69
- 5: list ₹499, launch ₹299
- 10: list ₹899, launch ₹499
- 20: list ₹1599, launch ₹899
- 30: list ₹2498, launch ₹1398
- 40: list ₹3198, launch ₹1798
- 50: list ₹4097, launch ₹2297

### Questions
- 50: list ₹75, launch ₹40
- 100: list ₹149, launch ₹79
- 150: list ₹224, launch ₹119
- 200: list ₹298, launch ₹158
- 250: list ₹373, launch ₹198
- 300: list ₹447, launch ₹237
- 350: list ₹499, launch ₹249
- 500: list ₹499, launch ₹249

## Metering Rules

- Zettel entitlement is checked before `/api/zettels/add` starts extraction or Gemini work.
- Kasten entitlement is checked before `POST /api/rag/sandboxes` creates a sandbox.
- RAG question entitlement is checked before retrieval, rerank, or Gemini answer work starts.
- Subscription quota is consumed before one-time credit packs.
- One-time packs act as overflow credits after included subscription quota is exhausted.
- Quota-blocked UI must preserve the interrupted action and resume it automatically after verified purchase.

## UX Rules

- Every quota prompt must offer a direct purchase path.
- "Watch an ad" is a disabled coming-soon action in this release and grants no credits.
- No flow may ask the user to manually retry after successful purchase.
- Known surfaces include Home Add Zettel, My Zettels Add Zettel, Home Kasten creation, My Kastens creation, Knowledge Graph Kasten modal, and User RAG questions.
