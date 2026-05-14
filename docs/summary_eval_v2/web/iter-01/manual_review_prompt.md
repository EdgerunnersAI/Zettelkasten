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
## URL 1: https://peps.python.org/pep-0701/

### SUMMARY
```yaml
mini_title: 'PEP 701: F-string Grammar Formalization'
brief_summary: PEP 701, accepted for Python 3.12, formalizes the f-string grammar
  and integrates it into the parser. This change lifts previous restrictions from
  PEP 498, reduces maintenance costs of the old C parsing code, and allows f-strings
  to benefit from the new PEG parser's improved error messages, enhancing consistency
  and ease of implementation for alternative Python versions.
tags:
- python
- pep-701
- f-strings
- grammar
- parser
- python-3.12
- peg-parser
- language-features
- syntax
detailed_summary:
- heading: Core Proposal and Motivation
  bullets:
  - "PEP 701, authored by Pablo Galindo Salgado, Batuhan Taskaya, Lysandros Nikolaou,\
    \ and Marta G\xF3mez Mac\xEDas, was accepted on March 14, 2023, for Python 3.12."
  - The proposal formalizes the f-string grammar and integrates it directly into the
    parser, lifting restrictions from the original PEP 498 specification.
  - Motivation includes reducing the high maintenance cost of the existing hand-written
    C parsing code, which is error-prone.
  - The change allows f-strings to benefit from the improved error messages of the
    new PEG parser (PEP 617).
  - The old system, which tokenized f-strings as a single `STRING` token for post-processing,
    created inconsistencies and difficulty for alternative Python implementations
    like PyPy.
  sub_sections: {}
- heading: Restrictions Lifted from PEP 498
  bullets:
  - PEP 498's implementation-driven limitations, which a previous attempt in PEP 536
    failed to lift, are removed by PEP 701.
  sub_sections:
    Previous Restrictions:
    - Prohibiting the use of the same quote character within an f-string's expression
      part (e.g., `f' { bag['wand'] }'` ).
    - 'Forbidding backslashes (e.g., `f"{''

      ''.join(a)}"`).'
    - Disallowing comments (`#`) inside expressions.
    PEP 701 Changes:
    - Removes these restrictions, allowing any valid Python expression inside an f-string's
      braces.
    - Aligns Python with other languages like Ruby, JavaScript, Swift, and C# that
      permit arbitrary expression nesting in string interpolation.
- heading: Technical Implementation with PEG Grammar
  bullets:
  - The new formal PEG grammar introduces three new tokens.
  sub_sections:
    New Tokens:
    - '`FSTRING_START`: Represents the prefix and opening quote.'
    - '`FSTRING_MIDDLE`: Represents text between expressions.'
    - '`FSTRING_END`: Represents the closing quote.'
    Tokenizer Update:
    - The `tokenize` module will be updated to emit these new tokens.
    - This new tokenization scheme requires a lexer that can handle nested modes.
    - The PEP provides a draft algorithm for adapting a CPython-like tokenizer.
- heading: User Impact and Debates
  bullets:
  - Users can now reuse the same quote type inside an f-string expression.
  - Users can now use backslashes and newlines within f-string expressions.
  - Users can add comments inside f-string expressions, provided the closing brace
    is on a new line.
  sub_sections:
    Quote Reuse Debate:
    - Debated due to potential readability issues and challenges for simple syntax
      highlighters (like IDLE's).
    - Retained because it simplifies code generators (e.g., `ast.unparse`).
    - Provides "referential transparency".
    - Forbidding it would significantly complicate the PEG parser implementation.
    - The PEP authors argue that readability concerns should be addressed by linters.
- heading: Backwards Compatibility and Specifics
  bullets:
  - The proposal maintains backwards compatibility and does not introduce semantic
    changes to existing code.
  - The Abstract Syntax Tree (AST) is preserved.
  sub_sections:
    Debug Feature:
    - The f-string debug feature (`f"{expr=}"`), introduced in Python 3.8 (bpo-36817),
      is unaffected.
    - Its formal handling requires the lexer to preserve the raw string of the expression,
      including whitespace.
    Nesting Levels:
    - 'The PEP specifies minimum nesting levels for implementations: 5 for f-strings
      and 2 for format specifiers (e.g., `f"{'''':*^{1:{1}}}"` must be valid).'
    - The language specification does not mandate arbitrary nesting, allowing implementations
      to set their own limits, similar to existing CPython limits on parenthesis or
      block nesting.
- heading: Rejected Ideas and Simplified Teaching
  bullets:
  - Certain ideas were considered but ultimately rejected or deferred.
  sub_sections:
    Rejected Ideas:
    - Lifting the restriction on top-level `:` and `!` characters (e.g., for lambdas)
      remains to avoid tokenizer complexity.
    - Allowing escaped braces (`\{`, `\}`) was deferred as a separate issue.
    Simplified Teaching:
    - 'The new way to teach f-strings is simplified to: "You can place any valid Python
      expression inside an f-string expression, and everything after a `:` character
      at the top level will be identified as a format specification."'
metadata:
  source_type: web
  url: https://peps.python.org/pep-0701/
  author: null
  date: null
  extraction_confidence: high
  confidence_reason: HTML article text extracted
  total_tokens_used: 2455
  gemini_pro_tokens: 0
  gemini_flash_tokens: 2455
  total_latency_ms: 48360
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: 'PEP 701: F-string Grammar Formalization'
    brief_summary: PEP 701, accepted for Python 3.12, formalizes the f-string grammar
      and integrates it into the parser. This change lifts previous restrictions from
      PEP 498, reduces maintenance costs of the old C parsing code, and allows f-strings
      to benefit from the new PEG parser's improved error messages, enhancing consistency
      and ease of implementation for alternative Python versions.
    tags:
    - Python
    - PEP 701
    - f-strings
    - grammar
    - parser
    - Python 3.12
    - PEG parser
    - language features
    - syntax
    detailed_summary:
    - heading: Core Proposal and Motivation
      bullets:
      - "PEP 701, authored by Pablo Galindo Salgado, Batuhan Taskaya, Lysandros Nikolaou,\
        \ and Marta G\xF3mez Mac\xEDas, was accepted on March 14, 2023, for Python\
        \ 3.12."
      - The proposal formalizes the f-string grammar and integrates it directly into
        the parser, lifting restrictions from the original PEP 498 specification.
      - Motivation includes reducing the high maintenance cost of the existing hand-written
        C parsing code, which is error-prone.
      - The change allows f-strings to benefit from the improved error messages of
        the new PEG parser (PEP 617).
      - The old system, which tokenized f-strings as a single `STRING` token for post-processing,
        created inconsistencies and difficulty for alternative Python implementations
        like PyPy.
      sub_sections: {}
    - heading: Restrictions Lifted from PEP 498
      bullets:
      - PEP 498's implementation-driven limitations, which a previous attempt in PEP
        536 failed to lift, are removed by PEP 701.
      sub_sections:
        Previous Restrictions:
        - Prohibiting the use of the same quote character within an f-string's expression
          part (e.g., `f' { bag['wand'] }'` ).
        - 'Forbidding backslashes (e.g., `f"{''

          ''.join(a)}"`).'
        - Disallowing comments (`#`) inside expressions.
        PEP 701 Changes:
        - Removes these restrictions, allowing any valid Python expression inside
          an f-string's braces.
        - Aligns Python with other languages like Ruby, JavaScript, Swift, and C#
          that permit arbitrary expression nesting in string interpolation.
    - heading: Technical Implementation with PEG Grammar
      bullets:
      - The new formal PEG grammar introduces three new tokens.
      sub_sections:
        New Tokens:
        - '`FSTRING_START`: Represents the prefix and opening quote.'
        - '`FSTRING_MIDDLE`: Represents text between expressions.'
        - '`FSTRING_END`: Represents the closing quote.'
        Tokenizer Update:
        - The `tokenize` module will be updated to emit these new tokens.
        - This new tokenization scheme requires a lexer that can handle nested modes.
        - The PEP provides a draft algorithm for adapting a CPython-like tokenizer.
    - heading: User Impact and Debates
      bullets:
      - Users can now reuse the same quote type inside an f-string expression.
      - Users can now use backslashes and newlines within f-string expressions.
      - Users can add comments inside f-string expressions, provided the closing brace
        is on a new line.
      sub_sections:
        Quote Reuse Debate:
        - Debated due to potential readability issues and challenges for simple syntax
          highlighters (like IDLE's).
        - Retained because it simplifies code generators (e.g., `ast.unparse`).
        - Provides "referential transparency".
        - Forbidding it would significantly complicate the PEG parser implementation.
        - The PEP authors argue that readability concerns should be addressed by linters.
    - heading: Backwards Compatibility and Specifics
      bullets:
      - The proposal maintains backwards compatibility and does not introduce semantic
        changes to existing code.
      - The Abstract Syntax Tree (AST) is preserved.
      sub_sections:
        Debug Feature:
        - The f-string debug feature (`f"{expr=}"`), introduced in Python 3.8 (bpo-36817),
          is unaffected.
        - Its formal handling requires the lexer to preserve the raw string of the
          expression, including whitespace.
        Nesting Levels:
        - 'The PEP specifies minimum nesting levels for implementations: 5 for f-strings
          and 2 for format specifiers (e.g., `f"{'''':*^{1:{1}}}"` must be valid).'
        - The language specification does not mandate arbitrary nesting, allowing
          implementations to set their own limits, similar to existing CPython limits
          on parenthesis or block nesting.
    - heading: Rejected Ideas and Simplified Teaching
      bullets:
      - Certain ideas were considered but ultimately rejected or deferred.
      sub_sections:
        Rejected Ideas:
        - Lifting the restriction on top-level `:` and `!` characters (e.g., for lambdas)
          remains to avoid tokenizer complexity.
        - Allowing escaped braces (`\{`, `\}`) was deferred as a separate issue.
        Simplified Teaching:
        - 'The new way to teach f-strings is simplified to: "You can place any valid
          Python expression inside an f-string expression, and everything after a
          `:` character at the top level will be identified as a format specification."'
    route_subtype: page
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
- claim: PEP 701 proposes a formalized grammar for f-strings, integrating it directly
    into the parser and lifting restrictions originally formulated in PEP 498.
  importance: 5
- claim: The proposed syntactic formalization provides considerable advantages for
    end users and library developers while dramatically reducing the maintenance cost
    of f-string parsing code.
  importance: 5
- claim: The new grammar allows f-string expression components to be defined as "any
    applicable Python expression," eliminating previous obscure limitations.
  importance: 5
- claim: The expression component of f-strings can now include any string literal,
    enabling nesting of string literals (formatted or not) with the same quote type.
  importance: 5
- claim: All previous restrictions on f-string literals, such as disallowing same-quote
    strings, backslashes, newlines, and comments, are lifted by the new grammar.
  importance: 5
- claim: PEP 701 introduces no backwards incompatible syntactic or semantic changes
    to the Python language.
  importance: 5
- claim: The PEP simplifies teaching f-strings, as the rule becomes "you can place
    any valid Python expression inside an f-string expression."
  importance: 5
- claim: The previous f-string implementation in CPython had high maintenance costs
    due to hand-written parsing code and could not leverage the new PEG parser's improved
    error messages.
  importance: 4
- claim: Other Python implementations lacked a formal grammar for f-strings, making
    correct implementation and verification difficult.
  importance: 4
- claim: The PEP builds on the new Python PEG Parser (PEP 617) to redefine f-strings
    with a clear separation of string and expression components.
  importance: 4
- claim: Backslashes are now allowed within f-string expression components, resolving
    a common unintuitive limitation.
  importance: 4
- claim: 'New lines and comments (using the # character) are now allowed within f-string
    expression parts.'
  importance: 4
- claim: The PEP specifies a lower bound of 5 levels of f-string nesting, though implementations
    are free to impose their own limits.
  importance: 4
- claim: The new grammar preserves the Abstract Syntax Tree (AST) of the current implementation,
    ensuring no semantic changes to existing f-string code.
  importance: 4
- claim: Three new tokens (FSTRING_START, FSTRING_MIDDLE, FSTRING_END) are introduced
    and must be produced by the lexer when processing f-strings.
  importance: 4
- claim: The `tokenize` module will be updated to emit these new f-string tokens,
    allowing tools to leverage the new tokenization schema.
  importance: 4
- claim: Allowing quote reuse and backslashes simplifies code generators by enabling
    regular Python expression logic inside f-strings without special treatment.
  importance: 4
- claim: The PEP decided not to reject quote reuse at the parser level, recommending
    linters and code style tools handle readability concerns instead.
  importance: 4
- claim: Limiting quote reuse would have significantly increased the complexity of
    the proposed implementation due to parser context requirements.
  importance: 4
- claim: F-strings were originally introduced without a formal grammar, leading to
    restrictions imposed to avoid modifying the existing lexer.
  importance: 3
- claim: Previous attempts (PEP 536) to lift f-string limitations were never implemented.
  importance: 3
- claim: Arbitrary nesting of expressions without escape sequence expansion is a feature
    present in string interpolation methods of many other languages (e.g., Ruby, JavaScript,
    C#).
  importance: 3
- claim: The formal handling of f-string debug expressions (e.g., `f"{1+1=}"`) requires
    the lexer to keep track of raw string contents of the expression part.
  importance: 3
- claim: Some users find quote reuse within the same string confusing and hard to
    read, violating a simple Python string delimitation rule.
  importance: 3
- claim: Concerns were raised that quote reuse might break existing lexer and syntax
    highlighting tools that rely on simpler parsing mechanisms.
  importance: 3
- claim: Many other popular languages allow quote reuse in string interpolation, which
    could make Python more familiar to users from those languages.
  importance: 3
- claim: The `tokenize` module update represents a minor incompatibility for tools
    that rely on its previous f-string tokenization behavior.
  importance: 3
- claim: Expressions allowing a colon (':') character at the top level within an f-string
    still need to be enclosed in parentheses to avoid being interpreted as a format
    specification.
  importance: 3
- claim: The PEP decided not to lift the restriction that some expressions (e.g.,
    lambdas) need parentheses at the top level if they contain ':' or '!'.
  importance: 3
- claim: The PEP decided to disallow (for the time being) using escaped braces (\{
    and \}) in addition to the `{{` and `}}` syntax.
  importance: 2

```

### SOURCE
```
PEP 701 – Syntactic formalization of f-strings - Author: - Pablo Galindo Salgado <pablogsal at python.org>, Batuhan Taskaya <batuhan at python.org>, Lysandros Nikolaou <lisandrosnik at gmail.com>, Marta Gómez Macías <cyberwitch at google.com> - Discussions-To: - Discourse thread - Status: - Accepted - Type: - Standards Track - Created: - 15-Nov-2022 - Python-Version: - 3.12 - Post-History: - 19-Dec-2022 - Resolution: - 14-Mar-2023 Abstract This document proposes to lift some of the restrictions originally formulated in PEP 498 and to provide a formalized grammar for f-strings that can be integrated into the parser directly. The proposed syntactic formalization of f-strings will have some small side-effects on how f-strings are parsed and interpreted, allowing for a considerable number of advantages for end users and library developers, while also dramatically reducing the maintenance cost of the code dedicated to parsing f-strings. Motivation When f-strings were originally introduced in PEP 498, the specification was provided without providing a formal grammar for f-strings. Additionally, the specification contains several restrictions that are imposed so the parsing of f-strings could be implemented into CPython without modifying the existing lexer. These limitations have been recognized previously and previous attempts have been made to lift them in PEP 536, but none of this work was ever implemented. Some of these limitations (collected originally by PEP 536) are: - It is impossible to use the quote character delimiting the f-string within the expression portion: >>> f'Magic wand: { bag['wand'] }' ^ SyntaxError: invalid syntax - A previously considered way around it would lead to escape sequences in executed code and is prohibited in f-strings: >>> f'Magic wand { bag[\'wand\'] } string' SyntaxError: f-string expression portion cannot include a backslash - Comments are forbidden even in multi-line f-strings: >>> f'''A complex trick: { ... bag['bag'] # recursive bags! ... }''' SyntaxError: f-string expression part cannot include '#' - Arbitrary nesting of expressions without expansion of escape sequences is available in many other languages that employ a string interpolation method that uses expressions instead of just variable names. Some examples: # Ruby "#{ "#{1+2}" }" # JavaScript `${`${1+2}`}` # Swift "\("\(1+2)")" # C# $"{$"{1+2}"}" These limitations serve no purpose from a language user perspective and can be lifted by giving f-string literals a regular grammar without exceptions and implementing it using dedicated parse code. The other issue that f-strings have is that the current implementation in CPython relies on tokenising f-strings as STRING tokens and a post processing of these tokens. This has the following problems: - It adds a considerable maintenance cost to the CPython parser. This is because the parsing code needs to be written by hand, which has historically led to a considerable number of inconsistencies and bugs. Writing and maintaining parsing code by hand in C has always been considered error prone and dangerous as it needs to deal with a lot of manual memory management over the original lexer buffers. - The f-string parsing code is not able to use the new improved error message mechanisms that the new PEG parser, originally introduced in PEP 617, has allowed. The improvements that these error messages brought has been greatly celebrated but unfortunately f-strings cannot benefit from them because they are parsed in a separate piece of the parsing machinery. This is especially unfortunate, since there are several syntactical features of f-strings that can be confusing due to the different implicit tokenization that happens inside the expression part (for instance f"{y:=3}" is not an assignment expression). - Other Python implementations have no way to know if they have implemented f-strings correctly because contrary to other language features, they are not part of the official Python grammar. This is important because several prominent alternative implementations are using CPython’s PEG parser, such as PyPy, and/or are basing their grammars on the official PEG grammar. The fact that f-strings use a separate parser prevents these alternative implementations from leveraging the official grammar and benefiting from improvements in error messages derived from the grammar. A version of this proposal was originally discussed on Python-Dev and presented at the Python Language Summit 2022 where it was enthusiastically received. Rationale By building on top of the new Python PEG Parser (PEP 617), this PEP proposes to redefine “f-strings”, especially emphasizing the clear separation of the string component and the expression (or replacement, {...} ) component. PEP 498 summarizes the syntactical part of “f-strings” as the following: In Python source code, an f-string is a literal string, prefixed with ‘f’, which contains expressions inside braces. The expressions are replaced with their values. However, PEP 498 also contained a formal list of exclusions on what can or cannot be contained inside the expression component (primarily due to the limitations of the existing parser). By clearly establishing the formal grammar, we now also have the ability to define the expression component of an f-string as truly “any applicable Python expression” (in that particular context) without being bound by the limitations imposed by the details of our implementation. The formalization effort and the premise above also has a significant benefit for Python programmers due to its ability to simplify and eliminate the obscure limitations. This reduces the mental burden and the cognitive complexity of f-string literals (as well as the Python language in general). - The expression component can include any string literal that a normal Python expression can include. This opens up the possibility of nesting string literals (formatted or not) inside the expression component of an f-string with the same quote type (and length): >>> f"These are the things: {", ".join(things)}" >>> f"{source.removesuffix(".py")}.c: $(srcdir)/{source}" >>> f"{f"{f"infinite"}"}" + " " + f"{f"nesting!!!"}" This “feature” is not universally agreed to be desirable, and some users find this unreadable. For a discussion on the different views on this, see the considerations regarding quote reuse section. - Another issue that has felt unintuitive to most is the lack of support for backslashes within the expression component of an f-string. One example that keeps coming up is including a newline character in the expression part for joining containers. For example: >>> a = ["hello", "world"] >>> f"{'\n'.join(a)}" File "<stdin>", line 1 f"{'\n'.join(a)}" ^ SyntaxError: f-string expression part cannot include a backslash A common work-around for this was to either assign the newline to an intermediate variable or pre-create the whole string prior to creating the f-string: >>> a = ["hello", "world"] >>> joined = '\n'.join(a) >>> f"{joined}" 'hello\nworld' It only feels natural to allow backslashes in the expression part now that the new PEG parser can easily support it. >>> a = ["hello", "world"] >>> f"{'\n'.join(a)}" 'hello\nworld' - Before the changes proposed in this document, there was no explicit limit in how f-strings can be nested, but the fact that string quotes cannot be reused inside the expression component of f-strings made it impossible to nest f-strings arbitrarily. In fact, this is the most nested-fstring that can be written: >>> f"""{f'''{f'{f"{1+1}"}'}'''}""" '2' As this PEP allows placing any valid Python expression inside the expression component of the f-strings, it is now possible to reuse quotes and therefore is possible to nest f-strings arbitrarily: >>> f"{f"{f"{f"{f"{f"{1+1}"}"}"}"}"}" '2' Although this is just a consequence of allowing arbitrary expressions, the authors of this PEP do not believe that this is a fundamental benefit and we have decided that the language specification will not explicitly mandate that this nesting can be arbitrary. This is because allowing arbitrarily-deep nesting imposes a lot of extra complexity to the lexer implementation (particularly as lexer/parser pipelines need to allow “untokenizing” to support the ‘f-string debugging expressions’ and this is especially taxing when arbitrary nesting is allowed). Implementations are therefore free to impose a limit on the nesting depth if they need to. Note that this is not an uncommon situation, as the CPython implementation already imposes several limits all over the place, including a limit on the nesting depth of parentheses and brackets, a limit on the nesting of the blocks, a limit in the number of branches in if statements, a limit on the number of expressions in star-unpacking, etc. Specification The formal proposed PEG grammar specification for f-strings is (see PEP 617 for details on the syntax): fstring | FSTRING_START fstring_middle* FSTRING_END fstring_middle | fstring_replacement_field | FSTRING_MIDDLE fstring_replacement_field | '{' (yield_expr | star_expressions) "="? [ "!" NAME ] [ ':' fstring_format_spec* ] '}' fstring_format_spec: | FSTRING_MIDDLE | fstring_replacement_field The new tokens (FSTRING_START , FSTRING_MIDDLE , FSTRING_END ) are defined later in this document. This PEP leaves up to the implementation the level of f-string nesting allowed (f-strings within the expression parts of other f-strings) but specifies a lower bound of 5 levels of nesting. This is to ensure that users can have a reasonable expectation of being able to nest f-strings with “reasonable” depth. This PEP implies that limiting nesting is not part of the language specification but also the language specification doesn’t mandate arbitrary nesting. Similarly, this PEP leaves up to the implementation the level of expression nesting in format specifiers but specifies a lower bound of 2 levels of nesting. This means that the following should always be valid: f"{'':*^{1:{1}}}" but the following can be valid or not depending on the implementation: f"{'':*^{1:{1:{1}}}}" The new grammar will preserve the Abstract Syntax Tree (AST) of the current implementation. This means that no semantic changes will be introduced by this PEP on existing code that uses f-strings. Handling of f-string debug expressions Since Python 3.8, f-strings can be used to debug expressions by using the = operator. For example: >>> a = 1 >>> f"{1+1=}" '1+1=2' This semantics were not introduced formally in a PEP and they were implemented in the current string parser as a special case in bpo-36817 and documented in the f-string lexical analysis section. This feature is not affected by the changes proposed in this PEP but is important to specify that the formal handling of this feature requires the lexer to be able to “untokenize” the expression part of the f-string. This is not a problem for the current string parser as it can operate directly on the string token contents. However, incorporating this feature into a given parser implementation requires the lexer to keep track of the raw string contents of the expression part of the f-string and make them available to the parser when the parse tree is constructed for f-string nodes. A pure “untokenization” is not enough because as specified currently, f-string debug expressions preserve whitespace in the expression, including spaces after the { and the = characters. This means that the raw string contents of the expression part of the f-string must be kept intact and not just the associated tokens. How parser/lexer implementations deal with this problem is of course up to the implementation. New tokens Three new tokens are introduced: FSTRING_START , FSTRING_MIDDLE and FSTRING_END . Different lexers may have different implementations that may be more efficient than the ones proposed here given the context of the particular implementation. However, the following definitions will be used as part of the public APIs of CPython (such as the tokenize module) and are also provided as a reference so that the reader can have a better understanding of the proposed grammar changes and how the tokens are used: FSTRING_START : This token includes the f-string prefix (f /F /fr ) and the opening quote(s).FSTRING_MIDDLE : This token includes a portion of text inside the string that’s not part of the expression part and isn’t an opening or closing brace. This can include the text between the opening quote and the first expression brace ({ ), the text between two expression braces (} and{ ) and the text between the last expression brace (} ) and the closing quote.FSTRING_END : This token includes the closing quote. These tokens are always string parts and they are semantically equivalent to the STRING token with the restrictions specified. These tokens must be produced by the lexer when lexing f-strings. This means that the tokenizer cannot produce a single token for f-strings anymore. How the lexer emits this token is not specified as this will heavily depend on every implementation (even the Python version of the lexer in the standard library is implemented differently to the one used by the PEG parser). As an example: f'some words {a+b:.3f} more words {c+d=} final words' will be tokenized as: FSTRING_START - "f'" FSTRING_MIDDLE - 'some words ' LBRACE - '{' NAME - 'a' PLUS - '+' NAME - 'b' OP - ':' FSTRING_MIDDLE - '.3f' RBRACE - '}' FSTRING_MIDDLE - ' more words ' LBRACE - '{' NAME - 'c' PLUS - '+' NAME - 'd' OP - '=' RBRACE - '}' FSTRING_MIDDLE - ' final words' FSTRING_END - "'" while f"""some words""" will be tokenized simply as: FSTRING_START - 'f"""' FSTRING_MIDDLE - 'some words' FSTRING_END - '"""' Changes to the tokenize module The tokenize module will be adapted to emit these tokens as described in the previous section when parsing f-strings so tools can take advantage of this new tokenization schema and avoid having to implement their own f-string tokenizer and parser. How to produce these new tokens One way existing lexers can be adapted to emit these tokens is to incorporate a stack of “lexer modes” or to use a stack of different lexers. This is because the lexer needs to switch from “regular Python lexing” to “f-string lexing” when it encounters an f-string start token and as f-strings can be nested, the context needs to be preserved until the f-string closes. Also, the “lexer mode” inside an f-string expression part needs to behave as a “super-set” of the regular Python lexer (as it needs to be able to switch back to f-string lexing when it encounters the } terminator for the expression part as well as handling f-string formatting and debug expressions). For reference, here is a draft of the algorithm to modify a CPython-like tokenizer to emit these new tokens: - If the lexer detects that an f-string is starting (by detecting the letter ‘f/F’ and one of the possible quotes) keep advancing until a valid quote is detected (one of " ,""" ,' or''' ) and emit aFSTRING_START token with the contents captured (the ‘f/F’ and the starting quote). Push a new tokenizer mode to the tokenizer mode stack for “F-string tokenization”. Go to step 2. - Keep consuming tokens until a one of the following is encountered: - A closing quote equal to the opening quote. - If in “format specifier mode” (see step 3), an opening brace ( { ), a closing brace (} ), or a newline token (\n ). - If not in “format specifier mode” (see step 3), an opening brace ( { ) or a closing brace (} ) that is not immediately followed by another opening/closing brace. In all cases, if the character buffer is not empty, emit a FSTRING_MIDDLE token with the contents captured so far but transform any double opening/closing braces into single opening/closing braces. Now, proceed as follows depending on the character encountered:- If a closing quote matching the opening quite is encountered go to step 4. - If an opening bracket (not immediately followed by another opening bracket) is encountered, go to step 3. - If a closing bracket (not immediately followed by another closing bracket) is encountered, emit a token for the closing bracket and go to step 2. - Push a new tokenizer mode to the tokenizer mode stack for “Regular Python tokenization within f-string” and proceed to tokenize with it. This mode tokenizes as the “Regular Python tokenization” until a : or a} character is encountered with the same level of nesting as the opening bracket token that was pushed when we enter the f-string part. Using this mode, emit tokens until one of the stop points are reached. When this happens, emit the corresponding token for the stopping character encountered and, pop the current tokenizer mode from the tokenizer mode stack and go to step 2. If the stopping point is a: character, enter step 2 in “format specifier” mode. - Emit a FSTRING_END token with the contents captured and pop the current tokenizer mode (corresponding to “F-string tokenization”) and go back to “Regular Python mode”. Of course, as mentioned before, it is not possible to provide a precise specification of how this should be done for an arbitrary tokenizer as it will depend on the specific implementation and nature of the lexer to be changed. Consequences of the new grammar All restrictions mentioned in the PEP are lifted from f-string literals, as explained below: - Expression portions may now contain strings delimited with the same kind of quote that is used to delimit the f-string literal. - Backslashes may now appear within expressions just like anywhere else in Python code. In case of strings nested within f-string literals, escape sequences are expanded when the innermost string is evaluated. - New lines are now allowed within expression brackets. This means that these are now allowed: >>> x = 1 >>> f"___{ ... x ... }___" '___1___' >>> f"___{( ... x ... )}___" '___1___' - Comments, using the # character, are allowed within the expression part of an f-string. Note that comments require that the closing bracket (} ) of the expression part to be present in a different line as the one the comment is in or otherwise it will be ignored as part of the comment. Considerations regarding quote reuse One of the consequences of the grammar proposed here is that, as mentioned above, f-string expressions can now contain strings delimited with the same kind of quote that is used to delimit the external f-string literal. For example: >>> f" something { my_dict["key"] } something else " In the discussion thread for this PEP, several concerns have been raised regarding this aspect and we want to collect them here, as these should be taken into consideration when accepting or rejecting this PEP. Some of these objections include: - Many people find quote reuse within the same string confusing and hard to read. This is because allowing quote reuse will violate a current property of Python as it stands today: the fact that strings are fully delimited by two consecutive pairs of the same kind of quote, which by itself is a very simple rule. One of the reasons quote reuse may be harder for humans to parse, leading to less readable code, is that the quote character is the same for both start and end (as opposed to other delimiters). - Some users have raised concerns that quote reuse may break some lexer and syntax highlighting tools that rely on simple mechanisms to detect strings and f-strings, such as regular expressions or simple delimiter matching tools. Introducing quote reuse in f-strings will either make it trickier to keep these tools working or will break the tools altogether (as, for instance, regular expressions cannot parse arbitrary nested structures with delimiters). The IDLE editor, included in the standard library, is an example of a tool which may need some work to correctly apply syntax highlighting to f-strings. Here are some of the arguments in favour: - Many languages that allow similar syntactic constructs (normally called “string interpolation”
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): 07f4472e9fabea09f649d953cfece7b25612889faa9070dd20161e99e34a9cba
