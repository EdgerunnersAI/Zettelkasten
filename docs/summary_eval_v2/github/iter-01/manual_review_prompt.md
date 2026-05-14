You are an INDEPENDENT rubric reviewer, blind to any prior evaluator's scoring. Do NOT read eval.json.

Stamp `eval_json_hash_at_review: "NOT_CONSULTED"` at the top of your manual_review.md.

RUBRIC:
version: rubric_github.v1
source_type: github
composite_max_points: 100
components:
- id: brief_summary
  max_points: 25
  criteria:
  - id: brief.user_facing_purpose
    description: Brief states what the repo does in user-facing terms.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
    - finesure.completeness
  - id: brief.architecture_high_level
    description: Brief identifies main components/architecture at a high level.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: brief.languages_and_frameworks
    description: Primary languages and major frameworks are mentioned.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
    - qafact
  - id: brief.usage_pattern
    description: Describes documented usage/installation/workflow.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
  - id: brief.public_surface
    description: If exposed, REST routes, CLI entry, UI pages, or Pages URL are summarized.
    max_points: 4
    maps_to_metric:
    - finesure.completeness
  - id: brief.no_maturity_fabrication
    description: Maturity claims (experimental/production-ready) only if explicitly
      signaled.
    max_points: 2
    maps_to_metric:
    - finesure.faithfulness
    - summac
- id: detailed_summary
  max_points: 45
  criteria:
  - id: detailed.features_bullets
    description: Core features as bullets, each tied to explicit code or docs.
    max_points: 8
    maps_to_metric:
    - finesure.faithfulness
    - qafact
  - id: detailed.architecture_modules
    description: 'Architecture bullets: directories, key classes, interactions.'
    max_points: 8
    maps_to_metric:
    - finesure.completeness
  - id: detailed.interfaces_exact
    description: Public APIs / CLI commands / config options with exact names.
    max_points: 8
    maps_to_metric:
    - finesure.faithfulness
    - summac
  - id: detailed.operational
    description: Install steps, deps, env vars, build, deploy instructions captured.
    max_points: 6
    maps_to_metric:
    - finesure.completeness
  - id: detailed.limitations_docs
    description: Documented limitations, caveats, security notes preserved.
    max_points: 5
    maps_to_metric:
    - finesure.faithfulness
  - id: detailed.benchmarks_tests_examples
    description: If benchmarks/tests/examples exist, what they demonstrate is summarized.
    max_points: 5
    maps_to_metric:
    - finesure.completeness
  - id: detailed.bullets_focused
    description: Each bullet covers one coherent aspect.
    max_points: 5
    maps_to_metric:
    - g_eval.coherence
- id: tags
  max_points: 15
  criteria:
  - id: tags.count_7_to_10
    description: Exactly 7-10 tags.
    max_points: 2
    maps_to_metric:
    - finesure.conciseness
  - id: tags.domain_tag
    description: Main domain/application tag present.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.languages
    description: Primary language(s) tagged.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.technical_concepts
    description: Key technical concepts ('rest-api','cli-tool','ml-serving') present.
    max_points: 3
    maps_to_metric:
    - finesure.completeness
  - id: tags.no_unsupported_claims
    description: No tags claim 'production-ready' without evidence.
    max_points: 4
    maps_to_metric:
    - finesure.faithfulness
    - summac
- id: label
  max_points: 15
  criteria:
  - id: label.owner_slash_repo
    description: Label is exactly 'owner/repo' matching the canonical GitHub path.
    max_points: 10
    maps_to_metric:
    - finesure.faithfulness
  - id: label.no_extra_descriptors
    description: No prepended/appended descriptors; qualifiers belong in summary or
      tags.
    max_points: 5
    maps_to_metric:
    - finesure.conciseness
anti_patterns:
- id: production_ready_claim_no_evidence
  description: Summary claims 'production-ready' without README evidence.
  auto_cap: 60
- id: invented_public_interface
  description: Summary claims an API route / CLI command / export not present in repo.
  auto_cap: 60
- id: label_not_owner_repo
  description: Label doesn't match 'owner/repo' regex.
  auto_cap: 75
global_rules:
  editorialization_penalty:
    threshold_flags: 3


SUMMARY:
## URL 1: https://github.com/psf/requests

### SUMMARY
```yaml
mini_title: psf/requests
brief_summary: psf/requests is a popular Python HTTP/1.1 library that simplifies HTTP
  requests by automating tasks like query string addition and form-encoding. Its main
  stack includes Python and Makefile. Users adopt it via `pip install requests`. Core
  public interfaces mentioned include the `json` method, `Session` objects, `HTTPDigestAuth`,
  and `PreparedRequest.copy()`.
tags:
- gh-psf-requests
- python
- library
- http-client
- http
- networking
- api-client
- web-development
- http-1-1
detailed_summary:
- heading: Overview
  bullets:
  - psf/requests is a Python HTTP/1.1 library designed to simplify HTTP requests.
  sub_sections:
    Core argument:
    - psf/requests is a Python HTTP/1.1 library designed to simplify HTTP requests.
    Architecture:
    - It automates common tasks like adding query strings or form-encoding data, and
      manages underlying complexities such as Keep-Alive, connection pooling, and
      session persistence.
    - The library is primarily written in Python, with a small component in Makefile,
      and is structured with dedicated directories for tests and documentation.
    Stack:
    - Python, Makefile.
- heading: Features and modules
  bullets: []
  sub_sections:
    Overview:
    - psf/requests is a popular Python HTTP/1.1 library, described as simple and elegant.
    - Approximately 300 million downloads per week and used by over 4,000,000 GitHub
      repositories.
    - Officially supports Python 3.10+.
    - 'Usability signals: pip install requests.'
    Core Features:
    - Simplifies HTTP requests by automating tasks like adding query strings or form-encoding
      PUT/POST data.
    - Recommends the `json` method for modern use.
    - Includes Keep-Alive and connection pooling.
    - Supports international domains.
    - Provides session management with cookie persistence.
    - Offers browser-style TLS/SSL verification.
    - Supports Basic/Digest authentication.
    - Performs automatic content decompression.
    - Handles multi-part file uploads.
    - Includes SOCKS proxy support.
    - Manages connection timeouts.
    - Enables streaming downloads.
    - Supports `.netrc`.
    - 'Public surface: json method, Session objects, HTTPDigestAuth, PreparedRequest.copy().'
    Architecture / Modules:
    - The repository is 99.3% Python and 0.7% Makefile.
    - Contains `tests` and `docs_dir` directories.
    - Utilizes 10 GitHub Actions workflows.
    Operational Guidance:
    - Installation is via `pip install requests`.
    - 'When cloning, it is recommended to use the `-c fetch.fsck.badTimezone=ignore`
      flag to prevent a timestamp error (issue #2690).'
    Ongoing Initiatives & Bug Fixes:
    - 'Major ongoing initiative: addition of inline type annotations (RFC #7271, PR
      #7272), with a call for feedback from maintainers of dependent projects.'
    - Numerous issues and pull requests focus on documentation improvements, such
      as clarifying the `timeout` parameter's scope (#7350), explaining `ChunkedEncodingError`
      (#7341), warning against using Session objects across forked processes (#7297),
      adding a comprehensive error handling example (#7289), and announcing a documentation
      localization project (#7357).
    - Recent bug fixes address preserving leading slashes in URLs for S3 (#7315),
      incorrect `Content-Length` for `StringIO` with multi-byte characters (#7201),
      malformed chunked requests with `None` values in data (#7217), `no_proxy` being
      ignored on redirects (#7194), and an issue where `REQUESTS_CA_BUNDLE` could
      override `session.verify=False` (#7384).
    - Other fixes include adding RFC 7616 support for non-Latin credentials in `HTTPDigestAuth`
      (#7232) and ensuring `PreparedRequest.copy()` does not share hooks with the
      original request (#7213).
    Tests / Benchmarks / Examples:
    - The repository contains a `tests` directory.
    - The `docs_dir` directory is present.
- heading: Benchmarks and examples
  bullets:
  - The repository contains a `tests` directory.
  - The `docs_dir` directory is present.
  sub_sections: {}
- heading: Closing remarks
  bullets:
  - 'Roadmap: Core public interfaces mentioned include the `json` method, `Session`
    objects, `HTTPDigestAuth`, and `PreparedRequest.copy()`.'
  sub_sections: {}
metadata:
  source_type: github
  url: https://github.com/psf/requests
  author: psf
  date: null
  extraction_confidence: high
  confidence_reason: repo metadata and README fetched
  total_tokens_used: 2647
  gemini_pro_tokens: 0
  gemini_flash_tokens: 2647
  total_latency_ms: 33732
  cod_iterations_used: 0
  self_check_missing_count: 0
  patch_applied: false
  engine_version: 2.0.0
  structured_payload:
    mini_title: psf/requests
    architecture_overview: psf/requests is a Python HTTP/1.1 library designed to simplify
      HTTP requests. It automates common tasks like adding query strings or form-encoding
      data, and manages underlying complexities such as Keep-Alive, connection pooling,
      and session persistence. The library is primarily written in Python, with a
      small component in Makefile, and is structured with dedicated directories for
      tests and documentation.
    brief_summary: psf/requests is a popular Python HTTP/1.1 library that simplifies
      HTTP requests by automating tasks like query string addition and form-encoding.
      Its main stack includes Python and Makefile. Users adopt it via `pip install
      requests`. Core public interfaces mentioned include the `json` method, `Session`
      objects, `HTTPDigestAuth`, and `PreparedRequest.copy()`.
    tags:
    - gh-psf-requests
    - python
    - library
    - http-client
    - http
    - networking
    - api-client
    - web-development
    - http-1-1
    benchmarks_tests_examples:
    - The repository contains a `tests` directory.
    - The `docs_dir` directory is present.
    detailed_summary:
    - heading: Overview
      bullets:
      - psf/requests is a popular Python HTTP/1.1 library, described as simple and
        elegant.
      - Approximately 300 million downloads per week and used by over 4,000,000 GitHub
        repositories.
      - Officially supports Python 3.10+.
      sub_sections: {}
      module_or_feature: Overview
      main_stack:
      - Python
      - Makefile
      public_interfaces: []
      usability_signals:
      - pip install requests
    - heading: Core Features
      bullets:
      - Simplifies HTTP requests by automating tasks like adding query strings or
        form-encoding PUT/POST data.
      - Recommends the `json` method for modern use.
      - Includes Keep-Alive and connection pooling.
      - Supports international domains.
      - Provides session management with cookie persistence.
      - Offers browser-style TLS/SSL verification.
      - Supports Basic/Digest authentication.
      - Performs automatic content decompression.
      - Handles multi-part file uploads.
      - Includes SOCKS proxy support.
      - Manages connection timeouts.
      - Enables streaming downloads.
      - Supports `.netrc`.
      sub_sections: {}
      module_or_feature: Core Features
      main_stack: []
      public_interfaces:
      - json method
      - Session objects
      - HTTPDigestAuth
      - PreparedRequest.copy()
      usability_signals: []
    - heading: Architecture / Modules
      bullets:
      - The repository is 99.3% Python and 0.7% Makefile.
      - Contains `tests` and `docs_dir` directories.
      - Utilizes 10 GitHub Actions workflows.
      sub_sections: {}
      module_or_feature: Architecture / Modules
      main_stack: []
      public_interfaces: []
      usability_signals: []
    - heading: Operational Guidance
      bullets:
      - Installation is via `pip install requests`.
      - 'When cloning, it is recommended to use the `-c fetch.fsck.badTimezone=ignore`
        flag to prevent a timestamp error (issue #2690).'
      sub_sections: {}
      module_or_feature: Operational Guidance
      main_stack: []
      public_interfaces: []
      usability_signals: []
    - heading: Ongoing Initiatives & Bug Fixes
      bullets:
      - 'Major ongoing initiative: addition of inline type annotations (RFC #7271,
        PR #7272), with a call for feedback from maintainers of dependent projects.'
      - Numerous issues and pull requests focus on documentation improvements, such
        as clarifying the `timeout` parameter's scope (#7350), explaining `ChunkedEncodingError`
        (#7341), warning against using Session objects across forked processes (#7297),
        adding a comprehensive error handling example (#7289), and announcing a documentation
        localization project (#7357).
      - Recent bug fixes address preserving leading slashes in URLs for S3 (#7315),
        incorrect `Content-Length` for `StringIO` with multi-byte characters (#7201),
        malformed chunked requests with `None` values in data (#7217), `no_proxy`
        being ignored on redirects (#7194), and an issue where `REQUESTS_CA_BUNDLE`
        could override `session.verify=False` (#7384).
      - Other fixes include adding RFC 7616 support for non-Latin credentials in `HTTPDigestAuth`
        (#7232) and ensuring `PreparedRequest.copy()` does not share hooks with the
        original request (#7213).
      sub_sections: {}
      module_or_feature: Ongoing Initiatives & Bug Fixes
      main_stack: []
      public_interfaces: []
      usability_signals: []
    - heading: Tests / Benchmarks / Examples
      bullets:
      - The repository contains a `tests` directory.
      - The `docs_dir` directory is present.
      sub_sections: {}
      module_or_feature: Tests / Benchmarks / Examples
      main_stack: []
      public_interfaces: []
      usability_signals: []
    _github_archetype:
      archetype: cli_tool
      confidence: 0.6
      reasons:
      - cli_tokens=1
    _dense_verify:
      archetype: framework_api
      missing_fact_count: 0
    owner_login: psf
    route_subtype: repo
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
- claim: Requests is a simple, yet elegant, HTTP library.
  importance: 5
- claim: Requests allows you to send HTTP/1.1 requests extremely easily.
  importance: 5
- claim: Requests is one of the most downloaded Python packages today.
  importance: 5
- claim: Requests is depended upon by 4,000,000+ repositories.
  importance: 5
- claim: Requests is a Python library.
  importance: 4
- claim: Requests pulls in around 300M downloads / week.
  importance: 4
- claim: Requests is available on PyPI.
  importance: 4
- claim: Requests officially supports Python 3.10+.
  importance: 4
- claim: "Requests is ready for the demands of building robust and reliable HTTP\u2013\
    speaking applications."
  importance: 4
- claim: Requests supports Keep-Alive & Connection Pooling.
  importance: 4
- claim: Requests supports Sessions with Cookie Persistence.
  importance: 4
- claim: Requests supports Browser-style TLS/SSL Verification.
  importance: 4
- claim: Requests supports Basic & Digest Authentication.
  importance: 4
- claim: Requests provides Automatic Content Decompression and Decoding.
  importance: 4
- claim: Requests supports Connection Timeouts.
  importance: 4
- claim: Requests is adding inline type annotations.
  importance: 4
- claim: The Requests library is accessible via 'import requests'.
  importance: 4
- claim: Requests simplifies adding query strings to URLs and form-encoding PUT &
    POST data.
  importance: 4
- claim: Requests recommends using the 'json' method for PUT & POST data.
  importance: 3
- claim: Requests supports International Domains and URLs.
  importance: 3
- claim: "Requests handles dict\u2013like Cookies."
  importance: 3
- claim: Requests supports Multi-part File Uploads.
  importance: 3
- claim: Requests supports SOCKS Proxy.
  importance: 3
- claim: Requests supports Streaming Downloads.
  importance: 3
- claim: Requests automatically honors .netrc.
  importance: 3
- claim: Requests supports Chunked HTTP Requests.
  importance: 3
- claim: The REQUESTS_CA_BUNDLE environment variable can override session.verify=False,
    causing TLS verification to still happen.
  importance: 3
- claim: The 'requests-docs-l10n' project is published for localization of Requests
    documentation.
  importance: 3
- claim: Requests fixes an issue where S3 presigned URLs with leading slashes were
    incorrectly modified.
  importance: 3
- claim: Requests adds RFC 7616 support for non-Latin credentials in HTTPDigestAuth.
  importance: 3

```

### SOURCE
```
Repository
psf/requests A simple, yet elegant, HTTP library. Language: Python Topics: client, cookies, forhumans, http, humans, python, python-requests, requests

README
# Requests [![Version](https://img.shields.io/pypi/v/requests.svg?maxAge=86400)](https://pypi.org/project/requests/) [![Supported Versions](https://img.shields.io/pypi/pyversions/requests.svg)](https://pypi.org/project/requests) [![Downloads](https://static.pepy.tech/badge/requests/month)](https://pepy.tech/project/requests) [![Contributors](https://img.shields.io/github/contributors/psf/requests.svg)](https://github.com/psf/requests/graphs/contributors) [![Documentation](https://readthedocs.org/projects/requests/badge/?version=latest)](https://requests.readthedocs.io) **Requests** is a simple, yet elegant, HTTP library. ```python >>> import requests >>> r = requests.get('https://httpbin.org/basic-auth/user/pass', auth=('user', 'pass')) >>> r.status_code 200 >>> r.headers['content-type'] 'application/json; charset=utf8' >>> r.encoding 'utf-8' >>> r.text '{"authenticated": true, ...' >>> r.json() {'authenticated': True, ...} ``` Requests allows you to send HTTP/1.1 requests extremely easily. There’s no need to manually add query strings to your URLs, or to form-encode your `PUT` & `POST` data — but nowadays, just use the `json` method! Requests is one of the most downloaded Python packages today, pulling in around `300M downloads / week` — according to GitHub, Requests is currently [depended upon](https://github.com/psf/requests/network/dependents?package_id=UGFja2FnZS01NzA4OTExNg%3D%3D) by `4,000,000+` repositories. ## Installing Requests and Supported Versions Requests is available on PyPI: ```console $ python -m pip install requests ``` Requests officially supports Python 3.10+. ## Supported Features & Best–Practices Requests is ready for the demands of building robust and reliable HTTP–speaking applications, for the needs of today. - Keep-Alive & Connection Pooling - International Domains and URLs - Sessions with Cookie Persistence - Browser-style TLS/SSL Verification - Basic & Digest Authentication - Familiar `dict`–like Cookies - Automatic Content Decompression and Decoding - Multi-part File Uploads - SOCKS Proxy Support - Connection Timeouts - Streaming Downloads - Automatic honoring of `.netrc` - Chunked HTTP Requests ## Cloning the repository When cloning the Requests repository, you may need to add the `-c fetch.fsck.badTimezone=ignore` flag to avoid an error about a bad commit timestamp (see [this issue](https://github.com/psf/requests/issues/2690) for more background): ```shell git clone -c fetch.fsck.badTimezone=ignore https://github.com/psf/requests.git ``` You can also apply this setting to your global Git config: ```shell git config --global fetch.fsck.badTimezone ignore ``` --- [![Kenneth Reitz](https://raw.githubusercontent.com/psf/requests/main/ext/kr.png)](https://kennethreitz.org) [![Python Software Foundation](https://raw.githubusercontent.com/psf/requests/main/ext/psf.png)](https://www.python.org/psf)

Languages
Python: 358123, Makefile: 2527

Issues
#7384: Don't let REQUESTS_CA_BUNDLE override an explicit session.verify=False ## Problem When a session is configured with `verify=False` and the `REQUESTS_CA_BUNDLE` (or `CURL_CA_BUNDLE`) environment variable is set, the environment variable silently wins and TLS verification still happens: ```python import os os.environ["REQUESTS_CA_BUNDLE"] = "/etc/ssl/certs/ca-certificates.crt" s = requests.S... #7357: Localization of The Requests Documentation ## Announcement Hello Requests Community, I am the author of the @localizethedocs organization. And I’m glad to announce that the 🎉 **requests-docs-l10n** 🎉 project is published now: - 🚀 **Preview:** [requests-docs-l10n](https://projects.localizethedocs.org/requests-docs-l10n) - 🌐 **Crowdin:** [requests-docs-l10n](https://localizethedocs.crowdin.c... #7350: [Docs] Clarify behavior of timeout parameter in requests.get ## Description The current documentation for the `timeout` parameter in `requests.get()` does not clearly explain that it applies to both connection and read timeouts unless specified otherwise. This can be confusing for users who expect it to behave as a total request timeout. ## Suggested Improvement Add clarification in the do... #7341: Documentation for ChunkedEncodingError is either incorrect or very misleading As per https://github.com/psf/requests/issues/4771 the docs need improving, since this is caused by transient network outages. That github bug is the best explanation of this situation currently. The easiest fix would be to just add to the documentation on https://docs.python-requests.org/en/latest/_modules/reque... #7315: Preserve leading slashes in request path_url ## Summary Fixes issue #6711 where S3 presigned URLs with keys starting with '/' were incorrectly modified, breaking URL signatures. URLs like `https://bucket.s3.amazonaws.com//key_name` now correctly preserve `//key_name` in the path. ## Changes - Remove URL manipulation that collapsed leading slashes in `request_url()` method - Add test to ver... #7297: docs: warn about using Session across forked processes Context: psf/requests#4323 notes that creating a Session before forking can lead to unsafe behavior because connection pools may be shared between processes. This PR adds a short warning to the "Session Objects" section of the advanced usage docs to help users avoid this pitfall. What changed - Add a warning admonition about fork()/mul... #7289: Docs: add complete error handling example to quickstart This PR adds a complete, copy-paste-ready example to the Quickstart guide demonstrating how to handle errors when making HTTP requests. ### What’s included Example using requests.get Proper use of response.raise_for_status() Basic exception handling with HTTPError and generic exceptions ### Why this is useful The current documentation... #7272: Add inline types to Requests # Add inline type annotations > [!IMPORTANT] > We want feedback from people who actively maintain projects that depend on Requests or use it heavily. Please share your experience testing this against your code **in the linked issue**. > >Comments that are clearly AI-generated will be hidden or removed. This is a library written "for Humans". The conversation is... #7271: RFC: Adding inline type annotations to Requests > [!TIP] > Want to skip the background? Jump to [How to test](#how-to-test) or [What feedback we need](#what-feedback-were-looking-for). ## Motivation Requests has notably been without inline type annotations since type hints entered the Python ecosystem. We've had aspirations of bringing Requests up to par with community standards for a few... #7232: feat: add RFC 7616 support for non-Latin credentials in HTTPDigestAuth ## Summary Implement [RFC 7616](https://www.rfc-editor.org/rfc/rfc7616) extensions to fix `HTTPDigestAuth` failing with non-Latin-1 usernames (e.g., Cyrillic, Czech diacritics). ## Problem ```python import requests from requests.auth import HTTPDigestAuth # This fails — 'ř' cannot be encoded as latin-1 auth = HTTPDigest... #7223: Chardet is used, when it is available, not when `[use-chardet-on-py3]`-extra is installed First: IMHO this is related to but not a duplicate of #5871 #7222 #7219 `requests` tries to use `chardet` when it can be imported. However, IMHO it should only use it, if `requests` was installed with the `[use-chardet-on-py3]`-extra. As reported in #7219, `requests` shows a warning, when the version... #7217: Fix #6122: Empty body with None values sends malformed chunked request When `data={'foo': None}`, the body encodes to an empty string but `Content-Length` was not set because `0` is falsy in the `if length:` check. This caused the adapter to fall back to `Transfer-Encoding: chunked`, sending a terminating chunk (`0\r\n\r\n`) that servers misinterpreted as a second, malformed request. ## Fi... #7213: Fix PreparedRequest.copy() sharing hooks reference with original `PreparedRequest.copy()` currently assigns `p.hooks = self.hooks` which means the copy shares the same hooks dictionary and callback lists with the original request. Modifying hooks on the copy (e.g., registering a new response hook) will unintentionally affect the original as well. This is inconsistent with how the method ha... #7201: Fix incorrect Content-Length for StringIO with multi-byte characters ## Summary Fixes #6917. `super_len()` uses `seek`/`tell` to measure the length of file-like objects such as `StringIO` and `BytesIO`. However, `StringIO.tell()` returns the **character position**, not the byte offset. For strings containing multi-byte UTF-8 characters (e.g. emoji), this produces an incorrect `Content-Leng... #7194: Fix: Strip proxies when redirect URL matches no_proxy This fixes issue #3296 where the no_proxy environment variable was ignored on 302 redirects. The root cause was in resolve_proxies() - when should_bypass_proxies() returned True, the function only avoided adding new environment proxies, but did not remove existing proxies that were passed in from the original request. Changes: - Modifie... #7188: DMTF Redfish fails with 2.32 request module The DMTF redfish is failing with the latest Request module the issue is not seen with requests-2.31.0. The issue is in file requests/sessions.py, self.prepare_request(req) function where by encoding "%" url converts to "%25" while preparing request url. Debug - > /usr/local/lib/python3.12/site-packages/requests/sessions.py(564)request() -> method... #7184: Remove quotes on qop and algorithm values for Digest auth This fixes https://github.com/psf/requests/issues/5745 ## Motivation The [RFC7616](https://datatracker.ietf.org/doc/html/rfc7616) states the following for the Authorization header: "For historical reasons, a sender MUST NOT generate the quoted string syntax for the following parameters: algorithm, qop, and nc." The examples provided... #7183: docs: Clarify connect timeout also includes time for sending request body Adding to the docs that connect timeout includes the time for sending the request body, so people don't set this timeout too low for larger POST requests. #7181: docs: clarify gzip decompression behavior when using stream=True When using stream=True, Requests does not automatically decompress gzip-encoded responses when accessing Response.raw. This behavior is intentional but not clearly documented and has caused repeated confusion. This PR adds a note in the advanced documentation explaining the behavior and shows how to enable decompression manua... #7151: Fix: Correct Content-Length for StringIO with multi-byte characters ## Summary Fixes a regression where `Content-Length` is incorrectly calculated for `io.StringIO` objects containing multi-byte characters (like emojis). ## The Issue When `io.StringIO` is passed as `data`, [super_len](cci:1://file:///c:/Users/Harish/OneDrive/Desktop/webdev/sample%20project/requests_repo/src/requests/utils....

Commits
79f4df84cf77a2fee873809821dfbd786de05b97: Bump pypa/gh-action-pypi-publish from 1.13.0 to 1.14.0 (#7378) Bumps [pypa/gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish) from 1.13.0 to 1.14.0. - [Release notes](https://g... b294b08fe3b08df9f646f64d1250f96193dab066: Consider urllib3 version has 3 numbers at most (#7375) (#7376) 5f3ff9b9e4d3d44960467e7877e06e9efa22f20b: Fix typos discovered by codespell (#7371) 514c1623fefff760bfa15a693aa38e474aba8560: Update README and remove extraneous images (#7366) d2f6bdecc835475c3cfbb83565ea879d7b2712ca: Clarify decode_unicode behaviour in iter_{content,lines} docstrings (#7365) a044b020dea43230585126901684a0f30ec635a8: Move DigestAuth hash algorithms to use usedforsecurity=False (#7310) 16df2a09173b17a82d607c31a6826b7c5dd0fc57: Move pytest pin to support 9.x series (#7364) fe2063be0cfbc08150ef468ae57a708c2514a321: Don't hide navigation on mobile webpage (#7360) 4b0b1a3e9f2fc21b9dcd8b906f2ff02645aa697e: Update pre-commit versions (#7348) * Update pre-commit versions * Add dependabot entry for pre-commit * Update pre-commit and ruff hooks 185f587a78e2d2df31ae8af8c95d97a012213df7: Cleanup docs and add i18n wrappers (#7354)

Repository signals
Pages URL: none GitHub Actions workflows: 10 Recent releases: v2.33.1, v2.33.0, v2.32.5, v2.32.4, v2.32.3 Language composition: Python=99.3%, Makefile=0.7% Root dirs: tests, docs_dir

Architecture overview
The Requests repository provides a simple HTTP library, accessible via `import requests`. Its `tests` directory ensures the library's functionality, while `docs_dir` contains its documentation.
```


ATOMIC FACTS:
(see per-URL sections above)

SOURCE:
(see per-URL sections above)

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): e44b61aa0433612781d343b14e708293d5e0a0d5f726870aa3e5189c749f655e
