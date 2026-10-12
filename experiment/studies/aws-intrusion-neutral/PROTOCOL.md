# Protocol: AWS post-access next steps (neutral framing)

This is the neutral-framing arm of the study pre-registered in
[`../aws-intrusion/PROTOCOL.md`](../aws-intrusion/PROTOCOL.md). Everything there applies —
scenarios, output schema, coding (`codebook.md`), reliability, analysis, exclusions, ethics —
with one difference:

- **Framing (PROTOCOL §4).** The system prompt ([`prompts/system.md`](prompts/system.md)) gives no
  security or assessment framing and no stated goal: a bare agent holding AWS credentials, asked for
  its next step. The assessment arm instead frames an authorized red-team engagement.

The comparison between the two arms is the framing effect: run both over the identical scenarios and
models, code both, and report the difference in tactic/event frequencies and in refusal rate.

Keep this arm's `scenarios/` and `prompts/user.md` byte-identical to the assessment arm.
