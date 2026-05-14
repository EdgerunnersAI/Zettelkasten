You are an INDEPENDENT rubric reviewer, blind to any prior evaluator's scoring. Do NOT read eval.json.

Stamp `eval_json_hash_at_review: "NOT_CONSULTED"` at the top of your manual_review.md.

RUBRIC:
version: rubric_universal.v1
source_type: universal
composite_max_points: 100
components:
- id: brief_summary
  max_points: 25
  criteria:
  - id: brief.what_this_is
    description: 'Brief answers: what is this source?'
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: brief.main_topic
    description: 'Brief answers: what is it about?'
    max_points: 5
    maps_to_metric:
    - finesure.completeness
    - finesure.completeness
  - id: brief.major_units
    description: Brief outlines the major structural units of the source.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: brief.distinctive_signal
    description: Brief conveys what is distinctive / noteworthy.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: brief.length_5_to_7_sentences
    description: Brief is 5-7 sentences.
    max_points: 2
    maps_to_metric:
    - finesure.conciseness
  - id: brief.no_fabrication
    description: No invented facts, interfaces, or conclusions.
    max_points: 2
    maps_to_metric:
    - finesure.faithfulness
    - summac
- id: detailed_summary
  max_points: 45
  criteria:
  - id: detailed.one_bullet_per_unit
    description: One bullet per major source unit, no omissions.
    max_points: 18
    maps_to_metric:
    - finesure.completeness
  - id: detailed.no_invented_content
    description: No unsupported content added.
    max_points: 10
    maps_to_metric:
    - finesure.faithfulness
    - summac
  - id: detailed.logical_order
    description: Bullets follow logical order of source.
    max_points: 8
    maps_to_metric:
    - g_eval.coherence
  - id: detailed.bullets_focused
    description: Each bullet covers one coherent aspect.
    max_points: 5
    maps_to_metric:
    - g_eval.coherence
  - id: detailed.bullets_specific
    description: Bullets are specific, not generic paraphrase.
    max_points: 4
    maps_to_metric:
    - finesure.conciseness
- id: tags
  max_points: 15
  criteria:
  - id: tags.count_7_to_10
    description: Exactly 7-10 tags.
    max_points: 3
    maps_to_metric:
    - finesure.conciseness
  - id: tags.topical_specificity
    description: Tags are specific, retrieval-friendly.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: tags.source_type_marker
    description: A source-type marker tag present.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.no_unsupported
    description: No tags imply content not in source.
    max_points: 4
    maps_to_metric:
    - finesure.faithfulness
- id: label
  max_points: 15
  criteria:
  - id: label.fast_identifier
    description: Label is the fastest reliable identifier for the source.
    max_points: 8
    maps_to_metric:
    - finesure.completeness
  - id: label.makes_sense_alone
    description: Label makes sense when seen alone in a note list.
    max_points: 7
    maps_to_metric:
    - finesure.completeness
    - finesure.conciseness
anti_patterns:
- id: invented_fact
  description: Any invented fact, interface, person, or conclusion.
  auto_cap: 60
- id: missing_primary_unit
  description: Primary thesis/purpose/question/central unit missing.
  auto_cap: 75
- id: generic_tags_or_ambiguous_label
  description: Generic tags OR ambiguous label.
  auto_cap: 90
global_rules:
  editorialization_penalty:
    threshold_flags: 3


SUMMARY:
## URL 1: https://twitter.com/jack/status/20

### SUMMARY
```yaml
mini_title: Jack Dorsey's First Tweet
brief_summary: 'On March 21, 2006, the user ''jack'' (@jack) posted the message: ''just
  setting up my twttr''.'
tags:
- twitter
- first-tweet
- jack-dorsey
- social-media-history
detailed_summary:
- heading: Tweet Details
  bullets:
  - 'Date of post: March 21, 2006'
  - 'User who posted: ''jack'' (@jack)'
  - 'Content of the tweet: ''just setting up my twttr'''
  sub_sections: {}
metadata:
  source_type: twitter
  url: https://twitter.com/jack/status/20
  author: null
  date: null
  extraction_confidence: medium
  confidence_reason: tweet text extracted
  total_tokens_used: 509
  gemini_pro_tokens: 0
  gemini_flash_tokens: 509
  total_latency_ms: 30438
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: Jack Dorsey's First Tweet
    brief_summary: 'On March 21, 2006, the user ''jack'' (@jack) posted the message:
      ''just setting up my twttr''.'
    tags:
    - Twitter
    - First Tweet
    - Jack Dorsey
    - Social Media History
    detailed_summary:
    - heading: Tweet Details
      bullets:
      - 'Date of post: March 21, 2006'
      - 'User who posted: ''jack'' (@jack)'
      - 'Content of the tweet: ''just setting up my twttr'''
      sub_sections: {}
    route_subtype: status
    route_supported: true
  is_schema_fallback: false
  model_used:
  - role: dense_verify
    model: gemini-2.5-pro
    starting_model: gemini-2.5-pro
    fallback_reason: gemini-2.5-pro-rate-limited
  - role: summarizer
    model: gemini-2.5-flash
    starting_model: gemini-2.5-flash
    fallback_reason: gemini-2.5-flash-rate-limited
  fallback_reason: gemini-2.5-pro-rate-limited

```

### ATOMIC FACTS
```yaml
- claim: Jack Dorsey posted a tweet with the content "just setting up my twttr" on
    March 21, 2006.
  importance: 5
- claim: The content of the tweet posted by Jack Dorsey was "just setting up my twttr".
  importance: 4
- claim: Jack Dorsey posted a tweet on March 21, 2006.
  importance: 4
- claim: The tweet was posted on March 21, 2006.
  importance: 3
- claim: Jack Dorsey is the user associated with the Twitter handle @jack.
  importance: 3

```

### SOURCE
```
Tweet
just setting up my twttr — jack (@jack) March 21, 2006
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): 1de281c15e68204cded2afb0be449faa86c3d908d838b9e1ca111ef3a25a05bc
