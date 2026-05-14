You are an INDEPENDENT rubric reviewer, blind to any prior evaluator's scoring. Do NOT read eval.json.

Stamp `eval_json_hash_at_review: "NOT_CONSULTED"` at the top of your manual_review.md.

RUBRIC:
version: rubric_youtube.v1
source_type: youtube
composite_max_points: 100
components:
- id: brief_summary
  max_points: 25
  criteria:
  - id: brief.thesis_capture
    description: Brief summary states the video's central thesis or learning objective
      in one sentence.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
    - finesure.completeness
  - id: brief.format_identified
    description: Brief identifies the video format explicitly (tutorial/interview/lecture/commentary/etc.).
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: brief.speakers_captured
    description: Brief names the host/channel and any guests or key products/libraries
      discussed.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
    - qafact
  - id: brief.major_segments_outlined
    description: Brief outlines the major structural segments of the video (intro,
      sections, demo, conclusion).
    max_points: 5
    maps_to_metric:
    - finesure.completeness
    - g_eval.coherence
  - id: brief.takeaways_surfaced
    description: Brief highlights 2-3 takeaways a viewer would remember after watching.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
    - finesure.completeness
  - id: brief.length_5_to_7_sentences
    description: Brief is 5-7 sentences.
    max_points: 2
    maps_to_metric:
    - finesure.conciseness
  - id: brief.no_clickbait
    description: Brief does not reproduce clickbait/hook phrasing from the source
      title.
    max_points: 2
    maps_to_metric:
    - finesure.faithfulness
- id: detailed_summary
  max_points: 45
  criteria:
  - id: detailed.chronological_order
    description: Detailed bullets follow the video's chronological order.
    max_points: 6
    maps_to_metric:
    - g_eval.coherence
  - id: detailed.all_chapters_covered
    description: Every substantive chapter or major topic turn is covered by at least
      one bullet.
    max_points: 10
    maps_to_metric:
    - finesure.completeness
    - qafact
  - id: detailed.demonstrations_preserved
    description: Demonstrations, code walkthroughs, or live examples are captured.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: detailed.caveats_preserved
    description: Warnings, caveats, limitations the speaker mentions are captured.
    max_points: 5
    maps_to_metric:
    - finesure.faithfulness
    - summac
  - id: detailed.examples_purpose_not_verbatim
    description: Examples/analogies summarized as PURPOSE, not reproduced verbatim.
    max_points: 5
    maps_to_metric:
    - finesure.conciseness
  - id: detailed.entities_named
    description: Products, libraries, datasets, or case studies referenced are named.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
    - qafact
  - id: detailed.closing_takeaway
    description: The video's closing takeaway is explicitly captured.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
  - id: detailed.no_sponsor_padding
    description: Sponsor reads, intros, and 'like and subscribe' fluff are not given
      bullets.
    max_points: 4
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
  - id: tags.topical_specificity
    description: Tags capture specific subject matter, not generic terms.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
  - id: tags.format_tag_present
    description: Includes a tag for content type (tutorial/interview/beginner/advanced).
    max_points: 2
    maps_to_metric:
    - finesure.completeness
  - id: tags.technologies_named
    description: Named technologies/libraries/frameworks from the video are tagged.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.no_unsupported_claims
    description: No tags imply topics not actually covered.
    max_points: 4
    maps_to_metric:
    - finesure.faithfulness
    - summac
- id: label
  max_points: 15
  criteria:
  - id: label.content_first_3_to_5_words
    description: Label is 3-5 words (max 50 chars), content-first, declarative.
    max_points: 5
    maps_to_metric:
    - finesure.conciseness
  - id: label.reflects_primary_topic
    description: Label reflects the primary topic, not side tangents.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: label.no_clickbait_retention
    description: Label removes clickbait/hook fragments from the original title.
    max_points: 5
    maps_to_metric:
    - finesure.faithfulness
anti_patterns:
- id: clickbait_label_retention
  description: Label retains YouTube clickbait phrasing ('You won't believe...', 'This
    changes EVERYTHING').
  auto_cap: 90
  detection_hint: Look for exclamation marks, superlatives, curiosity-gap phrasing
    in label.
- id: example_verbatim_reproduction
  description: Brief or detailed summary reproduces an example/analogy verbatim.
  auto_cap: null
  penalty_points: 3
- id: editorialized_stance
  description: Summary introduces stance/framing not present in source.
  auto_cap: 60
- id: speakers_absent
  description: Summary fails to identify the host or any referenced people.
  auto_cap: 75
- id: invented_chapter
  description: Summary invents a chapter or segment not present in the video.
  auto_cap: 60
global_rules:
  editorialization_penalty:
    threshold_flags: 3
    cap_on_trigger: 60


SUMMARY:
## URL 1: https://www.youtube.com/watch?v=UF8uR6Z6KLc

### SUMMARY
```yaml
mini_title: Steve Jobs Stanford Commencement Address
brief_summary: In this lecture, Steve Jobs argues that steve jobs argues that one
  can only understand the significance of life's events by looking backward, and therefore,
  it is crucial to trust that seemingly disparate experiences will eventually connect.
  The lecture moves through Connecting the Dots, Love and Loss, and Death. Along the
  way Steve Jobs references Apple, Reed College, and Macintosh.
tags:
- lecture
- steve-jobs
- commencement-address
- stanford-university
- life-lessons
- entrepreneurship
- apple-history
- personal-growth
- career-advice
- motivation
detailed_summary:
- heading: Overview
  bullets:
  - In this lecture, Steve Jobs argues that steve jobs argues that one can only understand
    the significance of life's events by looking backward, and therefore, it is crucial
    to trust that seemingly disparate experiences will eventually connect.
  sub_sections:
    Format and speakers:
    - 'Format: lecture.'
    - 'Speakers: Steve Jobs.'
    Core argument:
    - Steve Jobs argues that one can only understand the significance of life's events
      by looking backward, and therefore, it is crucial to trust that seemingly disparate
      experiences will eventually connect. He emphasizes the importance of following
      one's heart and intuition, especially in the face of adversity and the certainty
      of death.
- heading: Chapter walkthrough
  bullets: []
  sub_sections:
    Connecting the Dots:
    - Steve Jobs recounts his adoption process, noting his biological mother's initial
      desire for him to be adopted by college graduates.
    - He describes dropping out of Reed College after six months due to its expense
      and his inability to perceive its value.
    - During his time as a "drop-in" at Reed, he attended a calligraphy class, which
      he considered the best in the country.
    - Ten years later, the knowledge of typography gained from this class was integrated
      into the original Macintosh computer.
    - He posits that the beautiful typography in personal computers, including Windows,
      might not exist without his calligraphy experience.
    - Jobs concludes that one can only connect life's "dots" in retrospect, urging
      trust in future connections.
    Love and Loss:
    - Jobs details starting Apple with Steve Wozniak in his parents' garage at age
      20, growing it into a significant company within a decade.
    - At age 30, he was publicly fired from Apple after a disagreement with the board
      of directors and a co-leader.
    - Despite the devastation, he realized his enduring love for his work and decided
      to embark on new ventures.
    - He describes being fired as a liberating experience, replacing the burden of
      success with the lightness of a new beginning.
    - In the subsequent five years, he founded NeXT and Pixar, and married his wife,
      Laurene.
    - Pixar produced Toy Story, the world's first computer-animated film.
    - Apple later acquired NeXT, facilitating Jobs's return to Apple and integrating
      NeXT's technology into Apple's core.
    Death:
    - 'Jobs shares a personal philosophy adopted at 17: to live each day as if it
      were his last.'
    - For 33 years, he has used this question daily to assess his life choices and
      make necessary changes.
    - He recounts a recent diagnosis of pancreatic cancer, initially believed to be
      incurable with a short prognosis.
    - A subsequent biopsy revealed a rare, operable form of the disease, leading to
      successful surgery and recovery.
    - This near-death experience reinforced his belief that time is finite and should
      not be wasted living someone else's life.
    - He encourages the graduates to avoid dogma and to bravely follow their inner
      voice and intuition.
- heading: Closing remarks
  bullets:
  - 'Recap: Jobs concludes by referencing the farewell message from The Whole Earth
    Catalog, "Stay hungry, stay foolish," as a guiding principle for continuous curiosity
    and bold exploration. He extends this wish to the graduating class, encouraging
    them to maintain a lifelong pursuit of knowledge and unconventional thinking.'
  sub_sections: {}
metadata:
  source_type: youtube
  url: https://www.youtube.com/watch?v=UF8uR6Z6KLc
  author: null
  date: null
  extraction_confidence: high
  confidence_reason: transcript via tier=gemini_filedata len=11971
  total_tokens_used: 3030
  gemini_pro_tokens: 0
  gemini_flash_tokens: 3030
  total_latency_ms: 41595
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: Steve Jobs Stanford Commencement Address
    brief_summary: In this lecture, Steve Jobs argues that steve jobs argues that
      one can only understand the significance of life's events by looking backward,
      and therefore, it is crucial to trust that seemingly disparate experiences will
      eventually connect. The lecture moves through Connecting the Dots, Love and
      Loss, and Death. Along the way Steve Jobs references Apple, Reed College, and
      Macintosh.
    tags:
    - steve-jobs
    - commencement-address
    - stanford-university
    - life-lessons
    - entrepreneurship
    - apple-history
    - personal-growth
    - career-advice
    - motivation
    - lecture
    speakers:
    - Steve Jobs
    guests: null
    entities_discussed:
    - Apple
    - Reed College
    - Macintosh
    - Windows
    - NeXT
    - Pixar
    - Toy Story
    - The Whole Earth Catalog
    detailed_summary:
      thesis: Steve Jobs argues that one can only understand the significance of life's
        events by looking backward, and therefore, it is crucial to trust that seemingly
        disparate experiences will eventually connect. He emphasizes the importance
        of following one's heart and intuition, especially in the face of adversity
        and the certainty of death.
      format: lecture
      chapters_or_segments:
      - timestamp: ''
        title: Connecting the Dots
        bullets:
        - Steve Jobs recounts his adoption process, noting his biological mother's
          initial desire for him to be adopted by college graduates.
        - He describes dropping out of Reed College after six months due to its expense
          and his inability to perceive its value.
        - During his time as a "drop-in" at Reed, he attended a calligraphy class,
          which he considered the best in the country.
        - Ten years later, the knowledge of typography gained from this class was
          integrated into the original Macintosh computer.
        - He posits that the beautiful typography in personal computers, including
          Windows, might not exist without his calligraphy experience.
        - Jobs concludes that one can only connect life's "dots" in retrospect, urging
          trust in future connections.
      - timestamp: ''
        title: Love and Loss
        bullets:
        - Jobs details starting Apple with Steve Wozniak in his parents' garage at
          age 20, growing it into a significant company within a decade.
        - At age 30, he was publicly fired from Apple after a disagreement with the
          board of directors and a co-leader.
        - Despite the devastation, he realized his enduring love for his work and
          decided to embark on new ventures.
        - He describes being fired as a liberating experience, replacing the burden
          of success with the lightness of a new beginning.
        - In the subsequent five years, he founded NeXT and Pixar, and married his
          wife, Laurene.
        - Pixar produced Toy Story, the world's first computer-animated film.
        - Apple later acquired NeXT, facilitating Jobs's return to Apple and integrating
          NeXT's technology into Apple's core.
      - timestamp: ''
        title: Death
        bullets:
        - 'Jobs shares a personal philosophy adopted at 17: to live each day as if
          it were his last.'
        - For 33 years, he has used this question daily to assess his life choices
          and make necessary changes.
        - He recounts a recent diagnosis of pancreatic cancer, initially believed
          to be incurable with a short prognosis.
        - A subsequent biopsy revealed a rare, operable form of the disease, leading
          to successful surgery and recovery.
        - This near-death experience reinforced his belief that time is finite and
          should not be wasted living someone else's life.
        - He encourages the graduates to avoid dogma and to bravely follow their inner
          voice and intuition.
      demonstrations: []
      closing_takeaway: Jobs concludes by referencing the farewell message from The
        Whole Earth Catalog, "Stay hungry, stay foolish," as a guiding principle for
        continuous curiosity and bold exploration. He extends this wish to the graduating
        class, encouraging them to maintain a lifelong pursuit of knowledge and unconventional
        thinking.
    attribution_confidence: high
    _youtube_format:
      format: explainer
      confidence: 0.625
    _dense_verify:
      format_label: talk
      missing_fact_count: 0
    route_subtype: video
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
- claim: You can't connect the dots looking forward; you can only connect them looking
    backwards.
  importance: 5
- claim: You have to trust that the dots will somehow connect in your future, trusting
    in something like your gut, destiny, life, or karma.
  importance: 5
- claim: You've got to find what you love to do, both for work and for personal relationships.
  importance: 5
- claim: The only way to be truly satisfied is to do what you believe is great work,
    and the only way to do great work is to love what you do.
  importance: 5
- claim: Remembering that you'll be dead soon is the most important tool to help make
    big choices in life.
  importance: 5
- claim: "In the face of death, almost everything\u2014external expectations, pride,\
    \ fear of embarrassment or failure\u2014falls away, leaving only what is truly\
    \ important."
  importance: 5
- claim: Your time is limited, so don't waste it living someone else's life.
  importance: 5
- claim: Have the courage to follow your heart and intuition, as they somehow already
    know what you truly want to become.
  importance: 5
- claim: '''Stay hungry, stay foolish'' is a farewell message that the speaker wishes
    for himself and the graduates.'
  importance: 5
- claim: Believing that the dots will connect down the road gives you the confidence
    to follow your heart, even when it leads you off the well-worn path.
  importance: 4
- claim: Getting fired from Apple, though devastating, turned out to be the best thing
    that could have ever happened, freeing the speaker to enter a creative period.
  importance: 4
- claim: If you haven't found what you love yet, keep looking, and don't settle.
  importance: 4
- claim: Don't lose faith when life hits you hard, as the only thing that kept the
    speaker going after being fired was his love for what he did.
  importance: 4
- claim: Remembering that you are going to die is the best way to avoid the trap of
    thinking you have something to lose.
  importance: 4
- claim: Don't be trapped by dogma, which is living with the results of other people's
    thinking.
  importance: 4
- claim: Don't let the noise of others' opinions drown out your own inner voice.
  importance: 4
- claim: Everything else is secondary to following your heart and intuition.
  importance: 4
- claim: Death is very likely the single best invention of life, acting as life's
    change agent by clearing out the old to make way for the new.
  importance: 4
- claim: Dropping out of Reed College and taking calligraphy classes, which seemed
    impractical, later proved crucial for designing the Macintosh's typography.
  importance: 3
- claim: The speaker decided to drop out of an expensive college after six months
    because he couldn't see the value, had no idea what to do, and was spending his
    parents' savings.
  importance: 3
- claim: The Macintosh was the first computer with beautiful typography because of
    the speaker's calligraphy knowledge.
  importance: 3
- claim: If the speaker hadn't dropped out and taken the calligraphy class, personal
    computers might not have the wonderful typography they do.
  importance: 3
- claim: After being fired from Apple, the speaker started NeXT and Pixar, with Pixar
    creating 'Toy Story' and becoming the most successful animation studio.
  importance: 3
- claim: Apple bought NeXT, and the speaker returned to Apple, with NeXT's technology
    being central to Apple's current renaissance.
  importance: 3
- claim: The speaker was diagnosed with a rare, curable form of pancreatic cancer
    and had surgery, and is now fine.
  importance: 3
- claim: The speaker has looked in the mirror every morning for 33 years, asking 'If
    today were the last day of my life, would I want to do what I am about to do today?'
    and changes things if the answer is 'no' too often.
  importance: 3

```

### SOURCE
```
Transcript
Thank you.I'm honored to be with you today for your commencement from one of the finest universities in the world.Truth be told, uh, I never graduated from college. And uh, this is the closest I've ever gotten to a college graduation.Today, I want to tell you three stories from my life. That's it. No big deal. Just three stories.The first story is about connecting the dots.I dropped out of Reed College after the first six months, but then stayed around as a drop-in for another 18 months or so before I really quit.So why'd I drop out?It started before I was born.My biological mother was a young, unwed graduate student, and she decided to put me up for adoption.She felt very strongly that I should be adopted by college graduates. So everything was all set for me to be adopted at birth by a lawyer and his wife.Except that when I popped out, they decided at the last minute that they really wanted a girl.So my parents, who were on a waiting list, got a call in the middle of the night asking, "We've got an unexpected baby boy. Do you want him?"They said, "Of course."My biological mother found out later that my mother had never graduated from college and that my father had never graduated from high school. She refused to sign the final adoption papers.She only relented a few months later when my parents promised that I would go to college. This was the start in my life.And 17 years later, I did go to college. But I naively chose a college that was almost as expensive as Stanford. And all of my working-class savings were being spent on my college tuition.After six months, I couldn't see the value in it. I had no idea what I wanted to do with my life and no idea how college was going to help me figure it out. And here I was spending all the money my parents had saved their entire life.So I decided to drop out and trust that it would all work out okay. It was pretty scary at the time, but looking back, it was one of the best decisions I ever made.The minute I dropped out, I could stop taking the required classes that didn't interest me and begin dropping in on the ones that looked far more interesting.It wasn't all romantic. I didn't have a dorm room, so I slept on the floor in friends' rooms. I returned Coke bottles for the five-cent deposits to buy food with. And I would walk the seven miles across town every Sunday night to get one good meal a week at the Hare Krishna Temple.I loved it. And much of what I stumbled into by following my curiosity and intuition turned out to be priceless later on.Let me give you one example.Reed College at that time offered perhaps the best calligraphy instruction in the country. Throughout the campus, every poster, every label on every drawer was beautifully hand-calligraphed. Because I had dropped out and didn't have to take the normal classes, I decided to take a calligraphy class to learn how to do this. I learned about serif and sans-serif typefaces, about varying the amount of space between different letter combinations, about what makes great typography great.It was beautiful, historical, artistically subtle in a way that science can't capture. And I found it fascinating.None of this had even a hope of any practical application in my life.But 10 years later, when we were designing the first Macintosh computer, it all came back to me. And we designed it all into the Mac. It was the first computer with beautiful typography.If I had never dropped in on that single course in college, the Mac would have never had multiple typefaces or proportionally spaced fonts. And since Windows just copied the Mac, it's likely that no personal computer would have them.If I had never dropped out, I would have never dropped in on that calligraphy class, and personal computers might not have the wonderful typography that they do. Of course, it was impossible to connect the dots looking forward when I was in college, but it was very, very clear looking backwards 10 years later. Again, you can't connect the dots looking forward. You can only connect them looking backwards. So you have to trust that the dots will somehow connect in your future. You have to trust in something, your gut, destiny, life, karma, whatever. Because believing that the dots will connect down the road will give you the confidence to follow your heart, even when it leads you off the well-worn path, and that will make all the difference.My second story is about love and loss.I was lucky. I found what I loved to do early in life. Woz and I started Apple in my parents' garage when I was 20. We worked hard, and in 10 years Apple had grown from just the two of us in a garage into a $2 billion company with over 4,000 employees. We'd just released our finest creation, the Macintosh, a year earlier, and I'd just turned 30.And then I got fired.How can you get fired from a company you started?Well, as Apple grew, we hired someone who I thought was very talented to run the company with me. And for the first year or so, things went well. But then our visions of the future began to diverge, and eventually we had a falling out. When we did, our board of directors sided with him. And so at 30, I was out, and very publicly out. What had been the focus of my entire adult life was gone, and it was devastating.I really didn't know what to do for a few months. I felt that I'd let the previous generation of entrepreneurs down, that I'd dropped the baton as it was being passed to me. I met with David Packard and Bob Noyce and tried to apologize for screwing up so badly. I was a very public failure, and I even thought about running away from the Valley. But something slowly began to dawn on me. I still loved what I did.The turn of events at Apple had not changed that one bit. I'd been rejected, but I was still in love.And so I decided to start over.I didn't see it then, but it turned out that getting fired from Apple was the best thing that could have ever happened to me. The heaviness of being successful was replaced by the lightness of being a beginner again, less sure about everything. It freed me to enter one of the most creative periods of my life. During the next five years, I started a company named NeXT, another company named Pixar, and fell in love with an amazing woman who would become my wife. Pixar went on to create the world's first computer-animated feature film, Toy Story, and is now the most successful animation studio in the world.In a remarkable turn of events, Apple bought NeXT, and I returned to Apple, and the technology we developed at NeXT is at the heart of Apple's current renaissance. And Laurene and I have a wonderful family together.I'm pretty sure none of this would have happened if I hadn't been fired from Apple. It was awful tasting medicine, but I guess the patient needed it. Sometime life, sometimes life's going to hit you in the head with a brick. Don't lose faith. I'm convinced that the only thing that kept me going was that I loved what I did. You've got to find what you love, and that is as true for work as it is for your lovers. Your work is going to fill a large part of your life, and the only way to be truly satisfied is to do what you believe is great work. And the only way to do great work is to love what you do. If you haven't found it yet, keep looking, and don't settle. As with all matters of the heart, you'll know when you find it. And like any great relationship, it just gets better and better as the years roll on. So keep looking. Don't settle.My third story is about death.When I was 17, I read a quote that went something like, "If you live each day as if it was your last, someday you'll most certainly be right."It made an impression on me. And since then, for the past 33 years, I've looked in the mirror every morning and asked myself, "If today were the last day of my life, would I want to do what I am about to do today?" And whenever the answer has been "no" for too many days in a row, I know I need to change something.Remembering that I'll be dead soon is the most important tool I've ever encountered to help me make the big choices in life. Because almost everything, all external expectations, all pride, all fear of embarrassment or failure, these things just fall away in the face of death, leaving only what is truly important. Remembering that you are going to die is the best way I know to avoid the trap of thinking you have something to lose. You are already naked. There is no reason not to follow your heart.About a year ago, I was diagnosed with cancer. I had a scan at 7:30 in the morning, and it clearly showed a tumor on my pancreas. I didn't even know what a pancreas was. The doctors told me this was almost certainly a type of cancer that is incurable, and that I should expect to live no longer than three to six months. My doctor advised me to go home and get my affairs in order, which is doctor's code for "prepare to die." It means to try and tell your kids everything you thought you'd have the next 10 years to tell them, in just a few months. It means to make sure everything is buttoned up so that it will be as easy as possible for your family. It means to say your goodbyes.I lived with that diagnosis all day. Later that evening, I had a biopsy where they stuck an endoscope down my throat, through my stomach and into my intestines, put a needle into my pancreas and got a few cells from the tumor. I was sedated, but my wife, who was there, told me that when they viewed the cells under a microscope, the doctors started crying because it turned out to be a very rare form of pancreatic cancer that is curable with surgery. I had the surgery, and thankfully, I'm fine now.This was the closest I've been to facing death, and I hope it's the closest I get for a few more decades. Having lived through it, I can now say this to you with a bit more certainty than when death was a useful but purely intellectual concept.No one wants to die. Even people who want to go to heaven don't want to die to get there. And yet, death is the destination we all share. No one has ever escaped it. And that is as it should be, because death is very likely the single best invention of life. It's life's change agent. It clears out the old to make way for the new. Right now, the new is you. But someday not too long from now, you will gradually become the old and be cleared away. Sorry to be so dramatic, but it's quite true.Your time is limited, so don't waste it living someone else's life. Don't be trapped by dogma, which is living with the results of other people's thinking. Don't let the noise of others' opinions drown out your own inner voice. And most important, have the courage to follow your heart and intuition. They somehow already know what you truly want to become. Everything else is secondary.When I was young, there was an amazing publication called The Whole Earth Catalog, which was one of the Bibles of my generation. It was created by a fellow named Stewart Brand, not far from here in Menlo Park, and he brought it to life with his poetic touch. This was in the late '60s, before personal computers and desktop publishing. So it was all made with typewriters, scissors, and Polaroid cameras. It was sort of like Google in paperback form, 35 years before Google came along. It was idealistic, overflowing with neat tools and great notions.Stewart and his team put out several issues of The Whole Earth Catalog. And then, when it had run its course, they put out a final issue. It was the mid-1970s, and I was your age.On the back cover of their final issue was a photograph of an early morning country road, the kind you might find yourself hitchhiking on if you were so adventurous. Beneath it were the words, "Stay hungry, stay foolish." It was their farewell message as they signed off. "Stay hungry, stay foolish." And I have always wished that for myself. And now, as you graduate to begin anew, I wish that for you. Stay hungry, stay foolish. Thank you all very much.
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): c2522650aee8ea6a9cd5e190158630a4e7a54288fe48b1172d4cb715a73bfbb1
