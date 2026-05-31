# Finale QA Worker Kernel Roleplay

## Source

Input file: `plan/finale-qa-test-20260531-110146.json`

This is a research/simulation artifact only. It does not change runtime code.

## Purpose

Roleplay what the worker kernel and workers would do if they received the envelope/plan pairs from the finale QA file as streamed runtime data.

The focus is exact runtime behavior by worker type and step, not planner correction.

## Global Kernel Rules For This Simulation

The kernel treats the `Plan` as the executable contract.

The kernel treats `Envelope.artifacts` as hints only. They are not runtime artifacts and do not satisfy `step.input_artifacts`.

The kernel streams only these inputs to a worker:

- The current `Task` compiled from `PlanStep`.
- The resolved prior runtime artifacts named in `step.input_artifacts`.
- A safe envelope summary for provenance when useful.
- No undeclared file, command, or write capability.

The kernel blocks rather than fabricates when required runtime artifacts or evidence are absent.

The current finale file mostly contains direct-support plans. For those, the only worker invoked is `direct_worker`.

## Worker Types Appearing In The File

- `direct_worker`
- `repo_worker`
- `research_worker`
- `code_worker`
- `verify_worker`

No `infra_worker` step appears in this file.

## Kernel Run Admission Checklist

For each run, the kernel would check:

- `plan.request_id` matches `envelope.request_id`.
- Budget covers workers, tool calls, and model calls.
- Worker type is registered.
- Every step has explicit `read_files`, `write_files`, and `run_commands` booleans.
- Every `step.input_artifacts` exists before dispatch.
- `write_files=true` only appears on `code_worker` mutation steps.
- Write steps have `write_paths_from_artifacts` or concrete `write_paths`.
- Direct-support steps have no file/command/write permissions.

## Run 1: `req_001` Gratitude Acknowledgment

Prompt: `thanks, that helped`

Plan: `plan_req_001_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Confirms budget: `max_workers=1`, `max_tool_calls=0`, `max_model_calls=1`.
- Confirms no input artifacts required.
- Confirms all permissions are false.
- Dispatches `direct_support_response` to `direct_worker`.
- Receives `direct_guidance` artifact.
- Completes run.

### direct_worker

Inputs streamed:

- Envelope raw_input: `thanks, that helped`
- Envelope normalized_input: `User expresses gratitude and indicates prior help was useful.`
- Envelope user_goal: `Acknowledge successful assistance and thank the assistant.`
- Envelope input_type: `gratitude_acknowledgment_message`
- Envelope intents: `["conversation.acknowledge_help", "conversation.express_gratitude"]`
- Envelope domains: `["general", "conversation"]`
- Envelope risks: `["no_actionable_work_item"]`
- Envelope artifacts: `[]`
- Envelope context_needed: `[]`
- Envelope constraints: `["do_not_infer_new_work_without_explicit_request", "preserve_non_actionable_acknowledgment_semantics"]`
- Envelope ambiguity: `["No new actionable task is requested."]`
- Envelope assumptions: `["The message is a conversational acknowledgment rather than a new task."]`
- Plan objective: `Provide direct conversational support by acknowledging the user’s gratitude and offering optional next-step help without any runtime actions.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User said “thanks, that helped,” indicating successful prior assistance and no explicit new task request. Unknowns: Whether the user wants any additional help right now. Do now: Respond with a brief friendly acknowledgment and invite a new question if needed. Do not do: Do not use tools, files, commands, or invent unsupported details. Output: direct_guidance with polite acknowledgment and safe optional next step.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- Generate brief acknowledgment.
- Do not ask for files, tools, or commands.

Artifact emitted:

- `direct_guidance`: `You're welcome. I'm glad that helped. If you want to continue or have another question, send it over.`

User response:

> You're welcome. I'm glad that helped. If you want to continue or have another question, send it over.

## Run 2: `req_002` Ambiguous Identity Query

Prompt: `who am i`

Plan: `plan_req_002_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Confirms no tools/files/commands.
- Dispatches `direct_support_response`.
- Stores `direct_guidance`.
- Completes run.

### direct_worker

Inputs streamed:

- Envelope raw_input: `who am i`
- Envelope normalized_input: `The user asks, "who am I."`
- Envelope user_goal: `Determine the user's identity.`
- Envelope input_type: `ambiguous_identity_query`
- Envelope intents: `["identity.lookup", "self_context.clarification"]`
- Envelope domains: `["general"]`
- Envelope risks: `["ambiguous_scope", "insufficient_context", "privacy_sensitive"]`
- Envelope artifacts: `[]`
- Envelope context_needed: `["what identity dimension is intended (name, role, account, personal traits)", "whether account/profile metadata is available", "any prior conversation context the user wants used"]`
- Envelope constraints: `["do_not_claim_or_infer_personal_identity_without_verified_context", "avoid fabricating personal facts", "treat identity-related details as privacy-sensitive"]`
- Envelope ambiguity: `["\"I\" is pronoun-only and does not specify which identity aspect is requested.", "No profile, account, or biographical context is provided."]`
- Envelope assumptions: `["The user is asking a broad self-identity question rather than a technical identifier lookup."]`
- Plan objective: `Provide direct support to clarify the user’s intended identity dimension and offer safe immediate guidance from the provided input only.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User asked "who am i" with no profile/account/biographical context. Identity details are privacy-sensitive and must not be inferred. Unknowns: Which identity dimension is intended (name, role, account, personal traits), whether profile metadata exists, and any prior context to use. Do now: Ask concise clarifying questions (e.g., "Do you mean your account username, your name/role, or a personal self-reflection question?") and provide immediate safe guidance to share only non-sensitive context needed for help. Do not do: Do not use tools, files, or commands; do not invent personal facts or claim verified identity. Output: direct_guidance with clarification questions and safe next steps.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- Ask what identity dimension the user means.
- Avoid claiming identity or account details.

Artifact emitted:

- `direct_guidance`: `I don't have enough context to know who you are. Do you mean your name/account identity, your role, or a more personal self-reflection question? If you share the context you want me to use, I can help safely without guessing personal details.`

User response:

> I don't have enough context to know who you are. Do you mean your name/account identity, your role, or a more personal self-reflection question? If you share the context you want me to use, I can help safely without guessing personal details.

## Run 3: `req_003` Today's Date

Prompt: `what is today's date`

Plan: `plan_req_003_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Notices envelope says date needs runtime clock, but plan explicitly has `no_tools`, `no_file_access`, and no command/tool access.
- Does not inject a clock because the plan forbids tools and says answer from user input only.
- Dispatches `direct_support_response` with no runtime clock artifact.
- Stores `direct_guidance`.
- Completes run with caveat that no runtime date source was available to worker.

### direct_worker

Inputs streamed:

- Envelope raw_input: `what is today's date`
- Envelope normalized_input: `User asks for the current date ("today's date").`
- Envelope user_goal: `Find out today's date.`
- Envelope input_type: `current_date_lookup_query`
- Envelope intents: `["time.lookup", "date.retrieve"]`
- Envelope domains: `["general", "time"]`
- Envelope risks: `["timezone_unspecified", "needs_runtime_clock"]`
- Envelope artifacts: `[{"name": "today's date", "type": "temporal_reference"}]`
- Envelope context_needed: `["current_date_source", "applicable_timezone_or_locale"]`
- Envelope constraints: `["date_response_must_use_runtime_current_date", "timezone_assumption_must_be_explicit_if_not_provided"]`
- Envelope ambiguity: `["No timezone or locale is specified, so the exact date may vary by region."]`
- Envelope assumptions: `["Assume the user wants the current calendar date at response time.", "Assume Gregorian calendar unless specified otherwise."]`
- Plan objective: `Provide the current date directly, while explicitly handling timezone ambiguity with a brief clarification or stated assumption.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User asks for today's date; timezone/locale is not specified; Gregorian calendar is assumed unless user says otherwise. Unknowns: Applicable timezone or locale for determining "today." Do now: Ask a concise timezone clarification if needed and provide immediate date guidance with an explicit timezone assumption when unavailable. Do not do: Do not use tools, files, or commands; do not invent unsupported provider-specific facts. Output: direct_guidance with the date response and safe next-step clarification.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- State limitation.
- Ask timezone or say that current-date lookup needs runtime clock access.

Artifact emitted:

- `direct_guidance`: `I need a runtime date source or your timezone to answer exactly. If you want the date for your local timezone, tell me your location/timezone, or allow a runtime date lookup.`

User response:

> I need a runtime date source or your timezone to answer exactly. If you want the date for your local timezone, tell me your location/timezone, or allow a runtime date lookup.

Kernel note:

- This plan is internally conservative but practically weak: the envelope requested runtime current date, while the direct-support plan denied tools. A stricter future kernel would return `blocked_runtime_context_missing` or route back for replan rather than pretend it knows the date.

## Run 4: `req_004` Standard Water Bottle Liters

Prompt: `how many liters are in a standard water bottle`

Plan: `plan_req_004_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Confirms no runtime artifacts or tools.
- Dispatches `direct_support_response`.
- Stores `direct_guidance`.

### direct_worker

Inputs streamed:

- Envelope raw_input: `how many liters are in a standard water bottle`
- Envelope normalized_input: `Determine the liter volume of a typical “standard” water bottle.`
- Envelope user_goal: `Get a quick unit-level estimate for how many liters a standard water bottle holds.`
- Envelope input_type: `ambiguous_consumer_container_volume_lookup`
- Envelope intents: `["research.lookup", "units.convert", "consumer_reference.clarify"]`
- Envelope domains: `["general", "consumer", "measurement"]`
- Envelope risks: `["ambiguous_scope", "term_not_standardized", "needs_verification"]`
- Envelope artifacts: `[{"name": "liters", "type": "unit"}, {"name": "standard water bottle", "type": "consumer_container"}]`
- Envelope context_needed: `["target market/region", "bottle type (single-use vs reusable)", "whether an exact value or common range is desired"]`
- Envelope constraints: `["do_not_assume_single_global_standard_volume", "state_assumptions_if_using_typical_size", "distinguish_estimate_from_exact_specification"]`
- Envelope ambiguity: `["“Standard water bottle” is not universally defined and can refer to multiple common sizes.", "No brand, region, or bottle category is specified."]`
- Envelope assumptions: `["User likely wants a common everyday reference size rather than a brand-specific exact capacity.", "Metric output in liters is the preferred unit."]`
- Plan objective: `Provide a direct estimate for the liter volume of a typical “standard” water bottle, while clarifying ambiguity and stating assumptions.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User asks how many liters are in a “standard water bottle”; term is ambiguous and not globally standardized; user likely wants a quick everyday estimate in liters. Unknowns: target region/market, bottle type (single-use vs reusable), and whether user wants one exact value or a common range. Do now: Give an immediate safe estimate with assumptions (e.g., common single-use bottle around 0.5 L, with common range about 0.33–1.0 L) and ask one concise clarifying question to refine if needed. Do not do: Do not use tools, files, or commands; do not invent brand- or region-specific exact standards. Output: direct_guidance with estimate, assumptions, and a brief clarification question.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- Provide a common estimate and clarify ambiguity.

Artifact emitted:

- `direct_guidance`: `There is no single global standard, but a common single-use water bottle is about 0.5 liters. Common bottles can range from about 0.33 L to 1.0 L depending on brand, region, and whether it is single-use or reusable. Do you mean a disposable bottle or a reusable bottle?`

User response:

> There is no single global standard, but a common single-use water bottle is about 0.5 liters. Common bottles can range from about 0.33 L to 1.0 L depending on brand, region, and whether it is single-use or reusable. Do you mean a disposable bottle or a reusable bottle?

## Run 5: `req_005` MRT Card Failure

Prompt: `my mrt card is not working and i need to commute tomorrow with no car`

Plan: `plan_req_005_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Confirms no tools/files/commands.
- Dispatches `direct_support_response`.
- Stores `direct_guidance`.

### direct_worker

Inputs streamed:

- Envelope raw_input: `my mrt card is not working and i need to commute tomorrow with no car`
- Envelope normalized_input: `The user says their MRT transit card is not working, and they must commute tomorrow without access to a car.`
- Envelope user_goal: `Find a way to complete tomorrow’s commute despite a non-working MRT card and no car.`
- Envelope input_type: `public_transit_card_failure_commute_urgency`
- Envelope intents: `["mobility.issue_report", "transit.access.restore", "commute.contingency_identification"]`
- Envelope domains: `["transportation", "general"]`
- Envelope risks: `["urgent_deadline", "service_disruption_impact", "needs_local_transit_policy_verification"]`
- Envelope artifacts: `[{"name": "mrt card", "type": "transit_card"}, {"name": "commute tomorrow", "type": "time_constraint"}, {"name": "car", "type": "vehicle"}]`
- Envelope context_needed: `["city_or_transit_network_for_mrt", "card_type_and_issuer", "observed_failure_mode_of_card", "available_replacement_or_top_up_channels", "accepted_alternative_fare_payment_methods", "commute_route_and_time_window"]`
- Envelope constraints: `["do_not_assume_root_cause_of_card_failure_without_evidence", "recommendations_must_match_local_transit_rules_and_payment_options", "time_sensitive_guidance_should_prioritize_next_day_commute_feasibility"]`
- Envelope ambiguity: `["MRT system location is unspecified.", "Exact card problem (damaged card, insufficient balance, backend issue, blocked card) is unknown.", "Commute route, timing, and transit alternatives are not provided."]`
- Envelope assumptions: `["The user relies on MRT for commuting.", "The commute is necessary tomorrow.", "The user currently does not have access to a car."]`
- Plan objective: `Help the user secure a feasible, legal way to commute tomorrow despite a non-working MRT card and no car, using only provided input and clarifying questions.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User says their MRT card is not working, they must commute tomorrow, and they do not have a car. Unknowns: City/transit network, card type/issuer, exact failure mode, replacement or top-up channels, accepted alternative payment methods, and commute route/time window. Do now: Ask concise clarifying questions (city/network, card type, exact error, route/time) and provide immediate safe guidance: check card balance/expiry/physical damage, try official ticket office/help point/app, prepare backup fare method accepted locally, and identify non-car backup transit options for tomorrow. Do not do: Do not use tools, files, or commands; do not invent provider-specific rules; do not suggest fare evasion or unsupported claims about root cause. Output: direct_guidance with brief questions plus actionable next steps for tomorrow commute readiness.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- Ask local transit/card details.
- Give safe non-provider-specific backup steps.
- Avoid fare evasion or invented rules.

Artifact emitted:

- `direct_guidance`: `A few details will decide the best option: what city or MRT network is this, what card type is it, and what happens when you tap it? For tomorrow, check balance/expiry/physical damage now, try the official app or ticket office/help point if available, prepare another accepted payment method if your system supports one, and identify a non-car backup such as bus, train ticket, ride-share/taxi for only the critical segment, or asking someone for a lift. Do not rely on unverified fare rules.`

User response:

> A few details will decide the best option: what city or MRT network is this, what card type is it, and what happens when you tap it? For tomorrow, check balance/expiry/physical damage now, try the official app or ticket office/help point if available, prepare another accepted payment method if your system supports one, and identify a non-car backup such as bus, train ticket, ride-share/taxi for only the critical segment, or asking someone for a lift. Do not rely on unverified fare rules.

## Run 6: `req_006` Relationship Repair

Prompt: `i have a girlfriend who is upset with me. help me make it up to her`

Plan: `plan_req_006_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Dispatches `direct_support_response` without tools/files/commands.

### direct_worker

Inputs streamed:

- Envelope raw_input: `i have a girlfriend who is upset with me. help me make it up to her`
- Envelope normalized_input: `The user says their girlfriend is upset with them and asks for help making amends.`
- Envelope user_goal: `Get guidance to repair the relationship by apologizing and making it up to their girlfriend.`
- Envelope input_type: `relationship_repair_advice`
- Envelope intents: `["relationship.repair", "communication.guidance", "apology.support"]`
- Envelope domains: `["relationships", "communication", "general"]`
- Envelope risks: `["ambiguous_scope", "one_sided_account", "emotional_harm_if_mishandled", "needs_context_for_safe_advice"]`
- Envelope artifacts: `[{"name": "girlfriend", "type": "person_role"}, {"name": "upset", "type": "emotional_state"}, {"name": "make it up", "type": "reconciliation_goal"}]`
- Envelope context_needed: `["what specifically happened", "whether harm was emotional, trust-related, or practical", "girlfriend’s stated needs or boundaries", "timeline and urgency of the conflict", "what apologies or repair attempts were already made", "any safety concerns or abusive dynamics"]`
- Envelope constraints: `["advice should avoid manipulation, pressure, or guilt tactics", "respect consent and personal boundaries", "encourage accountability and truthful communication", "do not assume fault details without user-provided facts", "recommend repair actions proportionate to the described issue"]`
- Envelope ambiguity: `["cause of the girlfriend being upset is not provided.", "severity of the conflict is unknown.", "no details on prior attempts to resolve the issue.", "relationship context and boundaries are unspecified."]`
- Envelope assumptions: `["the user is seeking constructive, non-harmful reconciliation advice.", "the relationship is ongoing.", "no immediate safety threat is disclosed in the prompt."]`
- Plan objective: `Provide safe, consent-respecting relationship repair guidance from the user input only, without any runtime actions.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User says their girlfriend is upset and wants help making amends; no safety threat is disclosed; advice must avoid manipulation, pressure, or guilt and respect boundaries. Unknowns: What happened, severity, her stated needs/boundaries, timeline/urgency, and what apology or repair attempts were already made. Do now: Ask 2-4 concise clarifying questions (what happened, what she said she needs, what was already tried, any safety concerns) and give immediate harmless guidance: acknowledge impact, give a sincere specific apology, ask what repair would help, and offer proportionate follow-through with no pressure. Do not do: Do not use tools/files/commands, do not assume fault details not provided, and do not invent unsupported facts. Output: direct_guidance with clarifying questions plus safe next-step wording the user can send or say.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- Ask clarifying questions.
- Provide non-manipulative repair script.

Artifact emitted:

- `direct_guidance`: `What happened, what did she say she needs, and have you already apologized or tried to fix it? Start with accountability: "I can see I hurt/upset you, and I'm sorry. I want to understand what landed badly and what would help repair it. I won't pressure you, but I'm ready to listen and follow through." Then give her space, avoid excuses, and offer a concrete repair that matches what happened.`

User response:

> What happened, what did she say she needs, and have you already apologized or tried to fix it? Start with accountability: "I can see I hurt/upset you, and I'm sorry. I want to understand what landed badly and what would help repair it. I won't pressure you, but I'm ready to listen and follow through." Then give her space, avoid excuses, and offer a concrete repair that matches what happened.

## Run 7: `req_007` Cozy 12-Chapter Outline

Prompt: `write a cozy 12-chapter book outline about rebuilding a small town library after a flood`

Plan: `plan_req_007_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Dispatches one direct response step.

### direct_worker

Inputs streamed:

- Envelope raw_input: `write a cozy 12-chapter book outline about rebuilding a small town library after a flood`
- Envelope normalized_input: `Create a cozy 12-chapter book outline about rebuilding a small-town library after a flood.`
- Envelope user_goal: `Obtain a structured, chapter-by-chapter outline for a cozy-themed book centered on a community library rebuilding effort after flood damage.`
- Envelope input_type: `creative_writing_book_outline_generation`
- Envelope intents: `["content.generate", "narrative.outline", "tone.cozy", "story.structuring"]`
- Envelope domains: `["creative_writing", "literature", "docs"]`
- Envelope risks: `["underspecified_character_details", "underspecified_setting_details", "tone_interpretation_variance", "scope_depth_unspecified"]`
- Envelope artifacts: `[{"name": "12-chapter book outline", "type": "deliverable"}, {"name": "small town library", "type": "setting_component"}, {"name": "flood", "type": "inciting_event"}, {"name": "cozy tone", "type": "style_constraint"}]`
- Envelope context_needed: `["target audience age range", "preferred subgenre boundaries for cozy tone", "desired chapter detail depth", "character roster or protagonist preference", "time period and geographic setting preferences", "ending preference (optimistic/bittersweet/open)"]`
- Envelope constraints: `["must produce exactly 12 chapters", "must center on rebuilding a small-town library", "must include post-flood context as core premise", "must maintain a cozy tone throughout", "should avoid inventing external facts not provided by the user"]`
- Envelope ambiguity: `["No protagonist or core character relationships specified.", "No target audience or reading level specified.", "No preferred setting region or time period specified.", "No chapter granularity (brief beats vs detailed summaries) specified."]`
- Envelope assumptions: `["The output should be an original fictional outline.", "'Cozy' implies warm, community-focused, low-grit narrative tone.", "The flood is a prior disruptive event that motivates the rebuilding arc."]`
- Plan objective: `Provide direct support for creating a cozy 12-chapter book outline using only the provided user input, with concise clarification prompts where details are missing.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User wants an original cozy 12-chapter book outline about rebuilding a small-town library after a flood; must be exactly 12 chapters and keep a cozy tone. Unknowns: Target audience age range, setting/time period preference, protagonist/character roster, chapter detail depth, and ending preference. Do now: Ask a few concise clarifying questions if the user wants customization, then provide immediate safe guidance by drafting a complete 12-chapter cozy outline using clear assumptions if no answers are provided yet. Do not do: Do not use tools, files, or commands; do not invent unsupported external/provider-specific facts; do not violate the exact 12-chapter requirement or cozy tone constraint. Output: direct_guidance with either clarification questions plus a usable provisional outline, or a finalized outline if preferences are provided.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- Provide a complete provisional outline without tools.

Artifact emitted:

- `direct_guidance`: a 12-chapter outline.

User response:

> Working assumption: contemporary small town, warm community tone, optimistic ending.
>
> 1. The Waterline On The Door: The town discovers the beloved library is too damaged to reopen after the flood.
> 2. A Box Of Returned Books: Volunteers gather salvaged books and memories, proving the library still matters.
> 3. The Temporary Reading Room: A bakery back room becomes a makeshift library and community hub.
> 4. Blueprints And Biscuits: The rebuild committee forms, mixing practical worries with neighborly hope.
> 5. The Missing Donor Ledger: Old records reveal small donors who kept the library alive for generations.
> 6. Story Hour In The Park: A public reading event reminds the town that the library is more than a building.
> 7. Shelves From Barn Wood: Local craftspeople offer materials, skills, and gentle comic disagreements.
> 8. Rain In The Forecast: Another storm tests morale and the unfinished repairs.
> 9. The Quiet Room: The protagonist learns why one resident has been resisting the rebuild.
> 10. A Festival Of Pages: A fundraiser brings food, music, donated books, and renewed trust.
> 11. The First Library Card: The rebuilt library opens softly for volunteers and families.
> 12. A Light In Every Window: The grand reopening celebrates the town's resilience and a new chapter together.

## Run 8: `req_008` API Gateway Vs Load Balancer

Prompt: `explain api gateway vs load balancer and give a simple decision checklist`

Plan: `plan_req_008_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Dispatches `direct_support_response` with no tools.

### direct_worker

Inputs streamed:

- Envelope raw_input: `explain api gateway vs load balancer and give a simple decision checklist`
- Envelope normalized_input: `Explain the difference between an API gateway and a load balancer, and provide a simple checklist for deciding which to use.`
- Envelope user_goal: `Understand API gateway vs load balancer roles and get a practical, simple decision aid for selection.`
- Envelope input_type: `networking_component_comparison_guidance`
- Envelope intents: `["concept.explain", "architecture.compare", "decision.support"]`
- Envelope domains: `["infra", "networking", "docs"]`
- Envelope risks: `["ambiguous_scope", "context_missing_for_recommendation", "oversimplification_risk"]`
- Envelope artifacts: `[{"name": "API gateway", "type": "component"}, {"name": "load balancer", "type": "component"}, {"name": "decision checklist", "type": "deliverable"}]`
- Envelope context_needed: `["target environment (cloud/vendor or on-prem)", "traffic types and protocols (HTTP, TCP, gRPC, etc.)", "required features (auth, rate limiting, routing rules, observability)", "scale and availability requirements"]`
- Envelope constraints: `["do_not_invent_environment_specific_facts", "differentiate_layer7_api_management_from_traffic_distribution_functions", "recommendations_should_be_conditioned_on_stated_requirements"]`
- Envelope ambiguity: `["No deployment context is provided (vendor/platform unknown).", "No workload details are provided (internal APIs, public APIs, or mixed).", "Checklist depth is unspecified beyond 'simple'."]`
- Envelope assumptions: `["User wants a vendor-neutral conceptual comparison.", "User expects a brief, practical checklist rather than deep implementation detail."]`
- Plan objective: `Provide direct support for understanding API gateway vs load balancer and give a simple, vendor-neutral decision checklist using only the provided input.`
- Plan strategy: `phase_aware_direct_support`
- Plan global_invariants: `["no_tools", "no_file_access", "answer_from_user_input_only"]`
- Step phase/mode/task_id: `FINALIZE` / `summarize_only` / `direct_support`
- Step instruction: `Known facts: User asked for a conceptual difference between API gateway and load balancer plus a simple decision checklist; environment/vendor is not specified; guidance must stay vendor-neutral. Unknowns: Target environment (cloud/on-prem), traffic/protocol mix (HTTP/TCP/gRPC), required features (auth, rate limiting, advanced routing, observability), and scale/availability needs. Do now: Give a concise comparison, provide an immediate simple checklist, and ask brief clarifying questions to tailor a recommendation. Do not do: Do not use tools, files, or commands; do not invent provider-specific capabilities; do not present unconditional recommendations without stated requirements. Output: direct_guidance with safe next-step guidance and concise clarification questions.`
- Step input_artifacts: `[]`
- Step output_artifacts: `["direct_guidance"]`
- Step permissions: `{"read_files": false, "run_commands": false, "write_files": false}`

Action:

- Produce vendor-neutral explanation and checklist.

Artifact emitted:

- `direct_guidance`: concise comparison/checklist.

User response:

> A load balancer mainly distributes traffic across backend instances to improve availability and capacity. An API gateway usually sits at the API boundary and adds application-layer concerns like authentication, rate limiting, request routing, versioning, transformations, and API observability.
>
> Simple checklist:
>
> - Need to spread traffic across servers? Use a load balancer.
> - Need auth, rate limits, API keys, quotas, or request policies? Use an API gateway.
> - Need both API policy and backend scaling? Use both.
> - Mostly TCP or non-HTTP traffic? Load balancer is usually the starting point.
> - Public APIs with multiple clients/versions? API gateway is usually helpful.
>
> To tailor this: are you handling HTTP APIs, gRPC, raw TCP, or a mix?

## Run 9: `req_009` Payment Dispute Duplicate Callback Bugfix

Prompt: payment dispute processor closes fraud cases when duplicate bank callbacks arrive after manual review lock expires; identify root cause, apply safe fix, include rollback plan, and verify with focused dispute-state checks

Plan: `plan_req_009_dispute_callback_state_fix`

Workers: `repo_worker`, `research_worker`, `repo_worker`, `code_worker`, `code_worker`, `verify_worker`, `research_worker`.

Important simulation constraint:

- The JSON gives a plan, not the actual payment service repository.
- A truthful kernel/worker simulation must not invent code paths, root cause, patch, or test results.
- If only the streamed envelope/plan is available and no repository filesystem is mounted, the kernel should block before mutation.

### Kernel Initial Admission

- Accepts plan shape.
- Budget allows 7 workers, 21 tool calls, 7 model calls.
- Builds artifact store: empty.
- Streams envelope summary as provenance only.
- Does not convert envelope artifacts like `payment dispute processor` into files.

### Step 1: `discover_dispute_processor_targets` / `repo_worker`

Permissions:

- `read_files=true`
- `write_files=false`
- `run_commands=false`

Kernel action:

- Tries to provide read-only repository capability.
- If no target repository/service is mounted, dispatch still cannot discover real files.

repo_worker action with only stream data:

- Reads plan instruction and envelope hints.
- Does not fabricate repository paths.
- Emits a discovery artifact stating repository context is unavailable and listing what must be mounted/provided.

Artifacts emitted:

- `repo_inventory`: `blocked_partial: no target payment-dispute repository mounted in runtime stream.`
- `candidate_code_locations`: `unresolved; likely search terms only: dispute processor, bank callback handler, manual review lock, fraud case state transition, idempotency/deduplication.`
- `event_timeline_requirements`: `need duplicate callback payloads/event IDs, lock expiry timestamps, state transition logs, fraud case before/after states.`

Kernel after step:

- Stores three artifacts.
- Marks them as low-confidence/insufficient for mutation.

### Step 2: `analyze_root_cause_evidence` / `research_worker`

Input artifacts:

- `repo_inventory`
- `candidate_code_locations`
- `event_timeline_requirements`

Kernel action:

- Resolves inputs from artifact store.
- Dispatches read-only task.

research_worker action:

- Reviews discovery artifacts.
- Determines evidence is insufficient because no code paths/logs were available.
- Does not infer root cause.

Artifacts emitted:

- `root_cause_evidence`: `insufficient_evidence; no code/log path available to prove callback idempotency or lock-expiry transition behavior.`
- `evidence_artifacts`: `missing_required_inputs: repository/service code, callback samples, event timeline, state machine definition, existing tests.`
- `evidence_gap_decision`: `STOP_BEFORE_MUTATION; evidence gap is material and violates evidence_based_root_cause_before_mutation.`

Kernel after step:

- Stores artifacts.
- Sees `evidence_gap_decision=STOP_BEFORE_MUTATION`.
- A strict future kernel should stop here or require replan. It should not continue to DESIGN/MUTATE.

### Step 3: `research_dependencies_and_tests` / `repo_worker`

If the kernel continues in observation-only mode:

- It resolves `candidate_code_locations` and `repo_inventory`.
- It dispatches `repo_worker` read-only.

repo_worker action:

- Cannot identify actual tests/dependencies without repository.

Artifacts emitted:

- `dependency_artifacts`: `unresolved; dependency/test surfaces cannot be inspected without target repository.`
- `baseline_verification_targets`: `desired checks only: duplicate callback idempotency, post-lock-expiry state transition, fraud case must not close incorrectly, rollback readiness.`

### Step 4: `design_scoped_fix_and_rollback` / `code_worker`

Kernel strict behavior:

- Blocks before dispatch because `evidence_gap_decision` says stop before mutation and no real `mutation_scope` can be safely produced.

Blocked result:

- status: `blocked`
- reason: `insufficient_evidence_for_design_and_mutation`
- replan recommended: `true`

If forced to roleplay code_worker without blocking:

- It must not design a concrete patch.
- It emits only a blocked design artifact.

Artifacts that would be emitted in forced observe-only simulation:

- `fix_design`: `blocked; cannot design concrete fix without target code and evidence.`
- `mutation_scope`: `unresolved; no paths approved.`
- `allowed_write_paths`: `[]`
- `rollback_plan`: `rollback unavailable until concrete changed files are known.`
- `verification_plan`: `desired tests listed, but not executable without repo/test harness.`

### Step 5: `mutate_apply_scoped_fix` / `code_worker`

Kernel strict behavior:

- Does not dispatch.
- Reason: `mutation_scope` and `allowed_write_paths` are unresolved/empty.
- `write_files=true` cannot be granted.

No mutation artifacts should be emitted.

### Step 6: `verify_targeted_dispute_state_checks` / `verify_worker`

Kernel strict behavior:

- Does not dispatch because there is no `change_summary`, no `rollback_patch`, and no applied mutation.

No verification pass should be claimed.

### Step 7: `finalize_fix_report` / `research_worker`

Kernel strict behavior:

- If stopped early, finalization can still produce a user-facing blocked report from available artifacts.

research_worker action:

- Summarize why execution stopped and what inputs are needed next.

User response:

> I cannot safely identify root cause or apply a fix from the provided stream alone because no target repository, code paths, logs, state machine definition, callback samples, or test harness were available. The safe result is to stop before mutation. To continue, provide or mount the dispute-processing repository/service and include callback event samples, manual-review lock expiry rules, current fraud/dispute state machine behavior, existing duplicate-callback tests, and rollback/deployment details. Once those are available, the worker flow can discover targets, gather evidence, design a scoped fix, mutate only approved paths, run focused dispute-state checks, and produce rollback artifacts.

## Run 10: `req_010` Multi-Tenant Audit Export Isolation Incident

Prompt: multi-tenant audit export service intermittently leaks cross-tenant records when async shard compaction overlaps resumable export pagination after failover; identify root cause, design a safe scoped fix, include rollback plan, verify tenant isolation with focused regression checks, and summarize residual risks

Plan: `plan_req_010_incident_fix`

Workers: `repo_worker`, `research_worker`, `code_worker`, `code_worker`, `verify_worker`, `research_worker`.

Important simulation constraint:

- No actual audit export service repository, logs, traces, data model, or test harness are present in the stream.
- The kernel must not let workers fabricate tenant-isolation evidence or claim verification.

### Kernel Initial Admission

- Accepts plan shape.
- Budget allows 6 workers, 19 tool calls, 6 model calls.
- Notes high-risk envelope: `cross_tenant_data_exposure`, `security`, `code`, `needs_verification`.
- Applies stricter confinement posture.
- Does not convert envelope artifacts into runtime paths.

### Step 1: `discover_export_isolation_surface` / `repo_worker`

Permissions:

- `read_files=true`
- `run_commands=false`
- `write_files=false`

repo_worker action with only stream data:

- Cannot inspect real modules.
- Emits unresolved discovery artifacts and required search surfaces.

Artifacts emitted:

- `repo_inventory`: `blocked_partial: no multi-tenant audit export repository mounted.`
- `architecture_map`: `unresolved; need export, compaction, pagination token, failover state handoff, tenant filter modules.`
- `candidate_paths`: `unresolved search terms only; no writable or readable concrete paths.`

### Step 2: `analyze_incident_evidence` / `research_worker`

Permissions:

- `read_files=true`
- `run_commands=true`
- `write_files=false`

Kernel action:

- Would expose read-only files and command runner only if a repo/log environment exists.
- Since no repo/logs exist, command execution is not useful and should be blocked or no-op.

research_worker action:

- Does not invent root cause.
- Identifies material evidence gap.

Artifacts emitted:

- `analysis_evidence`: `insufficient; no logs/traces/metrics/sample exports/code paths available.`
- `root_cause_evidence`: `not established.`
- `incident_timeline`: `unavailable; need timestamps for compaction, pagination resume, failover, and leaked export rows.`
- `dependency_artifacts`: `unresolved; need datastore consistency model, tenant partitioning invariants, pagination token schema, failover state handoff.`

Kernel after step:

- Stores artifacts.
- Marks mutation as unsafe because evidence-backed root cause is absent.

### Step 3: `design_scoped_fix_and_rollback` / `code_worker`

Kernel strict behavior:

- Blocks concrete design because `root_cause_evidence` is not established and `candidate_paths` are unresolved.

If forced to emit artifacts:

- `fix_design`: `blocked; cannot define scoped mitigation without evidence and paths.`
- `mutation_scope`: `unresolved; no concrete paths.`
- `rollback_plan`: `blocked; no changed files/config known.`
- `verification_plan`: `desired checks only: tenant isolation under compaction+pagination+failover overlap; rollback readiness; no cross-tenant rows.`

### Step 4: `apply_scoped_mitigation` / `code_worker`

Kernel strict behavior:

- Does not dispatch.
- `write_files=true` requires resolvable `mutation_scope`; none exists.
- Blocks with `confinement_scope_unresolved`.

No `change_summary` or `rollback_patch` should be emitted.

### Step 5: `verify_isolation_and_regressions` / `verify_worker`

Kernel strict behavior:

- Does not dispatch because no mutation occurred and required inputs are absent.
- No verification pass is claimed.

### Step 6: `finalize_incident_report` / `research_worker`

research_worker action:

- Summarize blocked execution and next required inputs.

User response:

> I cannot safely identify root cause, design a concrete scoped fix, or verify tenant isolation from this stream alone. The plan requires repository modules, tenant-partitioning invariants, pagination token schema, compaction/failover implementation details, logs/traces/metrics, affected export samples, and existing regression tests. Because those are absent, the kernel should stop before mutation. Next step: provide or mount the audit export service context so discovery can resolve candidate paths, evidence can establish the failure mode, DESIGN can produce a concrete mutation_scope and rollback_plan, and VERIFY can run focused tenant-isolation checks.

## Run 11: `req_011` 90-Day Finance Recovery Plan

Prompt: `design a 90-day personal finance recovery plan after job loss with high-interest debt`

Plan: `plan_req_011_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Dispatches `direct_worker` without tools/files/commands.

### direct_worker

Action:

- Provide safe template and questions.
- Avoid inventing income/assets/legal eligibility.

User response:

> Start with a 90-day stabilization plan.
>
> Days 1-7: list cash on hand, essential bills, debt minimums, APRs, and due dates. Prioritize housing, food, utilities, transport, insurance, and minimum debt payments.
>
> Days 8-30: apply for unemployment/benefits you may qualify for, pause nonessential spending, call creditors before missing payments, and ask about hardship plans or temporary reduced payments.
>
> Days 31-60: build a bare-bones budget, target high-interest debt first after essentials/minimums, and create a weekly job-search or income plan.
>
> Days 61-90: review progress, rebuild a small emergency buffer, keep hardship agreements documented, and adjust debt payoff based on APRs and cash flow.
>
> To tailor it, what are your monthly essentials, cash on hand, debt balances/APRs/minimums, and expected income or benefits?

## Run 12: `req_012` Urban Congestion Strategies

Prompt: `compare three strategies to reduce urban traffic congestion without expanding highways`

Plan: `plan_req_012_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Dispatches `direct_worker`.

### direct_worker

Action:

- Compare exactly three non-highway strategies.
- Avoid unsupported quantitative claims.

User response:

> Three non-highway strategies:
>
> 1. Improve public transit frequency and reliability. This can shift trips away from cars when service is fast, safe, and predictable, but it needs funding and good coverage.
> 2. Congestion pricing or demand-based road charges. This can reduce peak-hour driving and fund alternatives, but it must include equity protections and viable substitutes.
> 3. Bus/bike/walking priority streets. Dedicated bus lanes, safer bike routes, and pedestrian upgrades can move more people in less space, but require political support and careful street design.
>
> Assumption: this is a conceptual comparison, not city-specific modeling. To tailor it, what city, time horizon, and criteria matter most: cost, speed, equity, emissions, or congestion reduction?

## Run 13: `req_013` Startup Data Exposure Crisis Communication Plan

Prompt: `draft a crisis communication plan for a startup after a data exposure incident`

Plan: `plan_req_013_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Dispatches `direct_worker` without tools/files/commands.

### direct_worker

Action:

- Produce practical communication plan.
- Mark assumptions.
- Avoid legal conclusions or invented incident facts.

User response:

> Assumptions: incident details are still being confirmed, legal review is required before external statements, and facts must be separated from unknowns.
>
> Crisis communication plan:
>
> - Appoint owners: incident lead, legal reviewer, customer comms owner, internal comms owner, and spokesperson.
> - Freeze facts: what happened, when discovered, what data may be affected, current containment status, and what is still unknown.
> - Segment audiences: employees, customers/users, partners, regulators, investors, and media.
> - Message principles: be accurate, concise, empathetic, consistent, and avoid speculation.
> - Internal update: explain what employees can say, what they must not say, and where to route questions.
> - Customer notice draft: acknowledge the issue, state known impact, explain immediate protective steps, and promise updates.
> - Approval flow: incident lead drafts, legal/security validates, executive approves, comms publishes.
> - Cadence: update on a schedule even if the update is "investigation ongoing."
>
> Clarifying questions: what data was exposed, what jurisdictions are involved, has containment happened, and who must be notified first?

## Run 14: `req_014` 8-Week Statistics Study Strategy

Prompt: `create a study strategy for learning statistics in 8 weeks while working full-time`

Plan: `plan_req_014_direct_support`

Steps: one `direct_worker` step.

### Kernel

- Accepts direct-support plan.
- Dispatches `direct_worker`.

### direct_worker

Action:

- Provide realistic schedule compatible with full-time work.

User response:

> Baseline plan assuming 5-7 hours per week:
>
> - Week 1: refresh arithmetic, percentages, graphs, and basic probability.
> - Week 2: descriptive statistics: mean, median, variance, standard deviation, percentiles.
> - Week 3: probability distributions: normal, binomial, sampling ideas.
> - Week 4: sampling, confidence intervals, and margin of error.
> - Week 5: hypothesis testing: p-values, null/alternative hypotheses, common mistakes.
> - Week 6: correlation and simple regression.
> - Week 7: practice interpreting real examples and solving mixed problems.
> - Week 8: review, weak-area drills, and a small final project or mock exam.
>
> Weekly rhythm: 3 short sessions on weekdays plus one longer weekend practice block. What is your current level, goal, and realistic weekly study time?

## Run 15: `req_015` Macroeconomic Scenario Analysis

Prompt: `propose an economic scenario analysis for inflation, rates, and consumer demand over the next 12 months`

Plan: `plan_req_015_macro_scenario_support`

Workers: `research_worker`, `direct_worker`.

### Kernel Initial Admission

- Accepts two-step non-file support plan.
- Budget allows 2 workers, 0 tools, 2 model calls.
- Confirms no file or command permissions.
- Artifact store starts empty.

### Step 1: `analyze_scope_and_scenarios` / `research_worker`

Permissions:

- `read_files=false`
- `run_commands=false`
- `write_files=false`

Kernel action:

- Dispatches with no tools and no input artifacts.
- Allows model-only reasoning within one model call.

research_worker action:

- Builds clarification questions.
- Creates assumption set.
- Creates scenario framework.
- Does not use external data or precise forecasts.

Artifacts emitted:

- `clarification_questions`:
  - `Which geography or market is in scope?`
  - `Do rates mean central-bank policy rates, consumer lending rates, or bond yields?`
  - `Which inflation measure should be used?`
  - `Which consumer segment or industry matters most?`
- `scenario_assumption_set`:
  - `12-month horizon from current response time.`
  - `No geography specified, so scenarios are generic macro narratives.`
  - `No precise point forecasts without data.`
- `scenario_framework`:
  - `Base: inflation gradually cools, rates stay restrictive then ease slowly, consumer demand softens but remains positive.`
  - `Upside: inflation cools faster, rates ease earlier, real incomes improve and demand stabilizes.`
  - `Downside: inflation remains sticky, rates stay high, credit-sensitive demand weakens.`

Kernel after step:

- Stores three artifacts.
- Dispatches final step because all inputs exist.

### Step 2: `finalize_user_ready_response` / `direct_worker`

Input artifacts:

- `clarification_questions`
- `scenario_assumption_set`
- `scenario_framework`

direct_worker action:

- Converts artifacts into a user-ready response.
- Includes questions and provisional scenario narrative.

Artifact emitted:

- `final_summary`: user-ready scenario analysis.

User response:

> Before tailoring it, I need four details: geography/market, what you mean by rates, preferred inflation measure, and whether demand is economy-wide or sector-specific.
>
> Provisional 12-month framework:
>
> - Base case: inflation cools gradually but remains a constraint; rates stay restrictive for part of the year and ease slowly if inflation improves; consumer demand softens but stays uneven, with essentials stronger than discretionary categories.
> - Upside case: inflation declines faster than expected, rate pressure eases earlier, real incomes improve, and consumer demand stabilizes more broadly.
> - Downside case: inflation remains sticky, rates stay higher for longer, credit costs weigh on households, and discretionary demand weakens.
>
> Key signals to monitor: wage growth, core inflation trend, central-bank guidance, credit delinquencies, retail sales, consumer confidence, and savings/credit utilization.

## Cross-Worker Roleplay Summary

### direct_worker

Used in runs:

- 1-8, 11-15.

What it does:

- Converts planner instruction and resolved support artifacts into user-facing guidance.
- Does not use files, commands, or tools when permissions are false.
- Produces `direct_guidance`, `final_summary`, or equivalent final artifact.

What it must not do:

- Invent personal identity, provider-specific transit/date facts, legal facts, or external data.
- Satisfy runtime date lookup without a runtime clock artifact.
- Treat envelope artifacts as verified facts beyond user-provided text.

### repo_worker

Used in runs:

- Run 9: discovery and dependency/test research.
- Run 10: discovery.

What it does:

- Reads mounted repository context when available.
- Produces discovery artifacts like `repo_inventory`, `candidate_code_locations`, `candidate_paths`, `architecture_map`, dependency/test target artifacts.

What it does with only the streamed QA data:

- Reports unresolved repository context.
- Emits requirements/search surfaces, not fake file paths.

What it must not do:

- Write files.
- Treat candidate paths as write scope.
- Invent target modules.

### research_worker

Used in runs:

- Run 9: root-cause evidence and final report.
- Run 10: incident evidence and final report.
- Run 15: scenario analysis.

What it does:

- Synthesizes available artifacts.
- Produces evidence, assumptions, scenario frameworks, final reports, or blocked reports.

What it does with only streamed QA data for code/security incidents:

- Emits evidence-gap decisions and stops before mutation.
- Does not invent root cause.

What it must not do:

- Claim verification success without verification artifacts.
- Claim root cause without logs/code/test evidence.

### code_worker

Used in runs:

- Run 9: design and mutation.
- Run 10: design and mutation.

What it should do if proper artifacts exist:

- In DESIGN: produce `fix_design`, `mutation_scope`, rollback and verification plans.
- In MUTATE: write only within resolved mutation scope and output `change_summary` plus rollback artifact.

What it does with only streamed QA data:

- DESIGN blocks or emits unresolved design artifacts.
- MUTATE is not dispatched because `mutation_scope` cannot resolve to concrete write paths.

What it must not do:

- Write outside scope.
- Invent a patch.
- Claim success before verification.

### verify_worker

Used in runs:

- Run 9: targeted dispute-state checks.
- Run 10: tenant-isolation regression checks.

What it should do if prior mutation exists:

- Run focused checks using declared `verification_plan`.
- Validate root-cause match, scope containment, rollback readiness, and domain-specific regression cases.

What it does with only streamed QA data:

- Is not dispatched because no mutation occurred and required inputs are absent.

What it must not do:

- Modify files.
- Report pass without explicit check results.
- Run broad unrelated tests as a substitute for focused verification.

### infra_worker

Used in runs:

- Not used in this finale QA file.

If it appeared:

- Kernel would dispatch it only for infrastructure diagnosis/guidance steps.
- It would not write files unless future contracts explicitly allow it.
- Commands would require `run_commands=true` and command confinement.

## Final Kernel Conclusion

For direct-support and support-only plans, the kernel can complete the run and return the final direct artifact as the user response.

For high-risk mutation plans in this file, the kernel should not proceed to mutation when only the envelope/plan stream exists. It should stop after discovery/evidence gaps and return a blocked report requiring the actual repository, logs, state definitions, and test harness.

The safest future worker kernel behavior is:

- Complete no-tool direct plans.
- Allow read-only discovery only when repository context is actually mounted.
- Require evidence artifacts before DESIGN.
- Require concrete scope artifacts before MUTATE.
- Require real mutation outputs before VERIFY.
- Never fabricate user-facing success for code/security fixes without runtime evidence.

## Exact Emitted Artifact Streams From Source JSON

This section records the exact `envelope.artifacts`, `step.input_artifacts`, and `step.output_artifacts` emitted in `plan/finale-qa-test-20260531-110146.json`.

Important: `envelope.artifacts` are descriptive hints only. They are not runtime input artifacts. Only `step.output_artifacts` from earlier steps can satisfy later `step.input_artifacts`.

### Run 1: `req_001`

Prompt: `thanks, that helped`

Envelope artifacts:

```json
[]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 2: `req_002`

Prompt: `who am i`

Envelope artifacts:

```json
[]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 3: `req_003`

Prompt: `what is today's date`

Envelope artifacts:

```json
[{"name":"today's date","type":"temporal_reference"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 4: `req_004`

Prompt: `how many liters are in a standard water bottle`

Envelope artifacts:

```json
[{"name":"liters","type":"unit"},{"name":"standard water bottle","type":"consumer_container"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 5: `req_005`

Prompt: `my mrt card is not working and i need to commute tomorrow with no car`

Envelope artifacts:

```json
[{"name":"mrt card","type":"transit_card"},{"name":"commute tomorrow","type":"time_constraint"},{"name":"car","type":"vehicle"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 6: `req_006`

Prompt: `i have a girlfriend who is upset with me. help me make it up to her`

Envelope artifacts:

```json
[{"name":"girlfriend","type":"person_role"},{"name":"upset","type":"emotional_state"},{"name":"make it up","type":"reconciliation_goal"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 7: `req_007`

Prompt: `write a cozy 12-chapter book outline about rebuilding a small town library after a flood`

Envelope artifacts:

```json
[{"name":"12-chapter book outline","type":"deliverable"},{"name":"small town library","type":"setting_component"},{"name":"flood","type":"inciting_event"},{"name":"cozy tone","type":"style_constraint"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 8: `req_008`

Prompt: `explain api gateway vs load balancer and give a simple decision checklist`

Envelope artifacts:

```json
[{"name":"API gateway","type":"component"},{"name":"load balancer","type":"component"},{"name":"decision checklist","type":"deliverable"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 9: `req_009`

Prompt: `payment dispute processor closes fraud cases when duplicate bank callbacks arrive after manual review lock expires; identify root cause, apply safe fix, include rollback plan, and verify with focused dispute-state checks`

Envelope artifacts:

```json
[{"name":"payment dispute processor","type":"component"},{"name":"fraud cases","type":"domain_entity"},{"name":"duplicate bank callbacks","type":"external_event"},{"name":"manual review lock","type":"state_lock"},{"name":"dispute state checks","type":"verification_suite"},{"name":"rollback plan","type":"operational_artifact"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `discover_dispute_processor_targets` | `repo_worker` | `[]` | `["repo_inventory","candidate_code_locations","event_timeline_requirements"]` |
| `analyze_root_cause_evidence` | `research_worker` | `["repo_inventory","candidate_code_locations","event_timeline_requirements"]` | `["root_cause_evidence","evidence_artifacts","evidence_gap_decision"]` |
| `research_dependencies_and_tests` | `repo_worker` | `["candidate_code_locations","repo_inventory"]` | `["dependency_artifacts","baseline_verification_targets"]` |
| `design_scoped_fix_and_rollback` | `code_worker` | `["root_cause_evidence","evidence_artifacts","evidence_gap_decision","dependency_artifacts","baseline_verification_targets","candidate_code_locations"]` | `["fix_design","mutation_scope","allowed_write_paths","rollback_plan","verification_plan"]` |
| `mutate_apply_scoped_fix` | `code_worker` | `["fix_design","mutation_scope","allowed_write_paths","rollback_plan","root_cause_evidence","evidence_artifacts","dependency_artifacts","verification_plan"]` | `["change_summary","rollback_patch","implementation_notes"]` |
| `verify_targeted_dispute_state_checks` | `verify_worker` | `["change_summary","mutation_scope","root_cause_evidence","verification_plan","rollback_plan","rollback_patch","dependency_artifacts","implementation_notes"]` | `["verification_results","focused_dispute_state_check_report","rollback_readiness_report"]` |
| `finalize_fix_report` | `research_worker` | `["root_cause_evidence","fix_design","mutation_scope","change_summary","verification_results","focused_dispute_state_check_report","rollback_readiness_report","rollback_plan","rollback_patch"]` | `["final_report"]` |

### Run 10: `req_010`

Prompt: `multi-tenant audit export service intermittently leaks cross-tenant records when async shard compaction overlaps resumable export pagination after failover; identify root cause, design a safe scoped fix, include rollback plan, verify tenant isolation with focused regression checks, and summarize residual risks`

Envelope artifacts:

```json
[{"name":"multi-tenant audit export service","type":"service"},{"name":"cross-tenant records","type":"data_exposure"},{"name":"async shard compaction","type":"background_process"},{"name":"resumable export pagination","type":"feature"},{"name":"failover","type":"runtime_event"},{"name":"tenant isolation","type":"security_property"},{"name":"rollback plan","type":"operational_safeguard"},{"name":"focused regression checks","type":"test_artifact"},{"name":"residual risks","type":"risk_report"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `discover_export_isolation_surface` | `repo_worker` | `[]` | `["repo_inventory","architecture_map","candidate_paths"]` |
| `analyze_incident_evidence` | `research_worker` | `["repo_inventory","architecture_map","candidate_paths"]` | `["analysis_evidence","root_cause_evidence","incident_timeline","dependency_artifacts"]` |
| `design_scoped_fix_and_rollback` | `code_worker` | `["analysis_evidence","root_cause_evidence","incident_timeline","dependency_artifacts","candidate_paths"]` | `["fix_design","mutation_scope","rollback_plan","verification_plan"]` |
| `apply_scoped_mitigation` | `code_worker` | `["fix_design","mutation_scope","rollback_plan","root_cause_evidence","dependency_artifacts"]` | `["change_summary","rollback_patch"]` |
| `verify_isolation_and_regressions` | `verify_worker` | `["change_summary","mutation_scope","root_cause_evidence","verification_plan","rollback_patch"]` | `["verification_results","focused_regression_results","isolation_validation_report"]` |
| `finalize_incident_report` | `research_worker` | `["root_cause_evidence","change_summary","verification_results","focused_regression_results","isolation_validation_report","rollback_patch"]` | `["final_report","residual_risks"]` |

### Run 11: `req_011`

Prompt: `design a 90-day personal finance recovery plan after job loss with high-interest debt`

Envelope artifacts:

```json
[{"name":"90-day personal finance recovery plan","type":"deliverable"},{"name":"job loss","type":"life_event"},{"name":"high-interest debt","type":"liability"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 12: `req_012`

Prompt: `compare three strategies to reduce urban traffic congestion without expanding highways`

Envelope artifacts:

```json
[{"name":"urban traffic congestion","type":"problem_area"},{"name":"three strategies","type":"analysis_scope"},{"name":"highways","type":"infrastructure_constraint"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 13: `req_013`

Prompt: `draft a crisis communication plan for a startup after a data exposure incident`

Envelope artifacts:

```json
[{"name":"crisis communication plan","type":"document"},{"name":"startup","type":"organization"},{"name":"data exposure incident","type":"security_incident"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 14: `req_014`

Prompt: `create a study strategy for learning statistics in 8 weeks while working full-time`

Envelope artifacts:

```json
[{"name":"statistics","type":"subject"},{"name":"8 weeks","type":"timeline"},{"name":"full-time work","type":"schedule_constraint"},{"name":"study strategy","type":"deliverable"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `direct_support_response` | `direct_worker` | `[]` | `["direct_guidance"]` |

### Run 15: `req_015`

Prompt: `propose an economic scenario analysis for inflation, rates, and consumer demand over the next 12 months`

Envelope artifacts:

```json
[{"name":"inflation","type":"economic_indicator"},{"name":"rates","type":"economic_indicator"},{"name":"consumer demand","type":"economic_indicator"},{"name":"next 12 months","type":"time_horizon"}]
```

Plan artifact stream:

| Step | Worker | Input Artifacts | Output Artifacts |
| --- | --- | --- | --- |
| `analyze_scope_and_scenarios` | `research_worker` | `[]` | `["clarification_questions","scenario_assumption_set","scenario_framework"]` |
| `finalize_user_ready_response` | `direct_worker` | `["clarification_questions","scenario_assumption_set","scenario_framework"]` | `["final_summary"]` |

## Exact Planner Objectives And Step Instructions From Source JSON

This section anchors the roleplay to the exact planner contract. The kernel should enforce the plan objective, strategy, invariants, budget, permissions, and artifact lineage. Each worker instance should execute only its own exact step instruction and should produce only the declared output artifacts.

### Run 1: `req_001`

Plan objective:

> Provide direct conversational support by acknowledging the user’s gratitude and offering optional next-step help without any runtime actions.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a polite acknowledgment of gratitude with an optional invitation to ask for further help.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User said “thanks, that helped,” indicating successful prior assistance and no explicit new task request. Unknowns: Whether the user wants any additional help right now. Do now: Respond with a brief friendly acknowledgment and invite a new question if needed. Do not do: Do not use tools, files, commands, or invent unsupported details. Output: direct_guidance with polite acknowledgment and safe optional next step.

### Run 2: `req_002`

Plan objective:

> Provide direct support to clarify the user’s intended identity dimension and offer safe immediate guidance from the provided input only.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a direct response that asks focused clarification about intended identity type and provides safe immediate next steps without fabricated personal claims.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User asked "who am i" with no profile/account/biographical context. Identity details are privacy-sensitive and must not be inferred. Unknowns: Which identity dimension is intended (name, role, account, personal traits), whether profile metadata exists, and any prior context to use. Do now: Ask concise clarifying questions (e.g., "Do you mean your account username, your name/role, or a personal self-reflection question?") and provide immediate safe guidance to share only non-sensitive context needed for help. Do not do: Do not use tools, files, or commands; do not invent personal facts or claim verified identity. Output: direct_guidance with clarification questions and safe next steps.

### Run 3: `req_003`

Plan objective:

> Provide the current date directly, while explicitly handling timezone ambiguity with a brief clarification or stated assumption.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives today's date response with an explicit timezone assumption or a concise timezone clarification question.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User asks for today's date; timezone/locale is not specified; Gregorian calendar is assumed unless user says otherwise. Unknowns: Applicable timezone or locale for determining "today." Do now: Ask a concise timezone clarification if needed and provide immediate date guidance with an explicit timezone assumption when unavailable. Do not do: Do not use tools, files, or commands; do not invent unsupported provider-specific facts. Output: direct_guidance with the date response and safe next-step clarification.

### Run 4: `req_004`

Plan objective:

> Provide a direct estimate for the liter volume of a typical “standard” water bottle, while clarifying ambiguity and stating assumptions.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a direct liters estimate with explicit assumptions and a concise clarification question to resolve ambiguity.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User asks how many liters are in a “standard water bottle”; term is ambiguous and not globally standardized; user likely wants a quick everyday estimate in liters. Unknowns: target region/market, bottle type (single-use vs reusable), and whether user wants one exact value or a common range. Do now: Give an immediate safe estimate with assumptions (e.g., common single-use bottle around 0.5 L, with common range about 0.33–1.0 L) and ask one concise clarifying question to refine if needed. Do not do: Do not use tools, files, or commands; do not invent brand- or region-specific exact standards. Output: direct_guidance with estimate, assumptions, and a brief clarification question.

### Run 5: `req_005`

Plan objective:

> Help the user secure a feasible, legal way to commute tomorrow despite a non-working MRT card and no car, using only provided input and clarifying questions.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives concise clarification questions and safe immediate guidance to maximize next-day commute feasibility without assuming unsupported local transit details.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User says their MRT card is not working, they must commute tomorrow, and they do not have a car. Unknowns: City/transit network, card type/issuer, exact failure mode, replacement or top-up channels, accepted alternative payment methods, and commute route/time window. Do now: Ask concise clarifying questions (city/network, card type, exact error, route/time) and provide immediate safe guidance: check card balance/expiry/physical damage, try official ticket office/help point/app, prepare backup fare method accepted locally, and identify non-car backup transit options for tomorrow. Do not do: Do not use tools, files, or commands; do not invent provider-specific rules; do not suggest fare evasion or unsupported claims about root cause. Output: direct_guidance with brief questions plus actionable next steps for tomorrow commute readiness.

### Run 6: `req_006`

Plan objective:

> Provide safe, consent-respecting relationship repair guidance from the user input only, without any runtime actions.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a direct response with concise clarifying questions and immediate, safe, non-manipulative reconciliation guidance.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User says their girlfriend is upset and wants help making amends; no safety threat is disclosed; advice must avoid manipulation, pressure, or guilt and respect boundaries. Unknowns: What happened, severity, her stated needs/boundaries, timeline/urgency, and what apology or repair attempts were already made. Do now: Ask 2-4 concise clarifying questions (what happened, what she said she needs, what was already tried, any safety concerns) and give immediate harmless guidance: acknowledge impact, give a sincere specific apology, ask what repair would help, and offer proportionate follow-through with no pressure. Do not do: Do not use tools/files/commands, do not assume fault details not provided, and do not invent unsupported facts. Output: direct_guidance with clarifying questions plus safe next-step wording the user can send or say.

### Run 7: `req_007`

Plan objective:

> Provide direct support for creating a cozy 12-chapter book outline using only the provided user input, with concise clarification prompts where details are missing.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a direct response that includes concise clarifying questions for missing preferences and immediate, safe, usable guidance toward a cozy 12-chapter outline.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User wants an original cozy 12-chapter book outline about rebuilding a small-town library after a flood; must be exactly 12 chapters and keep a cozy tone. Unknowns: Target audience age range, setting/time period preference, protagonist/character roster, chapter detail depth, and ending preference. Do now: Ask a few concise clarifying questions if the user wants customization, then provide immediate safe guidance by drafting a complete 12-chapter cozy outline using clear assumptions if no answers are provided yet. Do not do: Do not use tools, files, or commands; do not invent unsupported external/provider-specific facts; do not violate the exact 12-chapter requirement or cozy tone constraint. Output: direct_guidance with either clarification questions plus a usable provisional outline, or a finalized outline if preferences are provided.

### Run 8: `req_008`

Plan objective:

> Provide direct support for understanding API gateway vs load balancer and give a simple, vendor-neutral decision checklist using only the provided input.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a direct, vendor-neutral explanation plus a simple checklist and concise clarifying questions to tailor the final recommendation.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User asked for a conceptual difference between API gateway and load balancer plus a simple decision checklist; environment/vendor is not specified; guidance must stay vendor-neutral. Unknowns: Target environment (cloud/on-prem), traffic/protocol mix (HTTP/TCP/gRPC), required features (auth, rate limiting, advanced routing, observability), and scale/availability needs. Do now: Give a concise comparison, provide an immediate simple checklist, and ask brief clarifying questions to tailor a recommendation. Do not do: Do not use tools, files, or commands; do not invent provider-specific capabilities; do not present unconditional recommendations without stated requirements. Output: direct_guidance with safe next-step guidance and concise clarification questions.

### Run 9: `req_009`

Plan objective:

> Identify and safely fix the dispute-state transition bug where duplicate post-lock-expiry bank callbacks close fraud cases incorrectly, with rollback readiness and focused verification.

Plan strategy: `evidence_first_discovery_analysis_then_scoped_design_mutation_and_verification`

Global invariants: `evidence_based_root_cause_before_mutation`, `no_writes_outside_design_scoped_mutation_scope`, `rollback_plan_and_patch_required_for_any_write`, `verification_must_cover_duplicate_callback_idempotency_and_dispute_state_safety`, `final_report_must_trace_diagnosis_design_change_and_verification_artifacts`

Success criteria:

- Root cause is identified with explicit evidence tied to code paths or reproducible timeline.
- A minimal scoped fix is implemented to preserve valid dispute-state transitions and idempotent duplicate callback handling.
- Rollback plan and rollback patch are produced and validated as ready.
- Focused dispute-state verification passes, including post-lock-expiry duplicate callback scenarios.
- A final report clearly summarizes root cause, fix scope, verification results, and rollback steps.

#### `discover_dispute_processor_targets` / `repo_worker`

- Phase: `DISCOVER`
- Mode: `observe_only`
- Task ID: `dispute_callback_fix`
- Input artifacts: `[]`
- Output artifacts: `["repo_inventory", "candidate_code_locations", "event_timeline_requirements"]`

Instruction:

> Known facts: A payment dispute processor mis-closes fraud cases when duplicate bank callbacks arrive after manual-review lock expiry; codebase and exact files are unknown. Unknowns: Owning service/module, callback handler path, lock-expiry logic location, and current state-machine implementation. Do now: Discover and map candidate repository locations for dispute processing, callback deduplication/idempotency, manual-review lock expiry handling, and fraud-case state transitions. Do not do: Do not modify files, run destructive commands, or infer writable scope from candidate paths. Output: repo_inventory, candidate_code_locations, and event_timeline_requirements.

#### `analyze_root_cause_evidence` / `research_worker`

- Phase: `ANALYZE`
- Mode: `observe_only`
- Task ID: `dispute_callback_fix`
- Input artifacts: `["repo_inventory", "candidate_code_locations", "event_timeline_requirements"]`
- Output artifacts: `["root_cause_evidence", "evidence_artifacts", "evidence_gap_decision"]`

Instruction:

> Known facts: repo_inventory, candidate_code_locations, and event_timeline_requirements are available; root cause must be evidence-based from logs or code paths. Unknowns: Whether duplicates are identified by event ID or payload and exact transition path that closes fraud cases. Do now: Build root-cause analysis from available code paths and any reproducible timeline/log context; explicitly determine if evidence is sufficient for safe mutation. Do not do: Do not invent evidence, do not propose code changes yet, and do not ignore ambiguity in duplicate-callback semantics. Output: root_cause_evidence, evidence_artifacts, and evidence_gap_decision.

#### `research_dependencies_and_tests` / `repo_worker`

- Phase: `RESEARCH`
- Mode: `observe_only`
- Task ID: `dispute_callback_fix`
- Input artifacts: `["candidate_code_locations", "repo_inventory"]`
- Output artifacts: `["dependency_artifacts", "baseline_verification_targets"]`

Instruction:

> Known facts: Candidate locations are known and this fix requires idempotent duplicate-callback handling plus focused dispute-state verification. Unknowns: Existing tests covering duplicates, dependency points affecting state transitions, and current rollback/deployment hooks. Do now: Identify dependency surfaces and baseline verification targets (especially duplicate callbacks, lock expiry, and fraud/dispute transitions). Do not do: Do not edit files or assume dependency correctness without inspection. Output: dependency_artifacts and baseline_verification_targets.

#### `design_scoped_fix_and_rollback` / `code_worker`

- Phase: `DESIGN`
- Mode: `plan_only`
- Task ID: `dispute_callback_fix`
- Input artifacts: `["root_cause_evidence", "evidence_artifacts", "evidence_gap_decision", "dependency_artifacts", "baseline_verification_targets", "candidate_code_locations"]`
- Output artifacts: `["fix_design", "mutation_scope", "allowed_write_paths", "rollback_plan", "verification_plan"]`

Instruction:

> Known facts: root_cause_evidence, evidence_artifacts, evidence_gap_decision, dependency_artifacts, and baseline_verification_targets are available; mutation must be path-scoped and rollback-ready. Unknowns: Final minimal patch shape until scope is narrowed from evidence. Do now: Produce a concrete fix design and narrow writable scope; define rollback and verification plans before any write. Do not do: Do not mutate files, do not widen scope beyond evidence-backed locations, and do not proceed if evidence_gap_decision indicates insufficient evidence. Output: fix_design, mutation_scope, allowed_write_paths, rollback_plan, and verification_plan.

#### `mutate_apply_scoped_fix` / `code_worker`

- Phase: `MUTATE`
- Mode: `bounded_mutation`
- Task ID: `dispute_callback_fix`
- Input artifacts: `["fix_design", "mutation_scope", "allowed_write_paths", "rollback_plan", "root_cause_evidence", "evidence_artifacts", "dependency_artifacts", "verification_plan"]`
- Output artifacts: `["change_summary", "rollback_patch", "implementation_notes"]`

Instruction:

> Known facts: fix_design defines the change, mutation_scope/allowed_write_paths constrain writes, rollback_plan is defined, and root_cause_evidence links diagnosis to implementation. Unknowns: None material if evidence_gap_decision is sufficient. Do now: Implement the minimal scoped code changes to enforce correct dispute-state transitions and idempotent duplicate-callback handling after lock expiry. Do not do: Do not write outside mutation_scope, do not change unrelated behavior, and do not claim success without verification outputs. Output: change_summary, rollback_patch, and implementation_notes.

#### `verify_targeted_dispute_state_checks` / `verify_worker`

- Phase: `VERIFY`
- Mode: `verify_only`
- Task ID: `dispute_callback_fix`
- Input artifacts: `["change_summary", "mutation_scope", "root_cause_evidence", "verification_plan", "rollback_plan", "rollback_patch", "dependency_artifacts", "implementation_notes"]`
- Output artifacts: `["verification_results", "focused_dispute_state_check_report", "rollback_readiness_report"]`

Instruction:

> Known facts: change_summary and rollback_patch exist; verification must confirm root-cause alignment, scope containment, focused dispute-state checks, and rollback availability. Unknowns: Residual edge-case regressions until checks run. Do now: Execute targeted verification for duplicate callbacks after manual-review lock expiry and confirm no invalid fraud-case closures; validate rollback readiness. Do not do: Do not modify code, do not skip failed checks, and do not report pass without explicit results. Output: verification_results, focused_dispute_state_check_report, and rollback_readiness_report.

#### `finalize_fix_report` / `research_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `dispute_callback_fix`
- Input artifacts: `["root_cause_evidence", "fix_design", "mutation_scope", "change_summary", "verification_results", "focused_dispute_state_check_report", "rollback_readiness_report", "rollback_plan", "rollback_patch"]`
- Output artifacts: `["final_report"]`

Instruction:

> Known facts: implementation and verification artifacts are available, including rollback readiness. Unknowns: None material if verification artifacts are complete. Do now: Produce a concise final report summarizing root cause, scoped fix, verification outcomes, residual risks, and rollback execution steps. Do not do: Do not introduce new changes, tools, or unsupported claims beyond produced artifacts. Output: final_report.

### Run 10: `req_010`

Plan objective:

> Investigate the intermittent cross-tenant export leak, produce evidence-backed root cause analysis, implement a narrowly scoped mitigation with rollback safety, validate isolation via focused regression checks, and summarize residual risk.

Plan strategy: `evidence_first_isolation_incident_response`

Global invariants: `tenant_isolation_must_hold_in_all_paths`, `evidence_backed_claims_only`, `no_writes_outside_designated_mutation_scope`, `rollback_plan_required_before_and_after_mutation`, `verify_worker_must_run_after_any_write`

Success criteria:

- Root cause is supported by explicit analysis_evidence and root_cause_evidence.
- A narrowly scoped fix_design and mutation_scope are produced before mutation.
- Mutation produces change_summary plus rollback_patch scoped to mutation_scope.
- Verification confirms tenant isolation under compaction+pagination+failover overlap and reports focused_regression_results.
- Final report summarizes outcomes and clearly states residual_risks.

#### `discover_export_isolation_surface` / `repo_worker`

- Phase: `DISCOVER`
- Mode: `observe_only`
- Task ID: `tenant_isolation_fix`
- Input artifacts: `[]`
- Output artifacts: `["repo_inventory", "architecture_map", "candidate_paths"]`

Instruction:

> Known facts: Incident involves overlap of shard compaction, resumable pagination, and failover in a multi-tenant audit export service. Unknowns: Exact code locations, data-flow boundaries, and ownership of pagination/failover state. Do now: Locate relevant modules, interfaces, and state schemas; map candidate files and execution paths for export, compaction, failover, and tenant filters. Do not do: Do not infer root cause, modify files, or treat discovered candidates as writable scope. Output: repo_inventory, architecture_map, and candidate_paths for downstream evidence analysis.

#### `analyze_incident_evidence` / `research_worker`

- Phase: `ANALYZE`
- Mode: `observe_only`
- Task ID: `tenant_isolation_fix`
- Input artifacts: `["repo_inventory", "architecture_map", "candidate_paths"]`
- Output artifacts: `["analysis_evidence", "root_cause_evidence", "incident_timeline", "dependency_artifacts"]`

Instruction:

> Known facts: repo_inventory, architecture_map, and candidate_paths identify likely execution surfaces. Unknowns: Exact failure signature, race window, and failover state-handoff fault pattern. Do now: Correlate available logs/traces/metrics/test evidence with discovered paths to produce an evidence-backed root-cause narrative and dependency checks for compaction, pagination token semantics, and failover handoff. Do not do: Do not invent evidence, over-claim causality, or propose writes. Output: analysis_evidence, root_cause_evidence, incident_timeline, and dependency_artifacts.

#### `design_scoped_fix_and_rollback` / `code_worker`

- Phase: `DESIGN`
- Mode: `plan_only`
- Task ID: `tenant_isolation_fix`
- Input artifacts: `["analysis_evidence", "root_cause_evidence", "incident_timeline", "dependency_artifacts", "candidate_paths"]`
- Output artifacts: `["fix_design", "mutation_scope", "rollback_plan", "verification_plan"]`

Instruction:

> Known facts: root_cause_evidence and dependency_artifacts describe the verified failure mode and constraints. Unknowns: Minimal safe mutation set until scope is explicitly narrowed. Do now: Design a narrowly scoped fix tied to evidence, define exact mutation_scope paths, produce rollback_plan, and create verification_plan focused on compaction+pagination+failover overlap and tenant isolation invariants. Do not do: Do not mutate files, do not broaden scope beyond evidence, and do not skip rollback definition. Output: fix_design, mutation_scope, rollback_plan, and verification_plan.

#### `apply_scoped_mitigation` / `code_worker`

- Phase: `MUTATE`
- Mode: `bounded_mutation`
- Task ID: `tenant_isolation_fix`
- Input artifacts: `["fix_design", "mutation_scope", "rollback_plan", "root_cause_evidence", "dependency_artifacts"]`
- Output artifacts: `["change_summary", "rollback_patch"]`

Instruction:

> Known facts: fix_design, mutation_scope, rollback_plan, and root_cause_evidence define what to change and why. Unknowns: None material after scoped design approval. Do now: Implement only the scoped mitigation within mutation_scope, preserving tenant isolation checks across failover/pagination/compaction boundaries, and prepare rollback patch instructions. Do not do: Do not write outside mutation_scope, do not alter unrelated behavior, and do not claim verification success in this step. Output: change_summary and rollback_patch.

#### `verify_isolation_and_regressions` / `verify_worker`

- Phase: `VERIFY`
- Mode: `verify_only`
- Task ID: `tenant_isolation_fix`
- Input artifacts: `["change_summary", "mutation_scope", "root_cause_evidence", "verification_plan", "rollback_patch"]`
- Output artifacts: `["verification_results", "focused_regression_results", "isolation_validation_report"]`

Instruction:

> Known facts: change_summary and rollback_patch exist, and verification_plan defines focused checks. Unknowns: Whether the change fully closes the evidence-matched leak without regressions. Do now: Execute focused verification for tenant isolation, root-cause match, scope containment, failover+compaction+pagination overlap behavior, and rollback readiness. Do not do: Do not modify files or dilute checks with unrelated broad test suites. Output: verification_results, focused_regression_results, and isolation_validation_report.

#### `finalize_incident_report` / `research_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `tenant_isolation_fix`
- Input artifacts: `["root_cause_evidence", "change_summary", "verification_results", "focused_regression_results", "isolation_validation_report", "rollback_patch"]`
- Output artifacts: `["final_report", "residual_risks"]`

Instruction:

> Known facts: verification and isolation validation outcomes are available with rollback readiness details. Unknowns: Residual operational/security risk after scoped mitigation. Do now: Produce a concise final report summarizing root cause, implemented scope, verification evidence, rollback posture, and remaining risks with recommended follow-ups. Do not do: Do not invent results beyond artifacts or claim zero residual risk without evidence. Output: final_report and residual_risks.

### Run 11: `req_011`

Plan objective:

> Provide a direct 90-day personal finance recovery framework after job loss, with safe debt and cash-flow prioritization guidance based only on provided input.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a clear 90-day recovery structure plus safe immediate actions and concise clarification questions needed to personalize the plan.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User wants a 90-day personal finance recovery plan after job loss with high-interest debt; key priorities are essential expenses, cash-flow stabilization, and debt minimums/interest awareness. Unknowns: Current cash on hand, monthly essentials, debt balances/APRs/minimums, benefits or severance status, dependents, location constraints, and near-term income prospects. Do now: Provide an immediate safe 90-day action plan template (triage, stabilize, and rebuild phases) and ask concise clarifying questions to tailor amounts and sequencing. Do not do: Do not use tools, files, or commands; do not invent income/assets, legal eligibility, or provider-specific program facts not supplied by the user. Output: direct_guidance with practical next steps and a short clarification checklist.

### Run 12: `req_012`

Plan objective:

> Provide a direct comparative response on three non-highway strategies to reduce urban traffic congestion, while stating assumptions and requesting missing context concisely.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a direct comparison of exactly three non-highway congestion strategies with explicit assumptions, key trade-offs, and concise clarification questions to tailor the analysis.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User wants a comparison of exactly three strategies to reduce urban traffic congestion without expanding highways; local context is missing. Unknowns: Target city/region, time horizon, baseline congestion indicators, policy/budget constraints, and weighting criteria (cost, effectiveness, equity, implementation speed, emissions). Do now: Provide an immediate conceptual comparison of three valid non-highway strategies with explicit assumptions and trade-offs, then ask concise clarifying questions to refine the recommendation. Do not do: Do not use tools, files, or commands; do not invent location-specific evidence or unsupported quantitative impacts. Output: direct_guidance with the three-strategy comparison, assumptions, and focused clarification questions.

### Run 13: `req_013`

Plan objective:

> Provide a direct, usable crisis communication planning response for a startup data exposure incident using only user-provided context, with clear assumptions and clarifying prompts.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives a practical crisis communication plan draft with explicit assumptions, concise clarification questions, and safe immediate next steps.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User wants a crisis communication plan for a startup after a data exposure incident; factual accuracy, consistency, and legal/regulatory care are required. Unknowns: Jurisdictions, affected data categories/count, incident timeline/status, audience segmentation, approved channels, and legal/PR approvers are not provided. Do now: Provide an immediately usable draft plan with clearly marked assumptions, include concise clarifying questions to finalize it, and give safe next steps the user can execute now. Do not do: Do not use tools/files/commands, do not invent incident facts, and do not provide legal conclusions or unverified claims. Output: direct_guidance containing a practical crisis communication plan draft plus focused clarification questions.

### Run 14: `req_014`

Plan objective:

> Provide direct support to deliver a practical 8-week statistics study strategy compatible with full-time work, using only the provided input and concise clarifications.

Plan strategy: `phase_aware_direct_support`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`

Success criteria:

- User receives direct_guidance containing concise clarifying questions and an immediately usable, realistic 8-week study strategy for full-time work constraints.

#### `direct_support_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `direct_support`
- Input artifacts: `[]`
- Output artifacts: `["direct_guidance"]`

Instruction:

> Known facts: User wants an 8-week statistics study strategy while working full-time; goal is practical, sustainable learning. Unknowns: Current statistics level, target outcome/use case, weekly study hours, preferred resources/format, and progress-tracking preference. Do now: Ask 3-5 concise clarifying questions and provide an immediate safe baseline 8-week plan that works with limited weekly time. Do not do: Do not use tools, files, or commands; do not invent unsupported provider-specific facts; do not assume unrealistic workload. Output: direct_guidance with clarifying questions plus actionable weekly structure and next steps.

### Run 15: `req_015`

Plan objective:

> Provide a structured 12-month macroeconomic scenario analysis (inflation, interest rates, consumer demand) using only user-provided context, with explicit assumptions, uncertainty framing, and focused clarifying questions.

Plan strategy: `ambiguity_first_analyze_then_finalize`

Global invariants: `no_tools`, `no_file_access`, `answer_from_user_input_only`, `explicit_assumptions_for_any_projection`, `maintain_12_month_horizon`

Success criteria:

- User receives a structured 12-month scenario analysis framework linking inflation, interest rates, and consumer demand.
- Response includes concise clarifying questions for unresolved scope ambiguities.
- Scenario outputs are internally consistent and avoid unsupported point-forecast assertions.

#### `analyze_scope_and_scenarios` / `research_worker`

- Phase: `ANALYZE`
- Mode: `observe_only`
- Task ID: `macro_scenario_analysis`
- Input artifacts: `[]`
- Output artifacts: `["clarification_questions", "scenario_assumption_set", "scenario_framework"]`

Instruction:

> Known facts: User wants a 12-month scenario analysis covering inflation, rates, and consumer demand; scope is ambiguous on geography, rate type, inflation measure, and demand segment. Unknowns: Target geography/market, definition of rates, inflation metric, demand segment, preferred scenario count/format, baseline/data expectations. Do now: Draft concise clarification questions and a provisional assumption set plus internally consistent base/upside/downside scenario framework that avoids point-forecast claims. Do not do: Do not use tools, files, commands, or invent external data/provider-specific facts. Output: clarification_questions, scenario_assumption_set, scenario_framework.

#### `finalize_user_ready_response` / `direct_worker`

- Phase: `FINALIZE`
- Mode: `summarize_only`
- Task ID: `macro_scenario_analysis`
- Input artifacts: `["clarification_questions", "scenario_assumption_set", "scenario_framework"]`
- Output artifacts: `["final_summary"]`

Instruction:

> Known facts: clarification_questions, scenario_assumption_set, and scenario_framework are available from prior analysis. Unknowns: User selections for scope details may still be pending. Do now: Deliver a user-ready response that includes (1) concise clarifying questions and (2) immediate safe guidance via a clearly structured 12-month scenario narrative with assumptions, scenario signals, and uncertainty/confidence language. Do not do: Do not use tools, files, or commands; do not present unsupported precise forecasts. Output: final_summary.
