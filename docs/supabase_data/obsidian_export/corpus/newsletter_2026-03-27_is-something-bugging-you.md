---
title: "Is something bugging you?"
source_type: newsletter
source_url: "https://news.ycombinator.com/item?id=39356920"
status: processed
fetch_timestamp: "2026-03-27T23:46:59.815825+00:00"
gemini_tokens_used: 6395
gemini_latency_ms: 13984
tags:
  - "source/newsletter"
  - "domain/Software Engineering"
  - "domain/Management"
  - "domain/Organizational Behavior"
  - "domain/Productivity"
  - "domain/Team Dynamics"
  - "type/Discussion"
  - "type/Opinion"
  - "type/Anecdote"
  - "difficulty/Intermediate"
  - "status/Processed"
  - "keyword/10x developer"
  - "keyword/productivity"
  - "keyword/management"
  - "keyword/software culture"
  - "keyword/team dynamics"
  - "keyword/Unix philosophy"
  - "keyword/incentives"
  - "keyword/technical debt"
metadata:
  bypass_used: "None"
---

# Is something bugging you?

> This discussion redefines "10x productivity" as innovative leverage rather than long hours, critiques how industry management often misidentifies and misrewards true value, and explores the negative team impacts of focusing on individual "rockstars" versus fostering cohesive, process-oriented teams.

**Source:** [newsletter](https://news.ycombinator.com/item?id=39356920)

## Summary

## Defining True 10x Productivity
*   **Beyond Hours Worked:** The concept of a "10x" or "50x" developer is not about working excessive hours leading to burnout.
*   **Leverage through Novel Solutions:** True high productivity comes from implementing solutions that few others considered or understood, providing immense leverage to deliver working software in a fraction of the time.

## Industry Practices and Misaligned Incentives
*   **Recognition Gap:** The industry would see more 10x behavior if it were recognized and rewarded more often.
*   **Focus on Effort over Efficiency:** Management often prioritizes developers who work long hours (e.g., 12-hour days for 8 hours of real work) over those who achieve the same results efficiently (e.g., in 8 hours).
*   **Discouraging Process Improvement:** Deviations from 'normal' are frowned upon; time spent improving processes (e.g., "building a wheelbarrow") is discouraged in favor of immediate, less efficient work (e.g., "hauling buckets faster").

## The Perverse Outcomes of Rewarding "10x Individuals" (Cobra Effect)
*   **Negative Team Dynamics:** Rewarding "10x" individuals can lead to perverse results on a wider scale, negatively impacting team productivity.
    *   **Case Study:** A "management-enabled, long-tenured '10x' rockstar" quickly fixed major customer-facing bugs but simultaneously created multiple smaller bugs and regressions for other developers to fix.
        *   This made other developers appear less productive (0.7x) compared to the "rockstar."
        *   The "rockstar" was allowed to bypass rules (e.g., "No Unsafe Rust" rule was optional for him), leading to a codebase portion only he could work on.
        *   Organizations must be cautious in measuring productivity, looking beyond first-order metrics.
    *   **Toxic Environment:** A similar experience involved a "principal" engineer whose fast, bug-ridden work was automatically blessed by management.
        *   This led to junior engineers cleaning up his messes, which was rarely acknowledged.
        *   The "principal" engineer became insecure, attempting to push out perceived threats, leading to a toxic environment and the resignation of multiple senior engineers.

## Management Competence and "Software Literacy"
*   **Illiterate Managers:** The issue is framed as "a bunch of illiterate managers are impressed with one good writer at the encyclop(a)edia publishers, now it turns out this guy makes mistakes, but hey, what do you expect when the management cannot read or write!"
*   **Clueless about Work:** Managers are often clueless about the actual work, making it difficult for them to discern true productivity or value.
*   **Short Memories:** Even smart managers seem to have short memories regarding past successes and 10x contributions.
*   **Leadership Gaps:** Many individuals without core technical competence end up in leadership, removing "perceived threats" to their authority.
    *   **Proposed Solution:** Engineering curricula should include leadership courses focusing on influence and power.
    *   **Consultants:** Consultants are often brought in when leadership lacks core competence or cannot handle internal backlash.
*   **Financial Myopia:** Executives struggle to think in terms of "Return on Equity" for future investments, focusing instead on short-term costs.

## The Value of Process Improvement and Simplicity
*   **"Sharpening the Axe" Philosophy:** Emphasizes spending significant time on upfront design, documentation, and thought processes before execution (e.g., "Give me six hours to chop down a tree and I will spend the first four sharpening the axe.").
    *   **Stakeholder Management:** Keeping anxious managers/clients updated on the design process and risks of alternative paths helps maintain their peace of mind.
*   **The Unix Philosophy (Bentley-McIlroy Story):** An anecdote illustrating the power of simple, composable tools over complex, custom-built solutions.
    *   **Context:** Jon Bentley asked Donald Knuth to write a complex program in Pascal (WEB) to illustrate literate programming. Doug McIlroy (inventor of Unix pipes) reviewed it, questioning the need for a custom program and demonstrating how the same task could be done in ~10 developer minutes using shell pipes, sort, and other Unix tools.
    *   **Enlightenment:** This story highlights how a programmer's philosophy can shift from complex Object-Oriented Design (OOD) to simple, elegant shell scripts for certain tasks.

## Cultivating 10x Engineers and Teams
*   **Team Cohesion:** A cohesive team that sticks together for more than two years can be significantly more productive than a group of talented individuals with high turnover.
*   **Experience as a Factor:** "10x engineers" often have significantly more practical programming experience (e.g., 20 years by age 30) due to starting coding at a young age (e.g., 12) and gaining professional experience early.
*   **Personal Anecdote (10x Junior Engineer):** A junior engineer, with early exposure to programming and best practices, transformed a small medical device software company's culture by:
    *   Introducing unit tests.
    *   Implementing proper version control (CVS with branches).
    *   Automating build scripts.
    *   Developing a JUnit-inspired testing framework for their specific language (IDL).
    *   This work enabled the company to scale beyond 3-4 developers and influenced hiring for test-oriented engineers.

## General Observations and Industry Truths
*   **Programmers are Not "Dime a Dozen":** An anecdote where a manager who claimed "Programmers are a dime a dozen" later faced client refusal to work without the specific developer he mistreated, highlighting the value of indispensable talent.
*   **"Rescuing Small Companies From Code Disasters":** Experience in fixing messes created by incompetent teams and management, often by replacing entire projects with 1/100 the code, resulting in smaller, tighter, faster, and more flexible solutions.
*   **Critique of Rewarding "10x Behavior":** One perspective argues that simply rewarding 10x behavior won't increase it, as many developers are "crap" or are in environments that reward "motion with action" rather than true productivity, especially at the "just above individual contributor" management layer.
*   **Unverified Claim:** The reposted "Parable of the Two Programmers" is described as potentially "fiction" without more backup, though acknowledged as "righteous fiction" that resonates with industry truths.
*   **Grammar Correction:** A minor point about the correct usage of "to tire" as a verb, noting an unnecessary correction in a linked story.


## Related Notes
- [[github_2026-03-28_markuspfundsteinmcp-obsidian-a5204a]]

- [[youtube_2026-03-27_notebooklm-changed-completely-heres-what-matters-in-2026]]
