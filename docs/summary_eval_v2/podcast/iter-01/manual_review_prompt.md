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
## URL 1: https://podcasts.apple.com/us/podcast/acquired/id1050462261

### SUMMARY
```yaml
mini_title: Acquired
brief_summary: "The 'Acquired' podcast, created by Ben Gilbert and David Rosenthal,\
  \ ran from 2015 to 2026, producing 213 episodes. Its premise is to teach listeners\
  \ the playbooks that built the world\u2019s greatest companies and how to apply\
  \ them. The show has a 'Clean' rating and is copyrighted in 2025 by ACQ, LLC."
tags:
- podcast
- business
- company-playbooks
- acquired
detailed_summary:
- heading: Show Overview
  bullets:
  - 'Title: Acquired'
  - 'Creators: Ben Gilbert and David Rosenthal'
  - 'Active Period: 2015 to 2026'
  - 'Total Episodes: 213'
  - 'Rating: Clean'
  - 'Copyright: 2025 by ACQ, LLC'
  - 'Website: A show website is mentioned'
  sub_sections: {}
- heading: Show Premise
  bullets:
  - "Teaches listeners the 'playbooks that built the world\u2019s greatest companies'."
  - Focuses on how to apply these playbooks.
  sub_sections: {}
metadata:
  source_type: podcast
  url: https://podcasts.apple.com/us/podcast/acquired/id1050462261
  author: null
  date: null
  extraction_confidence: medium
  confidence_reason: podcast page show notes extracted
  total_tokens_used: 641
  gemini_pro_tokens: 0
  gemini_flash_tokens: 641
  total_latency_ms: 22585
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: Acquired
    brief_summary: "The 'Acquired' podcast, created by Ben Gilbert and David Rosenthal,\
      \ ran from 2015 to 2026, producing 213 episodes. Its premise is to teach listeners\
      \ the playbooks that built the world\u2019s greatest companies and how to apply\
      \ them. The show has a 'Clean' rating and is copyrighted in 2025 by ACQ, LLC."
    tags:
    - Podcast
    - Business
    - Company Playbooks
    - Acquired
    detailed_summary:
    - heading: Show Overview
      bullets:
      - 'Title: Acquired'
      - 'Creators: Ben Gilbert and David Rosenthal'
      - 'Active Period: 2015 to 2026'
      - 'Total Episodes: 213'
      - 'Rating: Clean'
      - 'Copyright: 2025 by ACQ, LLC'
      - 'Website: A show website is mentioned'
      sub_sections: {}
    - heading: Show Premise
      bullets:
      - "Teaches listeners the 'playbooks that built the world\u2019s greatest companies'."
      - Focuses on how to apply these playbooks.
      sub_sections: {}
    route_subtype: episode
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
- claim: The show is hosted by Ben Gilbert and David Rosenthal.
  importance: 5
- claim: "The show's purpose is to teach listeners the playbooks that built the world\u2019\
    s greatest companies and how to apply them."
  importance: 5
- claim: The show's core premise or tagline is 'Every company has a story.'
  importance: 4
- claim: The show was active from 2015 to 2026.
  importance: 3
- claim: The show has 213 episodes.
  importance: 3
- claim: The show has a 'Clean' content rating.
  importance: 2
- claim: The show is copyrighted by ACQ, LLC in 2025.
  importance: 1

```

### SOURCE
```
Acquired Ben Gilbert and David Rosenthal Every company has a story. Learn the playbooks that built the world’s greatest companies — and how you can apply them. Trailer Hosts & Guests About Every company has a story. Learn the playbooks that built the world’s greatest companies — and how you can apply them. Information - CreatorBen Gilbert and David Rosenthal - Years Active2015 - 2026 - Episodes213 - RatingClean - Copyright© Copyright 2025 ACQ, LLC - Show Website
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): 97b138216f6b57565d381267904491d83a96bea69f3444b5e8ddcd8946b02b81
