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
## URL 1: https://www.linkedin.com/company/openai/

### SUMMARY
```yaml
mini_title: OpenAI's Recent Strategic and Product Updates
brief_summary: OpenAI has announced significant developments, including a rebuilt
  voice infrastructure with new GPT-Realtime models, the formation of the OpenAI Deployment
  Company via the acquisition of Tomoro and a multi-firm partnership, and new features
  like 'Trusted Contact' for user safety and 'Daybreak' for cyber defense. The company
  also released ChatGPT Images 2.0 and provided insights into enterprise AI adoption
  and the mainstreaming of ChatGPT for various tasks.
tags:
- openai
- ai-research
- voice-ai
- chatgpt
- enterprise-solutions
- business-partnerships
- acquisitions
- ai-safety
- cyber-security
- image-generation
detailed_summary:
- heading: Product & Feature Announcements
  bullets:
  - Rebuilt voice infrastructure for ChatGPT and the Realtime API.
  - Launched GPT-Realtime-2 in the API, a voice model with GPT-5-class reasoning.
  - Introduced streaming models GPT-Realtime-Translate and GPT-Realtime-Whisper.
  - Launched 'Trusted Contact' in ChatGPT, an optional safety feature for eligible
    adult users to notify a trusted person during a self-harm crisis, developed with
    input from the American Psychological Association.
  - Introduced 'Daybreak' for cyber defense, utilizing OpenAI models and Codex with
    security partners.
  - Released ChatGPT Images 2.0, an image model featuring web search and self-checking
    capabilities.
  sub_sections: {}
- heading: Business Expansion & Strategic Partnerships
  bullets:
  - Launched the OpenAI Deployment Company to embed engineers in businesses.
  - Initiated this venture with the acquisition of Tomoro, adding approximately 150
    Forward Deployed Engineers and Deployment Specialists.
  - The new company is a partnership with 19 firms, majority-owned by OpenAI.
  - TPG serves as the lead partner.
  - Advent, Bain Capital, and Brookfield are co-lead founding partners.
  - B Capital, BBVA, Emergence Capital, Goanna, Goldman Sachs, SoftBank Corp., Warburg
    Pincus LLC, and WCAS are founding partners.
  - Bain & Company, Capgemini, and McKinsey & Company are investors.
  sub_sections: {}
- heading: Company Context & AI Adoption Trends
  bullets:
  - OpenAI operates as a partnership focused on ensuring artificial general intelligence
    (AGI) benefits humanity safely.
  - 'Company details: 1,001-5,000 employees, headquartered in San Francisco, CA.'
  - The B2B Signals quarterly research series tracks enterprise AI adoption.
  - Frontier firms demonstrate 16x higher Codex usage in agentic workflows.
  - By early 2026, ChatGPT adoption became more mainstream across demographics for
    recurring tasks.
  - The 'ChatGPT Futures Class of 2026' showcases 26 students who used the tool throughout
    university for projects like mapping space objects and preserving languages.
  sub_sections: {}
metadata:
  source_type: linkedin
  url: https://www.linkedin.com/company/openai/
  author: null
  date: null
  extraction_confidence: low
  confidence_reason: LinkedIn login wall detected
  total_tokens_used: 1425
  gemini_pro_tokens: 0
  gemini_flash_tokens: 1425
  total_latency_ms: 39511
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: OpenAI's Recent Strategic and Product Updates
    brief_summary: OpenAI has announced significant developments, including a rebuilt
      voice infrastructure with new GPT-Realtime models, the formation of the OpenAI
      Deployment Company via the acquisition of Tomoro and a multi-firm partnership,
      and new features like 'Trusted Contact' for user safety and 'Daybreak' for cyber
      defense. The company also released ChatGPT Images 2.0 and provided insights
      into enterprise AI adoption and the mainstreaming of ChatGPT for various tasks.
    tags:
    - OpenAI
    - AI Research
    - Voice AI
    - ChatGPT
    - Enterprise Solutions
    - Business Partnerships
    - Acquisitions
    - AI Safety
    - Cyber Security
    - Image Generation
    - AI Adoption
    detailed_summary:
    - heading: Product & Feature Announcements
      bullets:
      - Rebuilt voice infrastructure for ChatGPT and the Realtime API.
      - Launched GPT-Realtime-2 in the API, a voice model with GPT-5-class reasoning.
      - Introduced streaming models GPT-Realtime-Translate and GPT-Realtime-Whisper.
      - Launched 'Trusted Contact' in ChatGPT, an optional safety feature for eligible
        adult users to notify a trusted person during a self-harm crisis, developed
        with input from the American Psychological Association.
      - Introduced 'Daybreak' for cyber defense, utilizing OpenAI models and Codex
        with security partners.
      - Released ChatGPT Images 2.0, an image model featuring web search and self-checking
        capabilities.
      sub_sections: {}
    - heading: Business Expansion & Strategic Partnerships
      bullets:
      - Launched the OpenAI Deployment Company to embed engineers in businesses.
      - Initiated this venture with the acquisition of Tomoro, adding approximately
        150 Forward Deployed Engineers and Deployment Specialists.
      - The new company is a partnership with 19 firms, majority-owned by OpenAI.
      - TPG serves as the lead partner.
      - Advent, Bain Capital, and Brookfield are co-lead founding partners.
      - B Capital, BBVA, Emergence Capital, Goanna, Goldman Sachs, SoftBank Corp.,
        Warburg Pincus LLC, and WCAS are founding partners.
      - Bain & Company, Capgemini, and McKinsey & Company are investors.
      sub_sections: {}
    - heading: Company Context & AI Adoption Trends
      bullets:
      - OpenAI operates as a partnership focused on ensuring artificial general intelligence
        (AGI) benefits humanity safely.
      - 'Company details: 1,001-5,000 employees, headquartered in San Francisco, CA.'
      - The B2B Signals quarterly research series tracks enterprise AI adoption.
      - Frontier firms demonstrate 16x higher Codex usage in agentic workflows.
      - By early 2026, ChatGPT adoption became more mainstream across demographics
        for recurring tasks.
      - The 'ChatGPT Futures Class of 2026' showcases 26 students who used the tool
        throughout university for projects like mapping space objects and preserving
        languages.
      sub_sections: {}
    route_subtype: company
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
- claim: OpenAI is an AI research and deployment company.
  importance: 5
- claim: OpenAI is dedicated to ensuring that general-purpose artificial intelligence
    benefits all of humanity.
  importance: 5
- claim: "OpenAI is dedicated to putting the alignment of interests first \u2014 ahead\
    \ of profit."
  importance: 5
- claim: OpenAI is launching the OpenAI Deployment Company to help businesses build
    around intelligence.
  importance: 5
- claim: "The OpenAI Deployment Company will extend OpenAI\u2019s ability to embed\
    \ engineers specialized in frontier AI deployment directly inside businesses tackling\
    \ complex problems in demanding environments."
  importance: 5
- claim: OpenAI is introducing GPT-Realtime-2 in the API, its most intelligent voice
    model yet.
  importance: 5
- claim: GPT-Realtime-2 brings GPT-5-class reasoning to voice agents.
  importance: 5
- claim: OpenAI rebuilt parts of its voice infrastructure to make ChatGPT voice and
    the Realtime API faster and more reliable for people around the world.
  importance: 4
- claim: AI is an extremely powerful tool that must be created with safety and human
    needs at its core.
  importance: 4
- claim: OpenAI believes artificial intelligence has the potential to help people
    solve immense global challenges.
  importance: 4
- claim: OpenAI wants the upside of AI to be widely shared.
  importance: 4
- claim: ChatGPT adoption broadened in early 2026, becoming a more mainstream tool,
    used by a broader mix of people, in more countries, and for increasingly recurring
    tasks.
  importance: 4
- claim: Daybreak is frontier AI for cyber defenders.
  importance: 4
- claim: Daybreak brings together the most capable OpenAI models, Codex, and security
    partners to accelerate cyber defense and continuously secure software.
  importance: 4
- claim: ChatGPT Images 2.0 is OpenAI's first image model with thinking capabilities.
  importance: 4
- claim: ChatGPT Images 2.0 enables searching the web for real-time information, creating
    multiple distinct images from one prompt, and double-checking its own outputs.
  importance: 4
- claim: OpenAI has agreed to acquire Tomoro, which will bring approximately 150 experienced
    Forward Deployed Engineers and Deployment Specialists from day one to the OpenAI
    Deployment Company.
  importance: 4
- claim: The OpenAI Deployment Company is a committed partnership between OpenAI and
    19 leading global investment firms, consultancies, and system integrators.
  importance: 4
- claim: OpenAI is rolling out Trusted Contact in ChatGPT, a new optional safety feature.
  importance: 4
- claim: Trusted Contact helps eligible users connect with someone they trust during
    moments of emotional crisis.
  importance: 4
- claim: Trusted Contact allows adult users to choose one trusted person ahead of
    time who may be notified if OpenAI's systems detect signs of a serious safety
    concern involving self-harm.
  importance: 4
- claim: Voice agents are now real-time collaborators that can listen, reason, and
    solve complex problems as conversations unfold due to GPT-Realtime-2.
  importance: 4
- claim: Enterprises are building an AI advantage through agentic workflows, with
    Codex usage 16x higher at frontier firms.
  importance: 4
- claim: Students from the ChatGPT Futures Class of 2026 are using AI in remarkable
    ways, such as mapping unknown objects in space, detecting disaster survivors,
    and improving healthcare.
  importance: 4
- claim: OpenAI's investment in diversity, equity, and inclusion is ongoing, executed
    through a wide range of initiatives, and championed and supported by leadership.
  importance: 3
- claim: OpenAI's specialties include artificial intelligence and machine learning.
  importance: 3
- claim: Codex allows users to bring projects, docs, slides, files, and recent work
    history into it to get work done faster without coding.
  importance: 3
- claim: "Trusted Contact was developed with input from experts from the American\
    \ Psychological Association, OpenAI\u2019s Global Physician\u2019s Network, and\
    \ its Expert Council on Well-Being and AI."
  importance: 3
- claim: Enterprise AI adoption is moving into a new phase from access to depth.
  importance: 3
- claim: The ChatGPT Futures Class of 2026 is the first graduating class to have ChatGPT
    throughout all four years of university.
  importance: 3

```

### SOURCE
```
Real-time voice AI only feels natural when conversations move at the speed of speech. We rebuilt parts of our voice infrastructure to make ChatGPT voice and the Realtime API faster and more reliable for people around the world. Here’s how we keep voice interactions feeling responsive at global scale: https://lnkd.in/g4CxdT4H About us OpenAI is an AI research and deployment company dedicated to ensuring that general-purpose artificial intelligence benefits all of humanity. AI is an extremely powerful tool that must be created with safety and human needs at its core. OpenAI is dedicated to putting that alignment of interests first — ahead of profit. To achieve our mission, we must encompass and value the many different perspectives, voices, and experiences that form the full spectrum of humanity. Our investment in diversity, equity, and inclusion is ongoing, executed through a wide range of initiatives, and championed and supported by leadership. At OpenAI, we believe artificial intelligence has the potential to help people solve immense global challenges, and we want the upside of AI to be widely shared. Join us in shaping the future of technology. - Website - https://openai.com/ External link for OpenAI - Industry - Research Services - Company size - 1,001-5,000 employees - Headquarters - San Francisco, CA - Type - Partnership - Specialties - artificial intelligence and machine learning Locations - Primary Get directions San Francisco, CA 94110, US Employees at OpenAI Updates - ChatGPT adoption broadened in early 2026, becoming a more mainstream tool, used by a broader mix of people, in more countries, and for increasingly recurring tasks. We take a look at how usage is expanding across age, gender, and geography, and how people are turning to ChatGPT for more specialized, repeatable work. https://lnkd.in/gyPWzaMn - Introducing Daybreak: frontier AI for cyber defenders. Daybreak brings together the most capable OpenAI models, Codex, and our security partners to accelerate cyber defense and continuously secure software. A step toward a future where security teams can move at the speed that defense demands. https://lnkd.in/gqaWjuR3 - OpenAI reposted this ChatGPT Images 2.0 is our first image model with thinking capabilities, which enables it to search the web for real-time information, create multiple distinct images from one prompt, and double-check its own outputs. We’re sharing tips and five examples of how professionals are experimenting with this new model in their fields. Read more about how pro users are getting the most out of ChatGPT here: https://lnkd.in/gN3egEkS - Today we’re launching the OpenAI Deployment Company to help businesses build around intelligence. Successful AI deployment is about empowering people and teams to do more. The OpenAI Deployment Company will extend OpenAI’s ability to embed engineers specialized in frontier AI deployment directly inside businesses tackling complex problems in demanding environments. In connection with the launch, OpenAI has agreed to acquire Tomoro, which will bring approximately 150 experienced Forward Deployed Engineers and Deployment Specialists from day one. As models become more capable, businesses can apply AI to larger, more important parts of how they operate. The work now is helping organizations rethink critical workflows around intelligence that can reason, act, and deliver measurable results. The OpenAI Deployment Company is a committed partnership between OpenAI and 19 leading global investment firms, consultancies, and system integrators. It is majority-owned and controlled by OpenAI, giving customers a unified experience whether they work directly with OpenAI, the deployment team, or both. The partnership is led by TPG, with Advent, Bain Capital, and Brookfield as co-lead founding partners, and B Capital, BBVA, Emergence Capital, Goanna, Goldman Sachs, SoftBank Corp., Warburg Pincus LLC, and WCAS as founding partners. Investors also include Bain & Company, Capgemini, and McKinsey & Company. https://lnkd.in/gbvh8TRg - OpenAI reposted this Bring your projects, docs, slides, files, and recent work history into Codex and get work done faster—no coding required. https://lnkd.in/gYMRpfpS - We’re rolling out Trusted Contact in ChatGPT, a new optional safety feature that helps eligible users connect with someone they trust during moments of emotional crisis. Trusted Contact was developed with input from experts from the American Psychological Association, OpenAI’s Global Physician’s Network, and our Expert Council on Well-Being and AI. It allows adult users to choose one trusted person ahead of time who may be notified if our systems detect signs of a serious safety concern involving self-harm. Trusted Contact is designed to: • Encourage real-world connection and support • Preserve user choice and privacy • Complement crisis resources and professional care Trusted Contact is part of our ongoing work to strengthen how ChatGPT responds in sensitive situations and help connect people with the support, relationships, and resources that matter most. https://lnkd.in/gKakRMxw - Introducing GPT-Realtime-2 in the API: our most intelligent voice model yet, bringing GPT-5-class reasoning to voice agents. Voice agents are now real-time collaborators that can listen, reason, and solve complex problems as conversations unfold. Now available in the API alongside streaming models GPT-Realtime-Translate and GPT-Realtime-Whisper — a new set of audio capabilities for the next generation of voice interfaces. https://lnkd.in/gdQ_pRir - Enterprise AI adoption is moving into a new phase from access to depth, and B2B Signals is our recurring research series tracking how that shift is happening across businesses. A few signals from the first release: ✅ Enterprises are building an AI advantage through agentic workflows, with Codex usage 16x higher at frontier firms ✅ Leading firms are using AI to help employees build skills and confidence, not just complete tasks faster. ✅ AI use is moving from general productivity to becoming more specialized across business functions B2B Signals will track these patterns quarterly as enterprise AI adoption continues to evolve. https://lnkd.in/e9pAG-nq - Meet the ChatGPT Futures Class of 2026. As the first graduating class to have ChatGPT throughout all four years of university, they’re already showing what’s possible with AI in their hands and changing the world around them. These 26 students and recent graduates are using AI in remarkable ways: mapping unknown objects in space, detecting disaster survivors, preserving endangered languages, improving healthcare, expanding access to education, preventing scams, reducing waste, and more. They represent a new generation turning bold ideas into real-world impact. ChatGPT.com/Futures
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): f82b3f8e676cf0a1e3327197a1af338cf149b659ba89721c004659dc2ca4f4a2
