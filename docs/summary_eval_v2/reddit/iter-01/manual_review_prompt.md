You are an INDEPENDENT rubric reviewer, blind to any prior evaluator's scoring. Do NOT read eval.json.

Stamp `eval_json_hash_at_review: "NOT_CONSULTED"` at the top of your manual_review.md.

RUBRIC:
version: rubric_reddit.v1
source_type: reddit
composite_max_points: 100
components:
- id: brief_summary
  max_points: 25
  criteria:
  - id: brief.op_intent_captured
    description: Brief states OP's core question, problem, or claim in neutral wording.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
    - finesure.completeness
  - id: brief.response_range
    description: Brief summarizes the range of responses (main solution, common advice,
      dissent).
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: brief.consensus_signal
    description: Brief describes consensus, partial agreement, or disagreement.
    max_points: 4
    maps_to_metric:
    - finesure.faithfulness
  - id: brief.caveats_surfaced
    description: Brief surfaces important caveats (regional, legal, risk).
    max_points: 3
    maps_to_metric:
    - finesure.faithfulness
  - id: brief.neutral_tone
    description: Brief is neutral; does not add summarizer's own judgment.
    max_points: 4
    maps_to_metric:
    - summac
  - id: brief.length_5_to_7_sentences
    description: Brief is 5-7 sentences.
    max_points: 2
    maps_to_metric:
    - finesure.conciseness
- id: detailed_summary
  max_points: 45
  criteria:
  - id: detailed.reply_clusters
    description: Detailed summary represents major opinion clusters, not individual
      comments.
    max_points: 10
    maps_to_metric:
    - finesure.completeness
  - id: detailed.hedged_attribution
    description: Unverified comment claims use hedging language ('commenters argue');
      no assertion as truth.
    max_points: 8
    maps_to_metric:
    - finesure.faithfulness
    - summac
  - id: detailed.counterarguments_included
    description: Minority or contrarian viewpoints are included when substantively
      different.
    max_points: 7
    maps_to_metric:
    - finesure.completeness
  - id: detailed.external_refs_captured
    description: Data, experiments, external references cited by commenters are captured
      without fabrication.
    max_points: 6
    maps_to_metric:
    - finesure.faithfulness
    - qafact
  - id: detailed.unresolved_questions
    description: Unresolved questions or open points are listed.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
  - id: detailed.moderation_context
    description: If moderator actions or removed-comment divergence affects thread,
      it's noted.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: detailed.no_joke_chains
    description: Joke chains, side-chatter, meta-discussion are not over-represented.
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
  - id: tags.subreddit_present
    description: Subreddit appears as a tag (e.g., 'r-askhistorians').
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.thread_type
    description: Thread type tag present ('q-and-a', 'experience-report', 'best-practices').
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.no_value_judgments
    description: No tags encode value judgments unless widely agreed in thread.
    max_points: 4
    maps_to_metric:
    - finesure.faithfulness
  - id: tags.topical_specificity
    description: Tags are specific, not generic.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
- id: label
  max_points: 15
  criteria:
  - id: label.rsubreddit_prefix
    description: Label starts with 'r/<subreddit> ' followed by compact title.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: label.central_issue
    description: Label captures central issue that majority of comments address.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: label.neutral
    description: Label is neutral, not outrage/meme framing.
    max_points: 4
    maps_to_metric:
    - finesure.faithfulness
anti_patterns:
- id: comment_claim_asserted_as_fact
  description: An unverified commenter claim is stated as truth without hedging.
  auto_cap: 60
- id: missing_removed_comment_note
  description: num_comments > rendered_count but summary doesn't mention missing/removed
    comments.
  auto_cap: 75
- id: editorialized_stance
  description: Summary adds summarizer's own judgment absent from thread.
  auto_cap: 60
global_rules:
  editorialization_penalty:
    threshold_flags: 3


SUMMARY:
## URL 1: https://www.reddit.com/r/IAmA/comments/9ke63/i_did_heroin_yesterday_i_am_not_a_drug_user_and/

### SUMMARY
```yaml
mini_title: r/IAmA first-time heroin risks
brief_summary: OP's main point was The Original Poster (OP) intended to share their
  first-time heroin experience, inviting questions (AMA). The dominant replies focused
  on Warnings and Personal Stories of Heroin Addiction and Withdrawal. Consensus stayed
  around warnings and personal stories of heroin addiction and withdrawal while relatability.
  Dissent centered on The OP's assertion of self-control and intent to resist future
  use.
tags:
- r-iama
- experience-report
- heroin
- first-time
- drug-experience
- ama
- addiction-risk
- opiates
- risk-taking
- drug-culture
detailed_summary:
- heading: OP Intent
  bullets:
  - The Original Poster (OP) intended to share their first-time heroin experience,
    inviting questions (AMA) from the community. They aimed to describe the intense
    pleasure felt, acknowledge the drug's addictive potential, and challenge common
    misconceptions and stereotypes about heroin users and drug dangers. The OP also
    explicitly stated an intent to try cocaine next to compare experiences and effects,
    viewing it as an "experiment and an adventure in life."
  sub_sections: {}
- heading: Reply Clusters
  bullets:
  - 'Warnings and Personal Stories of Heroin Addiction and Withdrawal: Many commenters,
    often self-identified former addicts, emphasize the extreme addictiveness of opioids,
    the severe pain of withdrawal, and how quickly addiction can develop. They share
    personal anecdotes of lives ruined, financial loss, and repeated overdoses, directly
    contradicting the OP''s perceived self-control.'
  - 'Relatability and Shared First-Time Experiences (Often with a Cautionary Tone):
    Several users relate to the OP''s initial feelings of intense pleasure and the
    belief in their own self-control, often drawing parallels to their own first experiences
    with heroin or other strong drugs (like cocaine or prescription opiates). However,
    many of these comments still carry an underlying warning about the long-term risks.'
  - 'Challenging Drug Stereotypes and Advocating for Drug Liberalization/Harm Reduction:
    Some commenters appreciate the OP''s post for demystifying heroin or challenging
    the "automatic addict" narrative. They critique societal hypocrisy regarding drug
    use (e.g., alcohol vs. heroin, cocaine vs. heroin) and advocate for personal freedom
    or drug legalization.'
  - 'Specific Questions and Details about the Experience: Users ask for more descriptive
    details about the sensation, the aftermath, or specific aspects of the OP''s encounter.'
  - 'Advice on Other Drugs / Comparisons: Some users suggest trying other drugs (often
    psychedelics) as alternatives to heroin, or offer comparisons between heroin and
    other substances.'
  sub_sections:
    Warnings and Personal Stories of Heroin Addiction and Withdrawal:
    - Yes, opioid molecules are the best on earth. It really is the greatest feeling
      life has to offer. Unfortunately once I got a taste for that feeling it was
      all over. Opioids are the most addictive drugs on the planet. Once your body
      gets addicted the withdrawl is pure hell.
    - Don't do it again. EVER. There is nothing so terrible in this world as heroin
      withdrawals. It takes doing heroin every day or every other day for about 2-3
      months to get your first habit, but that time goes by real fast when you are
      high. Just leave it alone. Seriously.
    - Just wanted to let you know you sound exactly like I did when I first did dope.
      Same optimistic and sense of self control that us thrill seekers have. Smart
      enough to rationalize and study exactly what was going on. That was over $500,000
      ago. I was a CIO and built a very successful company while hiding track marks.
      Eventually it all fell apart. I od'd several times and now I steal from my best
      friend and hate myself.
    - Fuck heroin. It's awesome. Too awesome. The only reason I stopped is because
      I lost two good friends to it. One died after an OD. The other is still alive,
      but he's all but a vegetable.
    - STOP! IMMEDIATELY!! I have done many many drugs, i've dealt drugs when I was
      younger, but I have never seen something ruin so many friends lives like herion,
      luckily I never got too into it, I always enjoyed speedier drugs and hallucinogens,
      but if you get hooked on herion, you get hooked and if you step up to injecting
      it is VERY hard to stop.
    Relatability and Shared First-Time Experiences (Often with a Cautionary Tone):
    - Your story sounds very similar to the way I first tried cocaine. I was a successful
      career guy as a creative professional in the TV business, and had never really
      used drugs. I tried it one night on a whim when a friend had some, and had a
      similarly incredible first-time experience. I had the same thoughts of "Wow,
      I see why people love this and get hooked. I won't do it any more though." Within
      a year I had become seriously addicted, depleted my savings account, run up
      several credit cards to the max, run from the cops while I was high, done a
      month in jail for evading arrest, had to move in with my parents, and lost my
      job and my career.
    - I just had my wisdom teeth out and got 10mg oxycodone pills, which is basically
      synthetic heroin. I took them for funzies a few times after my mouth healed.
      It was great, I felt on top of the world, outgoing, full of happiness and good
      will! That being said, and despite the fact I'd love to feel that way again,
      I have no intentions of ever searching for any, and if the opportunity came
      up I probably wouldn't even buy any. I completely understand the OP and can
      relate. Maybe we're just blessed with nonaddictive personalities.
    - I totally relate to your experience. In the heat of the moment, I did heroin.
      It was great. I would use all the words you used to describe it, including all
      the 'red flags' and what not. It can almost seem insulting to ones level of
      control, if they truly do have that discipline.
    Challenging Drug Stereotypes and Advocating for Drug Liberalization/Harm Reduction:
    - Thanks for doing your best to combat the myth that you'll be ZOMG ADDICTED HOMELESS
      JUNKIE after one hit; it doesn't work that way. I've never done street heroin
      but I've experimented with plenty of similar drugs (particularly Oramorph, which
      is seriously good shit if you can get hold of it) to know what it'd be like.
      I can easily see how people get addicted to it though, it's one of the most
      intensely pleasurable experiences I've ever had I think, and I've tried a lot
      more than my fair share of drugs over the years. Definitely one for infrequent
      special occasions, isn't it!
    - Dude, fuck all the haters. I find it inconcievable that the same community that
      rallies behind marijuana legalization, the Portugal drug approach, and Ron Paul,
      is now going "ZOMG DRUGS R BAD 4 U!" I say good foor you, for trying something
      new. Good for you for experiencing life.
    - That's the second mind-fuck when it comes to doing drugs in the US. You learn
      that racism/classism is at the very core of drug laws. The first mind-fuck,
      as I've said in another thread, is realizing you've been lied to so hard about
      how dangerous drugs are. Thanks for writing about your experience.
    Specific Questions and Details about the Experience:
    - Wait, did you eat the fucking fries or what? Don't leave me hanging bro!
    - You seem very literate and self-conscious. Can you please describe the sensation
      with more detail? What kind of pleasure did you feel? How did it affect your
      state of mind and train of thought? Can you compare it to any familiar sensations
      (in quality, not intensity)?
    - Was there a bad hangover?
    - Did you get nauseous at all? You should be careful with that stuff.. I lost
      a brother to it.
    - Did you remember the whole experience, or do you feel like you were unaware
      during most of it? Do you feel like you could have done anything besides sitting
      on the couch while doing it (reading, writing, solving the world's problems
      etc)?
    Advice on Other Drugs / Comparisons:
    - Try LSD. mind expanding, zero addiction, life changing.
    - Sounds a lot like morphine. I had my elbow crushed, collarbone broken and I
      was in the worst pain of my life.. But after a shot of morphine I felt the best
      I ever have. I would not be doing shit like that recreationally though.
    - If you use a needle it is even BETTER. Using heroin as a recreational drug is
      just such a great idea.
    - compare and contrast with pot? Feelings, mental state, after effects, price?
- heading: Counterarguments
  bullets:
  - The OP's assertion of self-control and intent to resist future use is directly
    challenged by multiple experienced users who state that addiction "creeps up subtly"
    and that the urge to repeat the experience is a significant "red flag."
  - The OP's feeling of "pride" for trying heroin is seen as a dangerous rationalization
    that can lead to deeper involvement.
  - The OP's belief that they "can handle anything once" is countered by numerous
    personal accounts of lives ruined by initial experimentation.
  - The OP's claim that heroin is not the most addictive drug is disputed by many
    who call it "the most addictive drug on the planet" or emphasize its unique physical
    dependency.
  - The OP's plan to try cocaine as a potentially "safer" alternative is countered
    by warnings that cocaine also leads to addiction and overdoses, and that its perceived
    social acceptability is misleading.
  sub_sections: {}
- heading: Unresolved Questions
  bullets:
  - Did the OP eat the fries given by the dealer?
  - What was the specific sensation like, and how did it affect the OP's state of
    mind and train of thought, compared to familiar sensations?
  - Hypothetically, if addicted, what would be the first thing the OP would pawn for
    money?
  - Was there a bad hangover after the experience?
  - How often does the OP find themselves thinking about the experience since using?
  - Did the OP feel any negative physical or mental effects stemming from the use?
  - Why would the OP consider doing it again, despite acknowledging the risks?
  - If offered heroin next week, is the OP sure they would say no?
  - Did the OP get nauseous at all? (Some users claim vomiting is common).
  - Did the OP remember the whole experience, or feel unaware during most of it?
  - Could the OP have done anything besides sitting on the couch while high (e.g.,
    reading, writing, solving problems)?
  - Didn't seeing how much the drug ruins lives in "The Wire" put the OP off instead
    of inspire them?
  - How does the experience compare and contrast with pot in terms of feelings, mental
    state, after-effects, and price?
  - Has the OP walked by Washington Square Park again after the experience, and if
    so, how did they feel about it?
  - Is the OP normally a high-risk taker, or is this a phase?
  sub_sections: {}
- heading: Moderation Context
  bullets:
  - Rendered comments covered only part of the thread (103/761 visible; divergence
    86.47%).
  sub_sections: {}
metadata:
  source_type: reddit
  url: https://www.reddit.com/r/IAmA/comments/9ke63/i_did_heroin_yesterday_i_am_not_a_drug_user_and
  author: SpontaneousH
  date: null
  extraction_confidence: high
  confidence_reason: json endpoint ok; rendered=103/761 divergence=86.47%
  total_tokens_used: 9017
  gemini_pro_tokens: 0
  gemini_flash_tokens: 9017
  total_latency_ms: 41984
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: r/IAmA first-time heroin risks
    brief_summary: 'OP''s main point was The Original Poster (OP) intended to share
      their first-time heroin experience, inviting questions (AMA). The dominant replies
      focused on Warnings and Personal Stories of Heroin Addiction and Withdrawal.
      Consensus stayed around warnings and personal stories of heroin addiction and
      withdrawal while relatability and. Dissent centered on The OP''s assertion of
      self-control and intent to resist future use is. Caveat: The presence of `[deleted]`
      and `[removed]` comments indicates that moderation occurred within.'
    tags:
    - r-iama
    - experience-report
    - heroin
    - first-time
    - drug-experience
    - ama
    - addiction-risk
    - opiates
    - risk-taking
    - drug-culture
    detailed_summary:
      op_intent: The Original Poster (OP) intended to share their first-time heroin
        experience, inviting questions (AMA) from the community. They aimed to describe
        the intense pleasure felt, acknowledge the drug's addictive potential, and
        challenge common misconceptions and stereotypes about heroin users and drug
        dangers. The OP also explicitly stated an intent to try cocaine next to compare
        experiences and effects, viewing it as an "experiment and an adventure in
        life."
      reply_clusters:
      - theme: Warnings and Personal Stories of Heroin Addiction and Withdrawal
        reasoning: Many commenters, often self-identified former addicts, emphasize
          the extreme addictiveness of opioids, the severe pain of withdrawal, and
          how quickly addiction can develop. They share personal anecdotes of lives
          ruined, financial loss, and repeated overdoses, directly contradicting the
          OP's perceived self-control.
        examples:
        - Yes, opioid molecules are the best on earth. It really is the greatest feeling
          life has to offer. Unfortunately once I got a taste for that feeling it
          was all over. Opioids are the most addictive drugs on the planet. Once your
          body gets addicted the withdrawl is pure hell.
        - Don't do it again. EVER. There is nothing so terrible in this world as heroin
          withdrawals. It takes doing heroin every day or every other day for about
          2-3 months to get your first habit, but that time goes by real fast when
          you are high. Just leave it alone. Seriously.
        - Just wanted to let you know you sound exactly like I did when I first did
          dope. Same optimistic and sense of self control that us thrill seekers have.
          Smart enough to rationalize and study exactly what was going on. That was
          over $500,000 ago. I was a CIO and built a very successful company while
          hiding track marks. Eventually it all fell apart. I od'd several times and
          now I steal from my best friend and hate myself.
        - Fuck heroin. It's awesome. Too awesome. The only reason I stopped is because
          I lost two good friends to it. One died after an OD. The other is still
          alive, but he's all but a vegetable.
        - STOP! IMMEDIATELY!! I have done many many drugs, i've dealt drugs when I
          was younger, but I have never seen something ruin so many friends lives
          like herion, luckily I never got too into it, I always enjoyed speedier
          drugs and hallucinogens, but if you get hooked on herion, you get hooked
          and if you step up to injecting it is VERY hard to stop.
      - theme: Relatability and Shared First-Time Experiences (Often with a Cautionary
          Tone)
        reasoning: Several users relate to the OP's initial feelings of intense pleasure
          and the belief in their own self-control, often drawing parallels to their
          own first experiences with heroin or other strong drugs (like cocaine or
          prescription opiates). However, many of these comments still carry an underlying
          warning about the long-term risks.
        examples:
        - Your story sounds very similar to the way I first tried cocaine. I was a
          successful career guy as a creative professional in the TV business, and
          had never really used drugs. I tried it one night on a whim when a friend
          had some, and had a similarly incredible first-time experience. I had the
          same thoughts of "Wow, I see why people love this and get hooked. I won't
          do it any more though." Within a year I had become seriously addicted, depleted
          my savings account, run up several credit cards to the max, run from the
          cops while I was high, done a month in jail for evading arrest, had to move
          in with my parents, and lost my job and my career.
        - I just had my wisdom teeth out and got 10mg oxycodone pills, which is basically
          synthetic heroin. I took them for funzies a few times after my mouth healed.
          It was great, I felt on top of the world, outgoing, full of happiness and
          good will! That being said, and despite the fact I'd love to feel that way
          again, I have no intentions of ever searching for any, and if the opportunity
          came up I probably wouldn't even buy any. I completely understand the OP
          and can relate. Maybe we're just blessed with nonaddictive personalities.
        - I totally relate to your experience. In the heat of the moment, I did heroin.
          It was great. I would use all the words you used to describe it, including
          all the 'red flags' and what not. It can almost seem insulting to ones level
          of control, if they truly do have that discipline.
      - theme: Challenging Drug Stereotypes and Advocating for Drug Liberalization/Harm
          Reduction
        reasoning: Some commenters appreciate the OP's post for demystifying heroin
          or challenging the "automatic addict" narrative. They critique societal
          hypocrisy regarding drug use (e.g., alcohol vs. heroin, cocaine vs. heroin)
          and advocate for personal freedom or drug legalization.
        examples:
        - Thanks for doing your best to combat the myth that you'll be ZOMG ADDICTED
          HOMELESS JUNKIE after one hit; it doesn't work that way. I've never done
          street heroin but I've experimented with plenty of similar drugs (particularly
          Oramorph, which is seriously good shit if you can get hold of it) to know
          what it'd be like. I can easily see how people get addicted to it though,
          it's one of the most intensely pleasurable experiences I've ever had I think,
          and I've tried a lot more than my fair share of drugs over the years. Definitely
          one for infrequent special occasions, isn't it!
        - Dude, fuck all the haters. I find it inconcievable that the same community
          that rallies behind marijuana legalization, the Portugal drug approach,
          and Ron Paul, is now going "ZOMG DRUGS R BAD 4 U!" I say good foor you,
          for trying something new. Good for you for experiencing life.
        - That's the second mind-fuck when it comes to doing drugs in the US. You
          learn that racism/classism is at the very core of drug laws. The first mind-fuck,
          as I've said in another thread, is realizing you've been lied to so hard
          about how dangerous drugs are. Thanks for writing about your experience.
      - theme: Specific Questions and Details about the Experience
        reasoning: Users ask for more descriptive details about the sensation, the
          aftermath, or specific aspects of the OP's encounter.
        examples:
        - Wait, did you eat the fucking fries or what? Don't leave me hanging bro!
        - You seem very literate and self-conscious. Can you please describe the sensation
          with more detail? What kind of pleasure did you feel? How did it affect
          your state of mind and train of thought? Can you compare it to any familiar
          sensations (in quality, not intensity)?
        - Was there a bad hangover?
        - Did you get nauseous at all? You should be careful with that stuff.. I lost
          a brother to it.
        - Did you remember the whole experience, or do you feel like you were unaware
          during most of it? Do you feel like you could have done anything besides
          sitting on the couch while doing it (reading, writing, solving the world's
          problems etc)?
      - theme: Advice on Other Drugs / Comparisons
        reasoning: Some users suggest trying other drugs (often psychedelics) as alternatives
          to heroin, or offer comparisons between heroin and other substances.
        examples:
        - Try LSD. mind expanding, zero addiction, life changing.
        - Sounds a lot like morphine. I had my elbow crushed, collarbone broken and
          I was in the worst pain of my life.. But after a shot of morphine I felt
          the best I ever have. I would not be doing shit like that recreationally
          though.
        - If you use a needle it is even BETTER. Using heroin as a recreational drug
          is just such a great idea.
        - compare and contrast with pot? Feelings, mental state, after effects, price?
      counterarguments:
      - The OP's assertion of self-control and intent to resist future use is directly
        challenged by multiple experienced users who state that addiction "creeps
        up subtly" and that the urge to repeat the experience is a significant "red
        flag."
      - The OP's feeling of "pride" for trying heroin is seen as a dangerous rationalization
        that can lead to deeper involvement.
      - The OP's belief that they "can handle anything once" is countered by numerous
        personal accounts of lives ruined by initial experimentation.
      - The OP's claim that heroin is not the most addictive drug is disputed by many
        who call it "the most addictive drug on the planet" or emphasize its unique
        physical dependency.
      - The OP's plan to try cocaine as a potentially "safer" alternative is countered
        by warnings that cocaine also leads to addiction and overdoses, and that its
        perceived social acceptability is misleading.
      unresolved_questions:
      - Did the OP eat the fries given by the dealer?
      - What was the specific sensation like, and how did it affect the OP's state
        of mind and train of thought, compared to familiar sensations?
      - Hypothetically, if addicted, what would be the first thing the OP would pawn
        for money?
      - Was there a bad hangover after the experience?
      - How often does the OP find themselves thinking about the experience since
        using?
      - Did the OP feel any negative physical or mental effects stemming from the
        use?
      - Why would the OP consider doing it again, despite acknowledging the risks?
      - If offered heroin next week, is the OP sure they would say no?
      - Did the OP get nauseous at all? (Some users claim vomiting is common).
      - Did the OP remember the whole experience, or feel unaware during most of it?
      - Could the OP have done anything besides sitting on the couch while high (e.g.,
        reading, writing, solving problems)?
      - Didn't seeing how much the drug ruins lives in "The Wire" put the OP off instead
        of inspire them?
      - How does the experience compare and contrast with pot in terms of feelings,
        mental state, after-effects, and price?
      - Has the OP walked by Washington Square Park again after the experience, and
        if so, how did they feel about it?
      - Is the OP normally a high-risk taker, or is this a phase?
      moderation_context: Rendered comments covered only part of the thread (103/761
        visible; divergence 86.47%).
    _dense_verify:
      missing_fact_count: 0
    op_author: u/SpontaneousH
    route_subtype: reddit
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
    fallback_reason: gemini-2.5-flash-unavailable
  fallback_reason: gemini-2.5-pro-rate-limited

```

### ATOMIC FACTS
```yaml
- claim: The original poster (OP) tried Heroin yesterday.
  importance: 5
- claim: The OP experienced the most pleasurable feeling of pure relaxation and bliss,
    like a full body orgasm times 10 that kept going on and on.
  importance: 5
- claim: Heroin is described as "pure powdered pleasure" by the OP.
  importance: 5
- claim: The OP does not regret trying heroin at all.
  importance: 5
- claim: The OP plans to try cocaine next to compare its effects and experience with
    heroin.
  importance: 5
- claim: Opioids are described as the most addictive drugs on the planet, with withdrawal
    being "pure hell."
  importance: 5
- claim: A former heroin addict strongly advises against ever doing heroin again,
    stating there is nothing as terrible as heroin withdrawals.
  importance: 5
- claim: A former cocaine addict's life spiraled into serious addiction, depleted
    savings, maxed credit cards, jail time, and loss of job/career within a year of
    trying it.
  importance: 5
- claim: A former CIO and successful company builder lost everything, overdosed multiple
    times, and now steals from friends due to heroin addiction, despite initially
    having self-control.
  importance: 5
- claim: Heroin can lead to severe negative consequences, including friends dying
    from overdose or becoming brain-damaged vegetables.
  importance: 5
- claim: The lasting psychological impact of heroin means one will be "chasing that
    proverbial dragon for the rest of your life," even if not physically addicted.
  importance: 5
- claim: A former user strongly advises anyone curious to "skip heroin and skip meth."
  importance: 5
- claim: The OP is 24, has a masters degree, and a well-paying full-time job, and
    had never been a drug user beyond occasional pot in their teens.
  importance: 4
- claim: The OP's motivation for trying heroin was feeling that life had been boring
    and a desire to take risks or do something crazy.
  importance: 4
- claim: The OP was high for about 10 hours straight and was "blown away by the power
    of this drug."
  importance: 4
- claim: The OP now understands why people get hooked on drugs, acknowledging the
    addiction potential and how it can easily tear apart lives.
  importance: 4
- claim: The OP feels proud for doing something crazy and views the experience as
    a valuable life experience and a "window into another world."
  importance: 4
- claim: The OP states they have the urge to do heroin again but will resist, "at
    least not for a long time."
  importance: 4
- claim: Addiction to opioids can develop quickly, potentially within a week.
  importance: 4
- claim: Heroin addiction "creeps up on you in such a subtle way that you won't notice
    until you're already in deep," with warning signs including planning use, constantly
    thinking about the feeling, or never forgetting the first time.
  importance: 4
- claim: Doing heroin alone, especially for the first time, is dangerous and can lead
    to death.
  importance: 4
- claim: The first time using heroin is always by far the best, and subsequent uses
    are an attempt to achieve that initial high.
  importance: 4
- claim: Heroin is "awesome, too awesome," making it dangerous due to its intense
    pleasure.
  importance: 4
- claim: The negative environment associated with heroin use can involve theft and
    indifference to others' overdoses.
  importance: 4
- claim: Heroin has ruined many of a commenter's friends' lives, and injecting it
    makes it very hard to stop.
  importance: 4
- claim: Risks of buying and using street drugs include being shot, attacked, arrested,
    or the drug being tainted or miscalculated, leading to overdose.
  importance: 4
- claim: The OP was initially looking to buy pot but was offered heroin by a dealer
    who only sold larger quantities of pot, coke, or H.
  importance: 3
- claim: The OP googled "how to snort Heroin" to learn how to use it, as they only
    knew about injecting from movies.
  importance: 3
- claim: The experience of nodding off on heroin felt like being in a "pure conscious
    lucid dream like state," sometimes like leaving the body.
  importance: 3
- claim: Some believe that heroin is not the most addictive drug, and many people
    who try it do not get addicted, with speed/meth being considered "instant addiction."
  importance: 3

```

### SOURCE
```
Post
I did Heroin yesterday. I am not a drug user and have never done anything besides pot back when I was a teen, AMA (this is a little long) I have never been a drug user, I drink once in a while and smoked pot years ago back when I was a teen in highschool a few times and that's it. I'm 24 now, have a masters and a well paying full time job. Yesterday I was walking throgh Washington Square Park where I pass every day and there are always people there looking to sell drugs (not in the park anymore due to cameras, but it is well known you can meet a dealer than and do the transaction elsewhere these days). They usually don't solicit drugs to you unless you stop to stand around near one of them for some reason or look like you're looking for something. Yesterday I happened to stop by a row of benches to check some messages on my phone when a dealer on the bench to my right asks me if I need anything. My life has been pretty boring the last few years and I feel like I haven't really lived, taken any risks, or done anything crazy so I figured what the hell maybe I'll buy some pot, it's been a while. I said yeah and after asking my several times if I'm a cop he gives me his number and tells me to meet him at a fast food place several blocks away and he will 'hook me up.' I say alright and nervously check to make sure I have cash and go meet this shady looking dude. We sit down and after hounding me asking if I'm a cop he asks what I need, I tell him I just want a dime bag and he says something like "Naw sorry man, I only sell half ounces, you can take that and I've got some coke and H." At this point I didn't want to buy half an ounce of pot, I probably never smoked more than an eighth in my life but then I started considering his last word, Heroin. I've heard so much about it and how crazy addictive it is and seen it in the movies and TV (I'm thinking The Wire here, one of my favorite shows) and it really started to intrigue me. I've always wondered what it would be like to do Heroin. Out of no where I say I'll take the H and we do the deal there. I give him the cash under the table and he slides me a small order of fries with a little stamped wax baggie in it then he tells me to let him leave first. I put it in my pocket then nervously race home my heart racing cannot believing what I just did. I held onto that bag in my pocket palms sweating the whole ride home. When I get home I open the bag and dump some golden flakes and powder on my glass coffee table. At this point I don't even know what to do, I know you can snort heroin but it looked all flaky so I try to remember how they did it in the movies but they always seem to inject it in film so I start googling "how to snort Heroin' like an idiot and do a little research on the stuff and how much to take. I used a card to get it into a fine powder and move a small 'bump' to the side which I inhaled through a dollar bill. I didn't feel anything yet so I snorted a small line which was essentially half the bag (there was very little inside). I waited and in a few minutes I had the most pleasurable feeling of pure relaxation and bliss wash over me. I just sat there and everything felt amazing. I nodded off and it was great, I had the TV on but wasn't paying attention, I must have sat around for 4 hours doing nothing but feel total pleasure. It was like a full body orgasm times 10 that kept going on and on. When I would nod off it felt like I was in a pure conscious lucid dream like state, sometimes it felt like I was leaving my body. At this point I did the rest of it and stayed up all night and must have been high for 10 hours straight. i might have slept at one point, it's hard to tell the difference when you nod off and everything feels good regardless, just the feeling of being under a blanket was amazing. I was blown away by the power of this drug and just how orgasmic it felt. I never understood why people did drugs before and got so hooked on them but now I see why. I have the urge to do it again but I will resist and not do it, at least not for a long time. I understand the addiction potential and how someone could easily tear apart their lives with this stuff. Heroin is pure powdered pleasure, I actually feel proud of myself for having the balls to do something this crazy and I feel like it was a valuable life experience and my window into another world and part of society. I will never forget the day I did heroin. Now, ask me anything. **New Edit: I have a lot of respect for most posters and drug addicts with experience here but this Redditor/addict is why people have the negative stereotypes they do about junkies:** http://www.reddit.com/r/IAmA/comments/9ke63/i_did_heroin_yesterday_i_am_not_a_drug_user_and/c0d6prn **Edit: Please no more comments telling me I'm going to be a homeless addict dying of an overdose now, don't lecture me with all of your misconceptions and lack of any real knowledge or experience about the drug. I understand if you know someone who has been hurt by it, we all do. Any drug can ruin lives, please ask me questions instead of trying to lecture me and do some research first before spewing lies.** ========================================== **Update 2: I don't regret this at all and I see a lot of talk about how cocaine isn't as bad as heroin and people telling anyone considering trying a hard drug to do coke instead. I've known and seen a lot of heavy coke users, many who have become addicted and ODed and I find it disturbing that people think coke is acceptable because some 'higher class' circles find it socially acceptable. I'm thinking the young Wall Street and college crowds here who associate it with money and being cool and is easily manageable to use for recreation, while society tells them that Heroin is for the poor and destitute and leads to automatic addiction and suffering.** **So I plan to try cocaine the next chance I get and compare the two in terms of effects and experience. Doing Heroin was memorable and life changing and I know I can handle anything once. I've done my research on coke and know the risks, so if anyone has any questions or opinions on that matter feel free to chime in. Whether it is to tell me I'm a fucking idiot or to give me advice, whatever. This is an experiment and an adventure in life, I'll report back once I try it.** https://www.reddit.com/r/IAmA/comments/9ke63/i_did_heroin_yesterday_i_am_not_a_drug_user_and/

Comments
NeverTrustAJunky: Wait, did you eat the fucking fries or what? Don't leave me hanging bro! [deleted]: [removed] formfactor: Yes, opioid molecules are the best on earth. It really is the greatest feeling life has to offer. Unfortunately once I got a taste for that feeling it was all over. Opioids are the most addictive drugs on the planet. Once your body gets addicted the withdrawl is pure hell. Be very very careful not to injest the stuff everyday, because in a matter of a week you will be completely addicted. I was very suprised at how fast my tolerance went up and how fast I was craving the stuff. I'm now clean of it and taking suboxone to minimize the withdraw. It has helped, but I can never take another opioid without my body going completely crazy for it. Enjoy it, but save it for a rainy day. Opioid addict... [deleted]: Not a question just a suggestion based on knowledge gained from years as a heroin addict. Don't do it again. EVER. There is nothing so terrible in this world as heroin withdrawals. It takes doing heroin every day or every other day for about 2-3 months to get your first habit, but that time goes by real fast when you are high. Just leave it alone. Seriously. topicproman: Hello... Your story sounds very similar to the way I first tried cocaine. I was a successful career guy as a creative professional in the TV business, and had never really used drugs. I tried it one night on a whim when a friend had some, and had a similarly incredible first-time experience. I had the same thoughts of "Wow, I see why people love this and get hooked. I won't do it any more though." Within a year I had become seriously addicted, depleted my savings account, run up several credit cards to the max, run from the cops while I was high, done a month in jail for evading arrest, had to move in with my parents, and lost my job and my career. The difficulty with addiction and drugs is... erez27: You seem very literate and self-conscious. Can you please describe the sensation with more detail? What kind of pleasure did you feel? How did it affect your state of mind and train of thought? Can you compare it to any familiar sensations (in quality, not intensity)? test450: Just wanted to let you know you sound exactly like I did when I first did dope. Same optimistic and sense of self control that us thrill seekers have. Smart enough to rationalize and study exactly what was going on. That was over $500,000 ago. I was a CIO and built a very successful company while hiding track marks. Eventually it all fell apart. I od'd several times and now I steal from my best friend and hate myself. dynamicinterface: hypothetically, if you were to become addicted, what would be the first thing you'd pawn off for money? hardman52: Heroin is not the most addictive drug. Most people I've known who've tried it didn't get addicted. Now speed-crank-meth, whatever you want to call it, THAT'S an instant addiction. All drugs are dangerous, and I don't know of anyone whose life was better by taking them. I say this having done drugs for 14 years when I was young. I've done LSD 400+ times, speed for a couple of years, and various other drugs, including heroin. I haven't done drugs for more than 30 years now, and if it were possible to go back and never to have done them, I would. I also think they should all be legal, except for speed, which causes violence. miasma66: Thanks for doing your best to combat the myth that you'll be ZOMG ADDICTED HOMELESS JUNKIE after one hit; it doesn't work that way. I've never done street heroin but I've experimented with plenty of similar drugs (particularly Oramorph, which is seriously good shit if you can get hold of it) to know what it'd be like. I can easily see how people get addicted to it though, it's one of the most intensely pleasurable experiences I've ever had I think, and I've tried a lot more than my fair share of drugs over the years. Definitely one for infrequent special occasions, isn't it! demogorgon: I have been a chronic pain patient for over 5 years. I get tons of opiates (hydromorphone) every week so I don't have much of a need to look for anything on the street, although I have always been curious about H. If I tried all I could get here would be some tar. My girlfriend (who also uses my reddit accounts) Is a recovering addict from heroin, and tells me again and again how H is a whole different game than my prescriptions. I don't doubt IV use is. It has caused some struggle, her being a recovering addict while I need to be on prescription opiates (which I do abuse sometimes...) anyways just thought I'd throw in a small part of my story. And by the way, given the chance I'd never hav... [deleted]: Try LSD. mind expanding, zero addiction, life changing. Pierce the illusion. coleman57: sheesh, sure are an awful lot of ninnies on this thread. i chased the dragon in amsterdam in 1987. after the hurling was over, the "i couldn't possibly care about anything" feeling kicked in, and i'll never forget it. someone could have told me my mother had just died a horrible death and i would have said "oh...that's too bad." really, it is another plane of existence. i can see why people who have chronic psychological or physical pain get addicted to it. i however, had something to live for, so i never did it again. but i don't regret it one bit, just like you. [deleted]: I'm seeing red flags all over your post, like this: &gt;I have the urge to do it again but I will resist and not do it, **at least not for a long time.** &gt;I actually **feel proud of myself** for having the balls to do something this crazy and I feel like it was a valuable life experience The problem with opiate addiction is that it creeps up on you in such a subtle way that you won't notice until you're already in deep. When you find yourself planning out a schedule for using, you're already in trouble. When you're thinking about how good it felt, you're already in trouble. When you'll never forget the day you first tried it, you're already in trouble. The only way to win is by not playi... qapmoc: Sounds a lot like morphine. I had my elbow crushed, collarbone broken and I was in the worst pain of my life.. But after a shot of morphine I felt the best I ever have. I would not be doing shit like that recreationally though. [deleted]: [deleted] 1234qwert: ....And so SpontaneousH turned half of reddit.com into heroin addicts with his story about a 8-10hr full body orgasm. nwootten: Was there a bad hangover? [deleted]: You are treading dangerous waters. (Obligatory warning) On to the questions: How often do you find yourself thinking about your experience since your use? Did you feel any negative effects stemming from your use? Physically/mentally? Why would you consider doing it again? As an experienced drug user you are showing signs that could lead you to addiction. Just be careful and do considerable research on what you're planning to do before you do it. Yeah I see you don't really want to be lectured about how it can ruin lives, but you really are messing with something that can kill you. I've had multiple friends OD and die using it. I went the other route, I chose cocaine. As a rule of thumb hero... joe12321: Just curious... I know you don't intend to do it again, but if by some wacky happenstance you run across someone who offers you heroin next week, are you sure you'd say no? [deleted]: &gt; Update 2: I don't regret this at all and I see a lot of talk about how cocaine isn't as bad as heroin and people telling anyone considering trying a hard drug to do coke instead. I've known and seen a lot of heavy coke users, many who have become addicted and ODed and I find it disturbing that people think coke is acceptable because some 'higher class' circles find it socially acceptable. That's the second mind-fuck when it comes to doing drugs in the US. You learn that racism/classism is at the very core of drug laws. The first mind-fuck, as I've said in another thread, is realizing you've been lied to so hard about how dangerous drugs are. Thanks for writing about your experience. I... scarrister: I think you have a masters in creative writing. philliez: Wow... now I'm tempted to do an IAMA for my meth years. One thing I don't get though: what's with the alternate accounts to admit things and answer questions? No reason to be so ashamed IMO pungen: thanks a lot for posting this. ive been offered heroin a bunch and would never try it, but i've always wondered what it was like from the perspective of a non-addict. just curious - a junkie i used to know told me people throw up right after they do heroin every time. did this happen to you? was the person lying? flixer: It's really stupid to do heroin alone, especially the first time. Don't do that again! People die! Trust me, I know. I used to do heroin 15 or so years ago; I loved it. So did my friends, some of whom are dead or brain-damaged from it now. I really think it's a dangerous drug. I'm all for legalizing EVERYTHING, but I really think that's one thing that's best avoided. Be careful, man. drgreedy911: If you use a needle it is even BETTER. Using heroin as a recreational drug is just such a great idea. [deleted]: [deleted] [deleted]: Did you remember the whole experience, or do you feel like you were unaware during most of it? Do you feel like you could have done anything besides sitting on the couch while doing it (reading, writing, solving the world's problems etc)? I have always been partial to psychedelics myself for the creativity/self discovery. whentoastersfly: So which is worse - heroin or Reddit? [deleted]: Didn't seeing how much the drug ruins lives in The Wire put you off instead of inspire you? AnythingApplied: The first time is always by far the best. Every other time is just trying to achieve the high of your first time trying it. You will never have that experience again, however hard you pursue it. heyzeuschristo: I have to agree with you, OP, the feeling of heroin is second to none. I had done a fair share of opiates in college. I started with crappy mid-grade weed in high school, eventually graduating to designer strains as a freshman in college. I soon found myself bored with weed, so while I continued to smoke it a ton, I also started to dabble in coke, hallucinogens, and opiates. Coke was never my thing, I don't like uppers. LSD and mushrooms were a joy, but is never something I have craved. Doing oxycontin for the first time was eye-opening. I was hanging with a neighbor late night one Saturday who offered me a 20mg to "chill out." I made the mistake of snorting it all within a few minutes. WOW... abuckley77: Whether you know it or not, whether you become addictted or not, you will be chasing that proverbial dragon for the rest of your life. I've seen it happen to so many kids i grew up with, who keep chasing that feeling or whatever it is happened to them when they did heroin or OCs. I myself have struggled through addiction in my teens. It might not even be drug related, but that physiological and psychological feeling will be something you try to mimic, it will be a driving force in your life. I hope these are all just assumptions, and i know that you have a great solid life, but that is what concerns me the most. You have so much to lose, please don't try it again, not even once. Magento: I'm not entierly sure how to phrase my question, so feel free to answer any way you like. Looking back at it, do you find heroin "boring"? I did opium in Thailand lots of years ago. Smoked a bunch and to me it was the least rewarding drug I've ever taken. Yes, I did get a pleasant feeling, but since I had already escaped all my troubles and was already in a happy state of mind it didn't change much for me. Smoking pot can make me raving mad, going into a full on psycosis, seeing god and solving world problems. Acid or stronger stuff I can't even touch because my brain is to fragile. Speed and coke just makes me "normal", but at least it gives me the feeling of having superpowers. Getting dr... american_idle: Fuck heroin. It's awesome. Too awesome. The only reason I stopped is because I lost two good friends to it. One died after an OD. The other is still alive, but he's all but a vegetable. He just started walking last year with the help of a walker. He still can't speak, shakes like he's got Parkinson's. (And this guy used to be a star 225lb wrestler in high school and college all paid for by a scholarship.) We were just kids having fun. Fuck heroin. (All of that happened in a month and I have to admit I still *want* it.) Smoke weed, drink, take shrooms, whatever. Have fun w/ all of that shit. But if I could give any advice to the curious it would be this: skip heroin and skip meth. Those are... IWillKickU: Dude, fuck all the haters. I find it inconcievable that the same community that rallies behind marijuana legalization, the Portugal drug approach, and Ron Paul, is now going "ZOMG DRUGS R BAD 4 U!" I say good foor you, for trying something new. Good for you for experiencing life. Obviously there are risks, but come the fuck on, we have a [whole subreddit](http://reddit.com/r/beer) for a drug that's more addictive than H, kills more people, leads to more criminal behavior, and takes more lives. All of you cautionary tales folks need to go there and post 57 comments in every thread warning them of alchohalism. I've never done H, or any drug harder than pot, but if I did and opened the floor f... cbranden: Your experience sounds just about like 
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): 4ad30e58884bed36cbbdbb12e7044f49f6cb05308aa207f8851b90f6299495f9
