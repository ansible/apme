# Review Findings Intent

## Status: intent

This document records why the Review findings step exists and what that step must convey before remediation begins. It specifies no layout, no components, and no copy.

The live workflow is Scan, then Review findings, then the remediation steps derived from session state. With AI, those steps are Rule-based fix proposals, Rule-based fix applied, AI escalation, AI proposals, and AI applied, followed by Create branch and Commit. Without AI, the first remediation step is Apply findings, followed by Create branch and Commit. See [ADR-064](../../.sdlc/adrs/ADR-064-assess-pause-session-continue.md).

The engine's three-way split of findings is described in [DESIGN_REMEDIATION.md](DESIGN_REMEDIATION.md) and [ADR-023](../../.sdlc/adrs/ADR-023-per-finding-classification.md). This document is the workflow briefing that split must become, in the words of the steps the user is about to enter.

## Purpose of the step

Review findings shows the whole scan once, summarized by the kind of remediation each finding will receive, with counts. The buckets use the same words as the steps ahead, so those names already refer to something the user has seen. The summary carries the order of the work: rule-based first, then AI for what that step leaves, with manual findings outside that work.

The step is that briefing. An inventory of every finding, grouped by location and filtered by severity or kind, can carry the same three counts and still leave the order and the step names unexplained.

A way to inspect one bucket is compatible with this purpose. It is how someone looks at findings that may not be listed again. Inspection is not the step.

## Why it cannot be deferred to a later page

Review findings is the last live step where the whole scan is together. The step indicator is a progress display. Next moves the session forward into remediation.

After that, each step shows a subset of the same scan. A subset appears only after the previous step has progressed. Those subsets converge on one end result. A finding may not be listed again. A later step cannot reassemble the scan the user is leaving, so it cannot explain the sequence or name what the sequence will leave untouched.

## Exit knowledge

Next starts only the first slice. Nothing has been changed yet. On leaving, the user already understands the rest:

1. The list they are leaving is the whole scan. Later steps will not put that full list back on screen.
2. The buckets are the plan, in the same words as the steps ahead. Rule-based is the slice that starts now. AI is a later slice and stays closed until the rule-based step has progressed.
3. Each later slice is smaller because it depends on the step before it. The same buckets stay visible, and a bucket's count declines when its step runs. Review findings sets those counts. Later steps realize them.
4. Manual findings are unfixable. No later step takes them, and that count does not decline, because the workflow will not fix them.
5. The buckets are one sequence. It converges on the slices the workflow can realize, plus the unfixable remainder shown here.

The click does the first of those things. The step has already done the other four.

## How the buckets scope the following steps

Three buckets account for every finding. They are one ordered plan.

**Rule-based.** This is the first slice. With AI in the run, it is the work behind Rule-based fix proposals and Rule-based fix applied. Without AI, it is the work behind Apply findings. The following slices stay closed until this step has progressed.

**AI.** This slice opens after the rule-based step. It is the work behind AI escalation, AI proposals, and AI applied. What it shows is the residue of the earlier step: the part of the scan that step did not already resolve. When AI is not part of the run, the workflow has no AI slice, and those findings are manual.

**Manual.** These findings are unfixable, and Review findings says so. No later step realizes this bucket. Create branch and Commit are not finding buckets. They follow the slices the workflow can realize.

The user is not choosing a pile to do first. Progress on the rule-based slice is what makes the AI slice available. Each follow-on step is a subset of the scan this step accounted for, and the subsets meet again at the end: the work the workflow realized, and the unfixable remainder.

## What later steps owe this page

Review findings publishes the starting counts. Later steps keep the same buckets on screen. A bucket's count declines when that step runs, so the user can see which slice was realized.

The full list does not return. The declining buckets are how the user recognizes the plan without remembering the counts from this step. The manual count stays at the number set here.

## What this page does not explain

A finding can leave the bucket it started in. A rule-based fix can fail and be handed to AI, and an AI fix can fail and become manual ([ADR-023](../../.sdlc/adrs/ADR-023-per-finding-classification.md)). That movement stays in the engine. Review findings does not teach it.

The counts are the starting plan. Later steps realize that plan by shrinking the matching bucket. They are not a trace of findings moving between buckets.

## Heuristics

These boundaries follow Jakob Nielsen's [10 usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) (Nielsen 1994). They are the reason the boundaries hold, not a checklist for laying out the step.

- **Visibility of system status.** The scan is done, nothing is changed yet, and Next starts the first slice. Later pages keep the same buckets visible as their counts decline.
- **Match between the system and the real world, and consistency.** Bucket language is the workflow's step names, shared with the step indicator. Remediation-class jargon stays in the engine.
- **Error prevention.** A later page must read as one slice of the scan from this step. The user must already know that no step will fix manual findings.
- **Recognition rather than recall.** The same buckets persist and decline, so the user does not have to remember the counts from Review findings. Individual findings may not return.
- **Aesthetic and minimalist design.** Reclassification, and a full finding browser, compete with the briefing. They stay out of what the step is for.
- **Help and documentation.** The explanation is on this step, at the moment before the sequence starts, and it is about the task ahead.
