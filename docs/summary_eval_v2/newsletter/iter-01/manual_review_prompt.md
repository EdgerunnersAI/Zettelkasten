You are an INDEPENDENT rubric reviewer, blind to any prior evaluator's scoring. Do NOT read eval.json.

Stamp `eval_json_hash_at_review: "NOT_CONSULTED"` at the top of your manual_review.md.

RUBRIC:
version: rubric_newsletter.v1
source_type: newsletter
composite_max_points: 100
components:
- id: brief_summary
  max_points: 25
  criteria:
  - id: brief.main_topic_thesis
    description: Brief states main topic or thesis in one sentence.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
    - finesure.completeness
  - id: brief.argument_structure
    description: Brief summarizes how the author structures their argument.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
    - g_eval.coherence
  - id: brief.key_evidence
    description: Important evidence or examples are captured without invention.
    max_points: 5
    maps_to_metric:
    - finesure.faithfulness
    - qafact
  - id: brief.conclusions_distinct
    description: Author's conclusions/recommendations distinguished from background.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
  - id: brief.caveats_addressed
    description: If explicit caveats or counterarguments present, how author addresses
      them is summarized.
    max_points: 5
    maps_to_metric:
    - finesure.faithfulness
    - finesure.completeness
  - id: brief.stance_preserved
    description: 'Tone reflects author''s stance without editorializing. Retired 2026-05-13:
      coverage moved to editorialization_penalty + W4 guards.'
    max_points: 0
    maps_to_metric:
    - summac
- id: detailed_summary
  max_points: 45
  criteria:
  - id: detailed.sections_ordered
    description: Bullets represent major sections/argumentative steps in logical order.
    max_points: 8
    maps_to_metric:
    - g_eval.coherence
    - finesure.completeness
  - id: detailed.claims_source_grounded
    description: Claims in bullets are grounded in the source; no new claims.
    max_points: 8
    maps_to_metric:
    - finesure.faithfulness
    - summac
  - id: detailed.examples_captured
    description: Notable examples, case studies, data anchors are captured.
    max_points: 7
    maps_to_metric:
    - finesure.completeness
  - id: detailed.action_items
    description: Explicit action items / practical takeaways bulleted if present.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: detailed.multiple_scenarios
    description: If multiple viewpoints/scenarios discussed, each gets a bullet.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: detailed.no_footer_padding
    description: Unsubscribe language, house style, boilerplate not given bullets.
    max_points: 5
    maps_to_metric:
    - finesure.conciseness
  - id: detailed.bullets_specific
    description: Bullets concise yet specific; no vague paraphrase.
    max_points: 5
    maps_to_metric:
    - finesure.conciseness
- id: tags
  max_points: 15
  criteria:
  - id: tags.count_7_to_10
    description: Exactly 7-10 tags.
    max_points: 2
    maps_to_metric:
    - finesure.conciseness
  - id: tags.domain_subdomain
    description: Main domain and subdomain tagged.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.key_concepts
    description: Key concepts or frameworks introduced in the piece tagged.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.type_intent
    description: Piece type tagged (opinion, research-summary, how-to, case-study).
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.no_stance_misrepresentation
    description: Tags don't misrepresent stance (no 'bullish-call' on neutral piece).
    max_points: 4
    maps_to_metric:
    - finesure.faithfulness
- id: label
  max_points: 15
  criteria:
  - id: label.compact_declarative
    description: Label is a compact, declarative phrase reflecting main thesis.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: label.branded_source_rule
    description: For branded sources (Stratechery/Platformer/etc.), label includes
      publication name.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: label.informative_not_catchy
    description: Informative over catchy; obvious what the Zettel is about.
    max_points: 4
    maps_to_metric:
    - finesure.conciseness
anti_patterns:
- id: stance_mismatch
  description: Summary's implied stance differs from source's detected stance.
  auto_cap: 60
- id: invented_number
  description: Summary cites a number, date, or source not present in the newsletter.
  auto_cap: 60
- id: branded_source_missing_publication
  description: Branded newsletter label missing publication name.
  auto_cap: 90
global_rules:
  editorialization_penalty:
    threshold_flags: 3


SUMMARY:
## URL 1: https://newsletter.pragmaticengineer.com/p/the-product-minded-engineer

### SUMMARY
```yaml
mini_title: 'The Pragmatic Engineer: The Rise of Product Engineers'
brief_summary: The Pragmatic Engineer newsletter reports on the increasing demand
  for "product engineers," a role combining product and engineering skills, a trend
  accelerated by AI. It introduces "The Product-Minded Engineer" by Drew Hoskins,
  an O'Reilly book, through an interview with the author. The issue includes an excerpt
  from Chapter 3, which details effective error and warning message strategies. Hoskins
  advises engineers to ask "why" and engage in customer support to become more product-minded.
tags:
- pragmatic-engineer
- product-engineering
- software-development
- error-handling
- developer-experience
- ai-impact
- career-advice
- book-summary
- interview
- analysis
detailed_summary:
  publication_identity: Pragmatic Engineer
  issue_thesis: "The newsletter highlights the increasing demand for 'product engineers'\u2014\
    individuals who possess both product and engineering skills\u2014a trend significantly\
    \ accelerated by the advent of AI."
  sections:
  - heading: Introducing 'The Product-Minded Engineer' and Author Drew Hoskins
    bullets:
    - "The newsletter introduces the book \u201CThe Product-Minded Engineer\u201D\
      \ by Drew Hoskins, published by O\u2019Reilly, as a guide for this emerging\
      \ role."
    - "Drew Hoskins, the author, has a background as a software engineer at Microsoft\
      \ (2002\u20132009), Facebook (2009\u20132015), Oculus (2015-2017), and Stripe\
      \ (2018\u20132023), and is currently a Staff Product Manager at Temporal."
    - In an interview, Hoskins reveals the book took 18 months to write, a process
      that began after he joined Temporal.
    - He states that the book was inspired by O'Reilly's suggestion to focus on product-thinking
      rather than solely API design.
    - Hoskins identifies John Carmack as the best product-minded engineer he has worked
      with during his career.
  - heading: Cultivating Product-Mindedness and AI's Contribution
    bullets:
    - For engineers seeking to become more product-minded, Hoskins advises consistently
      asking "why" behind tasks and features.
    - He recommends switching between system and user viewpoints to gain a understanding
      of product impact.
    - Using scenarios and actively participating in customer support are also suggested
      as effective methods to develop product intuition.
    - Hoskins suggests that AI tools can significantly assist this process by aggregating
      user signals from various sources.
    - These sources include platforms like Slack, Miro, and GitHub, enabling a more
      holistic view of user needs and feedback.
  - heading: "Excerpt: Chapter 3 \u2013 'Errors and Warnings' on High-Leverage Diagnostics"
    bullets:
    - "The newsletter includes an excerpt from Chapter 3, \u201CErrors and Warnings,\u201D\
      \ which argues that diagnostics serve as a high-leverage, primary interface\
      \ for many products."
    - This point is underscored by the observation that autonomous agents frequently
      fail due to unhelpful or unclear error messages.
    - "The excerpt advises categorizing errors into five distinct types: System, User\u2019\
      s Invalid Argument, Precondition, Developer\u2019s Invalid Argument, and Assertion."
    - This categorization allows for tailoring messages to different personas, such
      as end-users versus developers or administrators versus regular users, to provide
      relevant information.
    - Using a fictional SaaS company, "Channelz," the text illustrates how to craft
      actionable error messages that provide context and suggest solutions, like correcting
      a channel prefix from '@' to '#'.
    - The chapter advocates for raising errors at the API or UI interface, where both
      user intent and system state are known, utilizing techniques like upfront validation
      or repackaging lower-level errors with more context, similar to Python's chained
      exceptions.
    - "It strongly promotes \"shifting left\"\u2014diagnosing errors as early as possible\u2014\
      through four techniques: static validations (e.g., checksums), upfront validation\
      \ in multi-step workflows, providing test environments or \"fakes\" (citing\
      \ Stripe's test mode with special card numbers like 4000 0000 0000 9995 to simulate\
      \ failures), and requesting user confirmations for ambiguous inputs (e.g., Google's\
      \ \"Did you mean\")."
  conclusions_or_recommendations:
  - Engineers should cultivate product-mindedness by asking 'why,' switching between
    system and user viewpoints, using scenarios, and engaging in customer support.
  - AI tools can aid product-mindedness by aggregating user signals from various platforms.
  - "Products should treat diagnostics as a primary interface, categorizing errors\
    \ into five types (System, User\u2019s Invalid Argument, Precondition, Developer\u2019\
    s Invalid Argument, Assertion) to tailor messages for different personas."
  - Error messages should be actionable, providing context and suggesting solutions.
  - Errors should be raised at the API or UI interface, leveraging upfront validation
    or repackaging lower-level errors.
  - Adopt 'shifting left' techniques for error diagnosis, including static validations,
    upfront validation in workflows, providing test environments/fakes, and requesting
    user confirmations for ambiguous inputs.
  stance: mixed
  cta: The newsletter author, Gergely Orosz, endorses the book and notes Hoskins will
    speak at The Pragmatic Summit in San Francisco on February 11. The article also
    contains a hiring announcement for a research role at The Pragmatic Engineer,
    with applications closing January 26.
  caveats: []
metadata:
  source_type: newsletter
  url: https://newsletter.pragmaticengineer.com/p/the-product-minded-engineer
  author: null
  date: null
  extraction_confidence: high
  confidence_reason: HTML article text extracted via direct
  total_tokens_used: 2603
  gemini_pro_tokens: 0
  gemini_flash_tokens: 2603
  total_latency_ms: 56245
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: 'The Pragmatic Engineer: The Rise of Product Engineers'
    brief_summary: The Pragmatic Engineer newsletter reports on the increasing demand
      for "product engineers," a role combining product and engineering skills, a
      trend accelerated by AI. It introduces "The Product-Minded Engineer" by Drew
      Hoskins, an O'Reilly book, through an interview with the author. The issue includes
      an excerpt from Chapter 3, which details effective error and warning message
      strategies. Hoskins advises engineers to ask "why" and engage in customer support
      to become more product-minded. The newsletter concludes with an endorsement
      of the book and an announcement for The Pragmatic Summit.
    tags:
    - product-engineering
    - software-development
    - error-handling
    - developer-experience
    - ai-impact
    - career-advice
    - book-summary
    - interview
    - analysis
    - opinion
    detailed_summary:
      publication_identity: Pragmatic Engineer
      issue_thesis: "The newsletter highlights the increasing demand for 'product\
        \ engineers'\u2014individuals who possess both product and engineering skills\u2014\
        a trend significantly accelerated by the advent of AI."
      sections:
      - heading: Introducing 'The Product-Minded Engineer' and Author Drew Hoskins
        bullets:
        - "The newsletter introduces the book \u201CThe Product-Minded Engineer\u201D\
          \ by Drew Hoskins, published by O\u2019Reilly, as a guide for this emerging\
          \ role."
        - "Drew Hoskins, the author, has a background as a software engineer at Microsoft\
          \ (2002\u20132009), Facebook (2009\u20132015), Oculus (2015-2017), and Stripe\
          \ (2018\u20132023), and is currently a Staff Product Manager at Temporal."
        - In an interview, Hoskins reveals the book took 18 months to write, a process
          that began after he joined Temporal.
        - He states that the book was inspired by O'Reilly's suggestion to focus on
          product-thinking rather than solely API design.
        - Hoskins identifies John Carmack as the best product-minded engineer he has
          worked with during his career.
      - heading: Cultivating Product-Mindedness and AI's Contribution
        bullets:
        - For engineers seeking to become more product-minded, Hoskins advises consistently
          asking "why" behind tasks and features.
        - He recommends switching between system and user viewpoints to gain a understanding
          of product impact.
        - Using scenarios and actively participating in customer support are also
          suggested as effective methods to develop product intuition.
        - Hoskins suggests that AI tools can significantly assist this process by
          aggregating user signals from various sources.
        - These sources include platforms like Slack, Miro, and GitHub, enabling a
          more holistic view of user needs and feedback.
      - heading: "Excerpt: Chapter 3 \u2013 'Errors and Warnings' on High-Leverage\
          \ Diagnostics"
        bullets:
        - "The newsletter includes an excerpt from Chapter 3, \u201CErrors and Warnings,\u201D\
          \ which argues that diagnostics serve as a high-leverage, primary interface\
          \ for many products."
        - This point is underscored by the observation that autonomous agents frequently
          fail due to unhelpful or unclear error messages.
        - "The excerpt advises categorizing errors into five distinct types: System,\
          \ User\u2019s Invalid Argument, Precondition, Developer\u2019s Invalid Argument,\
          \ and Assertion."
        - This categorization allows for tailoring messages to different personas,
          such as end-users versus developers or administrators versus regular users,
          to provide relevant information.
        - Using a fictional SaaS company, "Channelz," the text illustrates how to
          craft actionable error messages that provide context and suggest solutions,
          like correcting a channel prefix from '@' to '#'.
        - The chapter advocates for raising errors at the API or UI interface, where
          both user intent and system state are known, utilizing techniques like upfront
          validation or repackaging lower-level errors with more context, similar
          to Python's chained exceptions.
        - "It strongly promotes \"shifting left\"\u2014diagnosing errors as early\
          \ as possible\u2014through four techniques: static validations (e.g., checksums),\
          \ upfront validation in multi-step workflows, providing test environments\
          \ or \"fakes\" (citing Stripe's test mode with special card numbers like\
          \ 4000 0000 0000 9995 to simulate failures), and requesting user confirmations\
          \ for ambiguous inputs (e.g., Google's \"Did you mean\")."
      conclusions_or_recommendations:
      - Engineers should cultivate product-mindedness by asking 'why,' switching between
        system and user viewpoints, using scenarios, and engaging in customer support.
      - AI tools can aid product-mindedness by aggregating user signals from various
        platforms.
      - "Products should treat diagnostics as a primary interface, categorizing errors\
        \ into five types (System, User\u2019s Invalid Argument, Precondition, Developer\u2019\
        s Invalid Argument, Assertion) to tailor messages for different personas."
      - Error messages should be actionable, providing context and suggesting solutions.
      - Errors should be raised at the API or UI interface, leveraging upfront validation
        or repackaging lower-level errors.
      - Adopt 'shifting left' techniques for error diagnosis, including static validations,
        upfront validation in workflows, providing test environments/fakes, and requesting
        user confirmations for ambiguous inputs.
      stance: mixed
      cta: The newsletter author, Gergely Orosz, endorses the book and notes Hoskins
        will speak at The Pragmatic Summit in San Francisco on February 11. The article
        also contains a hiring announcement for a research role at The Pragmatic Engineer,
        with applications closing January 26.
      caveats: []
    _dense_verify:
      archetype: career_advice
      stance: optimistic
      missing_fact_count: 0
    _guard_audit:
      banned_adjectives_stripped:
      - comprehensive
      unverified_numerals: []
      requires_repair: false
    _shape: deep_dive_technical
    _shape_confidence: 0.65
    _shape_method: tier1_heuristic
    route_subtype: post
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
    fallback_reason: null
  fallback_reason: gemini-2.5-pro-rate-limited

```

### ATOMIC FACTS
```yaml
- claim: Product-minded engineers are increasingly in demand, capable of both implementing
    products and generating strong product/feature ideas, a trend accelerated by AI
    tools.
  importance: 5
- claim: Crafting well-structured diagnostics with useful messages is an incredibly
    valuable and high-leverage way to spend time, as diagnostics often serve as the
    primary interface for users dealing with complex applications.
  importance: 5
- claim: Diagnostics may be the most important interface of your product, especially
    as autonomous agents increasingly rely on helpful error messages to correct their
    actions.
  importance: 5
- claim: To be more product-minded, engineers should frequently ask "why," switch
    between system and user viewpoints, simulate user interactions with scenarios,
    and engage in customer support to identify permanent fixes.
  importance: 4
- claim: Error and warning messages should be actionable, providing users with suggestions
    or instructions on what to do, which saves time and prevents product churn.
  importance: 4
- claim: The best place to raise errors is at the interface between the system and
    the user, combining knowledge of what happened internally with what the user was
    trying to accomplish, rather than deep in the processing code.
  importance: 4
- claim: '"Shifting left" by giving diagnostics to users as early as possible offers
    numerous benefits, including reducing resource usage, protecting code from bad
    inputs, and saving users time by providing quick feedback.'
  importance: 4
- claim: 'When crafting errors, it''s essential to consider two main categories of
    user scenarios: the human one (end-user) and the programmer one (developer), tailoring
    messages to the right persona and circumstance.'
  importance: 3
- claim: Users and developers should never have to understand a system's internal
    implementation details to understand an error message.
  importance: 3
- claim: Drew Hoskins, author of "The Product-Minded Engineer," has over two decades
    of experience as a software engineer at companies like Microsoft, Facebook, and
    Stripe, and is now a product manager at Temporal.
  importance: 3
- claim: AI tools can significantly help developers be more product-minded by gathering
    user signals from various sources like Slack, Miro, GitHub, and Gong, and aggregating
    them into reports.
  importance: 3
- claim: Key aspects for crafting refreshingly useful warnings and errors include
    understanding the scenario/persona, providing context, suggesting actionable steps,
    choosing appropriate error codes, raising errors at the API/UI layer, and shifting
    left.
  importance: 3
- claim: Techniques for shifting left include static validations (cheap checks early),
    upfront validations (before expensive operations), letting users test with fakes,
    and requesting user confirmations for critical actions.
  importance: 3
- claim: For developer services, building "fakes" (high-fidelity versions of production
    systems for testing) helps customers adopt the service faster by allowing them
    to test in local environments.
  importance: 3
- claim: John Carmack is highlighted as an exemplary product-minded engineer due to
    his deep technical expertise combined with a relentless pursuit of important product
    goals, such as mentoring the VR community or bringing major apps to the Oculus
    platform.
  importance: 2
- claim: Engineers can be seen as product-minded and invited to do more product work
    by helping author use cases/scenarios, joining customer calls, and justifying
    their ideas with scenarios.
  importance: 2
- claim: Upfront validations are a technique for raising errors proactively at the
    API boundary, capturing the caller's intent more completely and providing a strong
    basis for great error messages.
  importance: 2
- claim: Repackaging lower-level errors into appropriate forms, often using mechanisms
    like Python's chained exceptions, allows for preserving inner error details while
    providing user-friendly context.
  importance: 2
- claim: The book "The Product-Minded Engineer" by Drew Hoskins, published by O'Reilly,
    originated from an API design outline but pivoted to product-thinking due to O'Reilly's
    interest in those themes.
  importance: 1
- claim: The book "The Product-Minded Engineer" took 18 months to write, with author
    Drew Hoskins writing it himself, utilizing friends and Claude for research, and
    gathering extensive Alpha and Beta feedback.
  importance: 1

```

### SOURCE
```
Article
The Product-Minded Engineer: The importance of good errors and warnings Product engineers are more in demand than ever, but how do you become one? New book, “The Product-Minded Engineer”, offers a guide. An interview with its author and an exclusive excerpt Before we start: I’m hiring! The Pragmatic Engineer is not a typical publication, and so this is also not a typical role. I’m looking for someone to help research and compile Tuesday deepdives like the one on Cursor, on Claude Code, on Stripe, and many others. This position will include directly talking with engineers at interesting companies, researching both public details and details made exclusively available to us, and compiling what we learned into detailed reports. If you’ve worked at startups or Big Tech for a while, would enjoy working full-remote, keeping up with the cutting edge of the industry sounds interesting, and you’d enjoy doing something that can start as part-time: read more and apply here. Applications close Monday, 26 Jan. One trend in tech is that more startups are hiring for “product engineers” or “product-minded engineers”, who can implement products and also come up with strong product and feature ideas, then build them. This trend of engineers’ involvement from the ideas stage through to shipping looks set to accelerate with AI tools generating ever more code. My recent analysis of what happens when AI writes almost all the code mentioned that nimble startups were already recruiting “product engineers” who can create their own work, and act as blends of mini-product manager and software engineer. I said this indicates that being more product-minded could become a baseline at startups because it’s increasingly important to specify what an AI tool should build. But how do you get better at being a product engineer? Obviously, pairing with a product manager, staying close to the business, and finding a mentor who’s a great product engineer are strong options. But if these aren’t all available in your workplace, there’s now a book dedicated to the topic. Entitled “The Product-Minded Engineer”, it’s written by former software engineer and current product manager, Drew Hoskins, and published by O’Reilly: A few years ago, I published an article named “The Product-Minded Software Engineer” which offers tips for software engineers to grow their “product muscle”, and it’s timely that a fellow engineer has invested in writing a guide about this increasingly pertinent subject. After hearing Drew was working on his book, I got in touch, reviewed a draft version, and asked if he’d consider sharing an excerpt in this newsletter. Graciously, both Drew and O’Reilly agreed. Drew will also be a speaker at The Pragmatic Summit in San Francisco next month, on 11 February, discussing tactics for leading product engineering teams in an AI-native environment. In today’s issue, we cover: Author’s background. Twenty years as a software engineer at Microsoft, Facebook, and Stripe – and today as a product manager at Temporal. Writing the book. Why create this guide now? Importance of good errors and warnings at product-level. Excerpt from Chapter 3: “Errors and Warnings”, about why designing the right approach to errors has a massive impact upon products used by developers and nontechnical users, too. My usual disclaimer: as with all my recommendations, I was not paid for this article, and none of the links are affiliates. See my ethics statement for more. 1. Author’s background With experienced tech professionals who cross over into being published writers, I find it’s always useful to understand something about their background, and Drew has an impressive one spanning more than two decades: Microsoft: Software Development Engineer (2002–2009). Worked on the C++ compiler backend, static analysis tools, and the Windows UI developer framework. Facebook: platform/product infra engineer (2009–2015). Worked on the Facebook API and the initial version of the Facebook App Center. Then founded and led a product infrastructure team building the core data APIs for internal engineers. Still used pervasively today, EntSchema turbocharged Facebook’s Ent framework with codegen, reflection, and a sandbox experience. This later led to the popular open-source Ent framework in Go. Oculus: Software engineer, E7: Senior Staff-level engineer, (2015-2017) Led the effort to rebuild Oculus’s web platform to Facebook’s infrastructure, after the social media giant acquired Oculus. Tech lead for Oculus’ Platform SDK. Stripe: Staff+ software engineer (2018–2023). Tech Lead on the Stripe Connect product, then founded and led the Workflow Engine, a framework built on Temporal. Temporal: Staff Product Manager (2024–present). Product manager at Temporal, an open source durable execution workflow service, working on developer experience and agentic orchestration. Drew went from working on APIs and platform teams, to leading large engineering efforts, and starting new teams and initiatives in his workplaces – before heading over to the “dark side” of product management at a developer tools company. To me, Drew seems the ideal professional to write such a book because he has plentiful experience of working as a software engineer when it was required to understand the business, and he’s now a product manager working with fellow engineers on Temporal. 2. Writing the book Drew told me more context about this project: What was the trigger to start writing this book? “I had written a book outline on usable API design for O’Reilly, and the main themes were product-thinking and user empathy; topics I’ve long wished more engineers engaged with. But Louise Corrigan at O’Reilly liked those themes more than the API topic, and suggested I make product-thinking itself the subject of the book. I liked how this pivot mirrors my personal career journey; of my interest in API design blossoming into a broader interest in products and users”. How long did it take to write? “It was an 18-month process end-to-end, starting the day after I joined Temporal – so that was an intense period! I upgraded a lot of three-day weekends to four-day weekends, and also did some writing on cruise ships. I wrote the whole thing myself, but used friends, and especially Claude, for research. I also sought lots of Alpha and Beta feedback because I believe “it takes a village”. The two biggest inputs were my own career experience and concepts from the design and product communities. It’s well-known stuff, but nobody bothered to inform engineers about it”. Who’s the best product-minded engineer you worked with? “John Carmack, with whom I overlapped at Oculus. He’s amazing because he’s super-deep technically in areas like graphics, yet doggedly pursues the most important product goals. One year, he decided the community needed to level up in building performant VR apps for a mobile compute envelope, so he mentored the entire community in marathon sessions. Another time, he decided the Oculus platform needed more great apps, so went to Netflix and Mojang, worked with those teams, and heroically brought the Netflix and Minecraft VR apps into existence”. What’s your advice for mid and senior-level software engineers who want to be more product-minded? “My suggestions: Ask “why” a lot. Don’t expect to always get clear answers, not even from EMs and PMs. Switch your viewpoint. Go from the system level, to the user lens, and then back again. Use scenarios. Simulate and sequence user interactions until this becomes routine. Writing scenario tests is often a good start. Customer support. Spend time on user support and think about more permanent fixes while you engage and unblock users”. As a product manager, what can devs do to be seen as product-minded and be invited to do more product work? “I try to have devs help me author use cases/scenarios. I also invite them to come along on customer calls if they want. If they have an idea, I ask them to justify it with scenarios. If they start throwing use cases back at me without prompting after a couple of months, I know they’re on the journey”. What is one technique for using AI tools that you’d recommend devs try, in order to be more product-minded? “It’s easier than ever to gather user signal with AI tools. My team at Temporal has a Claude Code skill for gathering customer signal: the tool searches our internal Slack, community Slack, Miro Insights, GitHub issues, and Gong, and aggregates it all into a report with lots of links to chase down customers and requests. Many of those tools in turn have AI assistants that make all this much easier to do!” 3. Excerpt: “The importance of good errors and warnings at product-level” The excerpt below is from “The Product-Minded Engineer”, by Drew Hoskins. Copyright © 2025 Drew Hoskins. Published by O’Reilly Media, Inc. Used with permission. From Chapter 3: “Errors and Warnings” The Value of Diagnostics Crafting well-structured diagnostics with useful messages is an incredibly valuable and high-leverage way to spend your time. For many applications and platforms with complex and open-ended inputs, diagnostics are the primary interface—the vast majority of the user’s time will be spent dealing with errors and progressing to the next one. Filling out electronic forms is all about being told about your missing or malformed input. My coding time is at least half dealing with errors and lint rules. Even writing in a word processor has become a constant process of looking at underlined text and being asked to proofread or rephrase. And yet, as we design software, because errors often don’t appear in screenshots, marketing materials, or API method listings, they can be out of sight and out of mind. Autonomous agents shine a bright light on this problem. They are now regularly presented with error messages resulting from their actions and instructed to correct their mistakes based on them. If the message isn’t sufficiently helpful, they fail at their task. The process of trying different things is slow and costly. Because agents are billed based on usage, the costs are directly measured. Tip: Diagnostics may be the most important interface of your product. Scenarios for Diagnostics When considering errors, warnings, and their associated messages, it is essential to think about a broad range of scenarios, starting with identifying edge cases, to understanding how developers can automate reactions, and how end users will understand and act upon them. Improve your ability to understand your users’ knowledge, generate user stories, and simulate user interactions, and you’ll improve your diagnostics. For users, we provide contextual and actionable errors. For developers, we carefully select our error types, codes, and metadata so that those who receive them can recover gracefully. In the rest of this chapter, you’ll learn how to craft refreshingly useful warnings and errors. We’ll explore how to: Understand the scenario—the persona who will benefit from the error and their situation Provide enough context to our users for them to understand the error Provide actionable error messages that suggest what to do about the problem Choose error codes and types carefully to allow upstream developers to serve their users Raise errors at the API or UI layer so that messages can be written with full context about what the user’s trying to do Shift left; that is, fire errors as early as possible to speed up your users, and before bad things happen In Chapter 8, I’ll address how to list out edge cases to figure out what errors to check for in the first place. For now, I’ll focus on crafting errors once you already know what they are. Categorizing Error Scenarios When writing errors, you need to make a few main choices. First, a user-facing choice: What is the error message? There are also choices of concern to developers so that they can catch errors and automate responses: What is the error’s class or code? What metadata is needed to pinpoint the problem? Thus, when you craft errors in virtually any application or platform, you must think of two categories of user scenarios: the human one and the programmer one. There are further divisions in the developer scenarios: are you communicating with members of your team who work on your codebase, or those from other teams or companies? This is especially important if you are building an API or service where upstream developers can catch your error and act upon it. So, the first step is to pitch your message to the right person in the right circumstance. We’ve all seen errors that didn’t seem to be meant for us, such as when websites show code listings to end users. To determine your audience, start by deciding your error’s category. For our purposes, the five shown in Table 3-1 cover most cases. Start by mentally categorizing any error you write. This gives a huge clue as to who you’re talking to—your own team, other developers, or users—and when the errors can be fixed—at runtime or during development. This will help you write with the right vocabulary and suggest helpful actions (see Table 3-2). These five types of scenarios reveal drastically different strategies. For example, if an assertion triggers in production, it’s usually catastrophic. If the code is in a state the authors didn’t foresee, it will lead to unpredictable behavior—most likely in the form of a crash or a poor error message. Occasionally there are worse consequences, like data corruption. In some programming languages, assertions are stripped out in production to optimize their execution, meaning that you shouldn’t rely on them for anything load bearing. In no case is the end user persona expected to interact with them successfully. For some applications, all end users are not the same; in which case, messages should be tailored to each persona. A classic example is a Preconditions Not Met error caused by the user not having the necessary access. Is the user an administrator or an end user? This determines whether we will provide them with direct instructions or instruct them to contact an administrator. Knowing your personas will help you speak to the user’s ontology. (Ontology was defined in Chapter 2 as a structured graph of known concepts.) Consider “PC Load Letter,” [a reference to a segment before this excerpt] which tried to ask users to reload the printer’s paper tray. It was actionable—it told the user to load the paper—but it failed because it was speaking to the wrong persona. “PC” stood for “paper cassette” and “Letter” referred to a size of paper— 8.5”x11”. Perhaps instead, they should have labeled the paper trays A, B, and C and said, “Reload tray B.” Categorizing Errors in Practice Let’s work an example to show how to use product thinking to categorize errors. Which of the five categories does a divide-by-zero—in Python, a ZeroDivisionError —fall into? Imagine you are writing a method to compute the average value of an online metric over a time window. Look at the return statement. If this method threw a ZeroDivisionError when the metrics array was empty, callers would be quite confused—they’d need to know the innards of your function to understand. Tip: Users and developers should never have to understand your implementation to understand an error. Thus, unless your code is literally a calculator, a divide-by-zero error is an Assertion, designed to be found at test time and telling your team that the code needs improvement. Avoid it—do some upfront validation before attempting the division. So, we’re going to validate, but what scenario category would that validation fall under? The circumstance that led to the len(metrics)==0 condition could have been any of those listed in Table 3-3. As I’ll discuss in the next section on messages, you’ll want to suggest different actions in each of these cases and therefore will need distinguishing checks in the code. Further, you will need to perform these validations at a moment when you have the necessary context. In this section, we categorized diagnostics as either interacting with developers or end users and distinguished between scenarios that were actionable at runtime and those that were actionable only during development. Next, we’ll build on this to author awesome messages. Warning and Error Messages Writing diagnostic messages combines system thinking with user thinking. Know precisely what happened, but shift to your user’s perspective. Explain what they need to know in terms they understand. Otherwise, obscure warnings like LaTeX’s “underfull hbox (badness 10000)” will result. Users seeing a diagnostic will want to know two things: What precisely happened to cause the error, in terms from the product’s ontology? This should help them know the impact of the failure and provide clues as to how to remediate it. What can they do about it, if anything? Actionable diagnostics will directly help them accomplish their task. Let’s tackle those two goals one at a time. But first, let me introduce an example that will thread through the next few sections. Case Study Introduction Channelz is a fictional software as a service (SaaS) company building a workplace communication tool like Slack, Microsoft Teams, or Discord. Elise works on the API team, and her teammate Deng is the tech lead. In Channelz, one can write direct messages to coworkers or send them to “channels,” which are groups of employees organized around a specific topic; the API engineering team might have a channel called #team-api-eng. Elise’s user handle is @elisek and Deng’s is @deng. Channelz is building out an API that can be used to send messages from bots, either directly to users or to channels. Their customers want to use it to send various notifications. Before coding, Elise sketches a quick developer interface design and shows it to Deng. Channelz messages can go to a set of individuals or to a channel to alert employees when something has gone wrong or a job has been completed. The method in the Python SDK they ship to customers will look like this: She sketches some use cases for Deng: Deng looks at Elise’s design and asks her to list failure scenarios as well. Elise has shown successful usages of the API, when callers already know what to do, but what about before then? If their users’ coding session is a journey, Elise has shown only the end. It’s as if somebody asked for directions with an online maps search, and she responded with only a pin on the destination. Elise comes up with a few scenarios. (I will teach probing for edge cases in Chapter 7. For this chapter, I’ll skip that step.) She raises one important scenario that we’ll obsess over here: what if the user or channel passed into the API is invalid? [We now skip ahead to the middle of the chapter, skipping through the section titled Provide Context.] Make Error and Warning Messages Actionable In many circumstances knowing what happened is only half the battle. Users often need to be given suggestions or told what to do. And for read operations—and increasingly with AI—you can even correct the mistake for them, as with Google’s “Showing results for: [correction]” feature, as well as coding or writing assistants automatically fixing your code or language. We’ve all spent countless hours of our lives dealing with error messages, figuring out what to do, sometimes discovering after much investigation that the fix is simple. In this section, you’ll see how to routinely improve your diagnostics. To achieve this, you’ll need to empathize with your audience, starting with the scenario categorization we did previously. Returning to our Channelz example, suppose you called the API: bot.send_message(message=”The sky is falling!”, channel=”@barnyard- friends”) and got this error message: Cannot deliver a Channelz message to channel ‘@barnyard-friends’: channel does not exist. Can you tell what went wrong? It may take a bit to figure out, and if you’re not super familiar with Chann
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): edd685c842922002fdecbdd765adddbbbe7a0dd288310d5badd8d814dfb80149
